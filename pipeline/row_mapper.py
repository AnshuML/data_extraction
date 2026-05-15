"""
Row Mapper — Layer 3.

Maps raw table rows to canonical schema rows using RapidFuzz.
Assigns a confidence score to each mapped value.
Low-confidence rows are flagged for LLM disambiguation.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from rapidfuzz import fuzz, process as rfprocess

import config
import schemas
from pipeline.table_detector import DetectedColumn, TableData
from utils.logger import get_logger

logger = get_logger("row_mapper")

_NUMBER_CLEAN_RE = re.compile(r"[^\d.\-]")   # keep digits, dot, minus


# ─────────────────────────────────────────────────────────────────────────────
# Number parsing
# ─────────────────────────────────────────────────────────────────────────────

def _parse_number(raw: str) -> Optional[float]:
    """
    Robustly parse a number from OCR output.
    Handles: "1,23,456", "1.23.456", "(12345)", "12345-", "NIL", "N.A."
    """
    if not raw:
        return None
    text = raw.strip().upper()
    if text in ("NIL", "N.A.", "N/A", "NA", "-", "–", "", "NULL", "NONE"):
        return 0.0
    # Remove brackets → negative
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    # Remove thousands separators and non-numeric chars (keep . and -)
    text = _NUMBER_CLEAN_RE.sub("", text)
    if not text:
        return None
    try:
        val = float(text)
        return -val if negative else val
    except ValueError:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Row label matching
# ─────────────────────────────────────────────────────────────────────────────

def _best_canonical_match(
    label: str,
    alias_map: Dict[str, List[str]],
) -> Tuple[Optional[str], float]:
    """
    Find the best canonical row name for a raw label using RapidFuzz.
    Returns (canonical_name, score).
    """
    if not label or not label.strip():
        return None, 0.0

    label_clean = re.sub(r"\s+", " ", label.lower()).strip()

    # Build flat alias → canonical lookup
    alias_to_canonical: Dict[str, str] = {}
    for canonical, aliases in alias_map.items():
        for alias in aliases:
            alias_to_canonical[alias] = canonical

    result = rfprocess.extractOne(
        label_clean,
        list(alias_to_canonical.keys()),
        scorer=fuzz.token_set_ratio,
        score_cutoff=0,
    )
    if result:
        return alias_to_canonical[result[0]], float(result[1])
    return None, 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Value extraction from a row
# ─────────────────────────────────────────────────────────────────────────────

def _extract_values(
    raw_row: List[str],
    col_map: List[DetectedColumn],
) -> Dict[str, Any]:
    """
    Given a raw cell row and column mapping, return {field_name: value} dict.
    Includes _raw (original string) and _confidence per field.
    """
    values: Dict[str, Any]         = {}
    raw_values: Dict[str, str]     = {}
    cell_confidence: Dict[str, float] = {}

    for dc in col_map:
        if dc.field_name in ("__skip__", "__unknown__", "asset_type", "item_name"):
            continue
        if dc.col_index >= len(raw_row):
            continue

        raw_cell = raw_row[dc.col_index]
        parsed   = _parse_number(raw_cell)

        values[dc.field_name]          = parsed if parsed is not None else 0.0
        raw_values[dc.field_name]      = raw_cell
        cell_confidence[dc.field_name] = dc.confidence if parsed is not None else 0.0

    return {
        "values":      values,
        "raw":         raw_values,
        "confidence":  cell_confidence,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def map_table_to_schema(table: TableData) -> List[Dict[str, Any]]:
    """
    Map a TableData to a list of dicts matching the canonical schema.
    Each dict contains extracted values + metadata (_confidence, _raw, _needs_llm).

    Returns only rows that were successfully matched (skips unrecognised rows).
    """
    alias_map   = (schemas.BLOCK_C_ROW_ALIASES  if table.block_type == "block_c"
                   else schemas.BLOCK_D_ROW_ALIASES)
    label_field = "asset_type" if table.block_type == "block_c" else "item_name"

    # Find the label column index
    label_col_idx: Optional[int] = None
    for dc in table.col_map:
        if dc.field_name == label_field:
            label_col_idx = dc.col_index
            break

    mapped_rows: List[Dict[str, Any]] = []

    for raw_row in table.data_rows:
        # Get raw label text
        if label_col_idx is not None and label_col_idx < len(raw_row):
            label_raw = raw_row[label_col_idx]
        else:
            # Try first non-empty cell
            label_raw = next((c for c in raw_row if c.strip()), "")

        canonical, row_score = _best_canonical_match(label_raw, alias_map)

        if canonical is None or row_score < config.FUZZY_LLM_THRESHOLD:
            logger.debug("Row unmatched: '%s' (score=%.0f)", label_raw[:50], row_score)
            continue

        extracted = _extract_values(raw_row, table.col_map)

        needs_llm = (
            row_score < config.FUZZY_ACCEPT_THRESHOLD
            or any(v < config.FUZZY_LLM_THRESHOLD
                   for v in extracted["confidence"].values())
        )

        row_dict: Dict[str, Any] = {
            label_field: canonical,
            **extracted["values"],
            "_raw":      extracted["raw"],
            "_confidence": {
                "row_match": row_score,
                **extracted["confidence"],
            },
            "_needs_llm":    needs_llm,
            "_label_raw":    label_raw,
            "_page_num":     table.page_num,
            "_source":       table.source,
        }

        mapped_rows.append(row_dict)
        logger.debug(
            "Mapped '%s' → '%s' (row_score=%.0f, needs_llm=%s)",
            label_raw[:40], canonical, row_score, needs_llm,
        )

    logger.info(
        "Table [%s] page %d: %d/%d rows mapped",
        table.block_type, table.page_num, len(mapped_rows), len(table.data_rows),
    )
    return mapped_rows


def merge_into_template(
    template: List[Dict[str, Any]],
    mapped_rows: List[Dict[str, Any]],
    label_field: str,
) -> List[Dict[str, Any]]:
    """
    Merge mapped_rows into the canonical template.
    Template rows maintain sl_no order; matched rows fill values.
    Later pages overwrite earlier if same canonical row appears multiple times
    (PDF may repeat table across pages — take the last occurrence).
    """
    # Index mapped rows by canonical label
    by_label: Dict[str, Dict[str, Any]] = {}
    for row in mapped_rows:
        lbl = row.get(label_field, "")
        if lbl:
            by_label[lbl] = row

    result = []
    for tmpl_row in template:
        canonical = tmpl_row[label_field]
        if canonical in by_label:
            merged = {**tmpl_row, **by_label[canonical]}
        else:
            merged = {**tmpl_row}
        result.append(merged)
    return result
