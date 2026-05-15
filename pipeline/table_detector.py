"""
Table Detector — Layer 2.

Takes raw CellMatrix objects from the PDF processor and:
  1. Identifies which tables are Block C (Fixed Assets) vs Block D (Working Capital)
  2. Detects the header row and maps column positions to schema field names
  3. Returns a typed TableData object ready for row mapping
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from rapidfuzz import fuzz, process as rfprocess

import schemas
from pipeline.pdf_processor import CellMatrix
from utils.logger import get_logger

logger = get_logger("table_detector")

# ─────────────────────────────────────────────────────────────────────────────
# Keywords to classify which block a table belongs to
# ─────────────────────────────────────────────────────────────────────────────

_BLOCK_C_SIGNALS = [
    "fixed assets", "gross block", "net block", "depreciation", "land",
    "building", "plant", "machinery", "capital work", "cwip",
    "schedule of fixed assets", "fixed asset schedule",
]
_BLOCK_D_SIGNALS = [
    "working capital", "current assets", "current liabilities", "inventory",
    "sundry debtors", "sundry creditors", "cash in hand", "raw material",
    "finished goods", "debtors", "creditors", "overdraft",
]

# Minimum columns to consider a table "real"
_MIN_COLS_BLOCK_C = 5
_MIN_COLS_BLOCK_D = 2


# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DetectedColumn:
    col_index:  int
    field_name: str    # canonical schema field name
    confidence: float  # 0–100


@dataclass
class TableData:
    block_type:    str                    # "block_c" | "block_d" | "unknown"
    header_row:    int                    # index of header row in matrix
    data_rows:     List[List[str]]        # rows after header, raw strings
    col_map:       List[DetectedColumn]   # column index → field name
    page_num:      int
    source:        str
    raw_matrix:    List[List[str]] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("\n", " ")).strip()


def _score_signals(text: str, signals: List[str]) -> float:
    t = _clean(text)
    best = 0.0
    for sig in signals:
        score = fuzz.partial_ratio(sig, t)
        if score > best:
            best = score
    return best


def _classify_table(matrix: List[List[str]]) -> str:
    """Return 'block_c', 'block_d', or 'unknown'."""
    flat_text = " ".join(
        cell for row in matrix[:6] for cell in row if cell
    )
    c_score = _score_signals(flat_text, _BLOCK_C_SIGNALS)
    d_score = _score_signals(flat_text, _BLOCK_D_SIGNALS)

    num_cols = max((len(r) for r in matrix), default=0)

    if c_score > 70 and num_cols >= _MIN_COLS_BLOCK_C:
        logger.debug("Table classified as block_c (score=%.0f)", c_score)
        return "block_c"
    if d_score > 70 and num_cols >= _MIN_COLS_BLOCK_D:
        logger.debug("Table classified as block_d (score=%.0f)", d_score)
        return "block_d"
    if c_score > d_score and num_cols >= _MIN_COLS_BLOCK_C:
        return "block_c"
    if d_score > c_score and num_cols >= _MIN_COLS_BLOCK_D:
        return "block_d"
    return "unknown"


def _find_header_row(matrix: List[List[str]], block_type: str) -> int:
    """
    Find the row index that looks like the column header row.
    Heuristic: first row where majority of cells are text (not numbers).
    """
    number_pattern = re.compile(r"^[\d,.\s]+$")
    for i, row in enumerate(matrix):
        non_empty = [c for c in row if c.strip()]
        if not non_empty:
            continue
        numeric_count = sum(1 for c in non_empty if number_pattern.match(c))
        text_ratio = 1 - (numeric_count / len(non_empty))
        if text_ratio > 0.5:
            return i
    return 0


def _map_columns_block_c(header_cells: List[str]) -> List[DetectedColumn]:
    """Map header cell texts to Block C field names using RapidFuzz."""
    aliases_flat: Dict[str, str] = {}
    for field_name, alias_list in schemas.BLOCK_C_COLUMN_ALIASES.items():
        for alias in alias_list:
            aliases_flat[alias] = field_name

    col_map: List[DetectedColumn] = []
    for idx, cell in enumerate(header_cells):
        cell_clean = _clean(cell)
        if not cell_clean:
            col_map.append(DetectedColumn(col_index=idx, field_name="__skip__", confidence=0))
            continue

        result = rfprocess.extractOne(
            cell_clean, list(aliases_flat.keys()),
            scorer=fuzz.token_set_ratio
        )
        if result and result[1] >= 55:
            col_map.append(DetectedColumn(
                col_index=idx,
                field_name=aliases_flat[result[0]],
                confidence=float(result[1]),
            ))
        else:
            # Check if it's the label column
            if any(kw in cell_clean for kw in ["sl", "no", "type", "asset", "particulars", "description"]):
                col_map.append(DetectedColumn(col_index=idx, field_name="asset_type", confidence=90))
            else:
                col_map.append(DetectedColumn(col_index=idx, field_name="__unknown__", confidence=0))

    return col_map


def _map_columns_block_d(header_cells: List[str]) -> List[DetectedColumn]:
    """Map header cell texts to Block D field names using RapidFuzz."""
    aliases_flat: Dict[str, str] = {}
    for field_name, alias_list in schemas.BLOCK_D_COLUMN_ALIASES.items():
        for alias in alias_list:
            aliases_flat[alias] = field_name

    col_map: List[DetectedColumn] = []
    for idx, cell in enumerate(header_cells):
        cell_clean = _clean(cell)
        if not cell_clean:
            col_map.append(DetectedColumn(col_index=idx, field_name="__skip__", confidence=0))
            continue

        result = rfprocess.extractOne(
            cell_clean, list(aliases_flat.keys()),
            scorer=fuzz.token_set_ratio
        )
        if result and result[1] >= 55:
            col_map.append(DetectedColumn(
                col_index=idx,
                field_name=aliases_flat[result[0]],
                confidence=float(result[1]),
            ))
        else:
            if any(kw in cell_clean for kw in ["sl", "no", "item", "particulars", "description"]):
                col_map.append(DetectedColumn(col_index=idx, field_name="item_name", confidence=90))
            else:
                col_map.append(DetectedColumn(col_index=idx, field_name="__unknown__", confidence=0))

    return col_map


def _infer_columns_by_position(num_cols: int, block_type: str) -> List[DetectedColumn]:
    """
    Positional fallback when no header row is detected.
    Assumes standard left-to-right column ordering.
    """
    if block_type == "block_c":
        # Standard Block C column order
        ordered = [
            "asset_type", "gross_opening", "gross_addition_reval",
            "gross_addition_actual", "gross_deduction", "gross_closing",
            "dep_up_to_beginning", "dep_provided_during_year",
            "dep_adjustment", "dep_up_to_end", "net_opening", "net_closing",
        ]
    else:
        ordered = ["item_name", "opening_rs", "closing_rs"]

    result = []
    for i in range(num_cols):
        fn = ordered[i] if i < len(ordered) else "__unknown__"
        result.append(DetectedColumn(col_index=i, field_name=fn, confidence=60.0))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def detect_tables(cell_matrices: List[CellMatrix]) -> List[TableData]:
    """
    Process a list of CellMatrix objects and return detected TableData objects.
    May return multiple tables per page (e.g. Block C and Block D on same page).
    """
    results: List[TableData] = []

    for cm in cell_matrices:
        if not cm.rows or len(cm.rows) < 2:
            continue

        block_type = _classify_table(cm.rows)
        if block_type == "unknown":
            logger.debug("Page %d: table not recognised as Block C or D — skipping",
                         cm.page_num)
            continue

        header_idx  = _find_header_row(cm.rows, block_type)
        header_row  = cm.rows[header_idx]
        data_rows   = cm.rows[header_idx + 1:]

        # Remove fully-empty rows
        data_rows = [r for r in data_rows if any(c.strip() for c in r)]

        # Map columns via RapidFuzz header matching
        num_cols = max((len(r) for r in cm.rows), default=0)
        if any(c.strip() for c in header_row):
            if block_type == "block_c":
                col_map = _map_columns_block_c(header_row)
            else:
                col_map = _map_columns_block_d(header_row)
        else:
            col_map = _infer_columns_by_position(num_cols, block_type)

        # Schema is FIXED — patch any unmapped columns positionally.
        # Block C always has 13 cols (1 label + 12 numeric).
        # Block D always has  3 cols (1 label +  2 numeric).
        expected_fields = (
            ["asset_type", "gross_opening", "gross_addition_reval",
             "gross_addition_actual", "gross_deduction", "gross_closing",
             "dep_up_to_beginning", "dep_provided_during_year",
             "dep_adjustment", "dep_up_to_end", "net_opening", "net_closing"]
            if block_type == "block_c"
            else ["item_name", "opening_rs", "closing_rs"]
        )
        already_mapped = {dc.field_name for dc in col_map
                          if dc.field_name not in ("__skip__", "__unknown__")}
        missing_fields  = [f for f in expected_fields if f not in already_mapped]

        if missing_fields:
            # Find physical columns that are currently unmapped
            unmapped_col_idxs = [
                dc.col_index for dc in col_map
                if dc.field_name in ("__unknown__",)
            ]
            for i, field_name in enumerate(missing_fields):
                if i < len(unmapped_col_idxs):
                    # Patch the unmapped column with the expected field
                    for dc in col_map:
                        if dc.col_index == unmapped_col_idxs[i]:
                            dc.field_name = field_name
                            dc.confidence  = 55.0   # low but usable
                            break
                    logger.debug(
                        "Positional patch: col[%d] → %s",
                        unmapped_col_idxs[i], field_name,
                    )
            if missing_fields:
                logger.info(
                    "Column patch applied for %s: %d fields filled positionally",
                    block_type, len(missing_fields),
                )

        known_cols = [c for c in col_map if c.field_name not in ("__skip__", "__unknown__")]
        logger.info(
            "Page %d [%s]: %s table — %d data rows, %d/%d cols mapped",
            cm.page_num, cm.source, block_type,
            len(data_rows), len(known_cols), len(col_map),
        )

        results.append(TableData(
            block_type=block_type,
            header_row=header_idx,
            data_rows=data_rows,
            col_map=col_map,
            page_num=cm.page_num,
            source=cm.source,
            raw_matrix=cm.rows,
        ))

    return results
