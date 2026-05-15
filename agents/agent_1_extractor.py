"""
Agent 1 — Extractor

Responsibility:
  - Receive pipeline-extracted rows (already mapped by RapidFuzz)
  - For rows flagged _needs_llm=True, call the local LLM to clarify
    ambiguous labels OR fill in cells that OCR returned as empty/noise
  - Returns the completed block_c and block_d row lists

The LLM is called ONLY for ambiguous rows — not for the full table.
This keeps latency low and prevents hallucination on well-extracted cells.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import config
import schemas
from utils.logger import get_logger
from utils.ollama_client import call_extractor, parse_json_from_response

logger = get_logger("agent_1_extractor")


# ─────────────────────────────────────────────────────────────────────────────
# LLM prompt builders
# ─────────────────────────────────────────────────────────────────────────────

def _build_block_c_prompt(ambiguous_rows: List[Dict[str, Any]], raw_text_snippet: str) -> str:
    canonical_labels = [r["asset_type"] for r in schemas.BLOCK_C_CANONICAL_ROWS]
    numeric_fields   = list(schemas.NUMERIC_ZERO.keys())

    rows_summary = "\n".join(
        f"  - Label: '{r['_label_raw']}' | Raw values: {r.get('_raw', {})}"
        for r in ambiguous_rows
    )

    return f"""You are a Financial Data Analyst. I have partially extracted a Fixed Assets schedule (Block C) from a scanned balance sheet PDF using OCR.

Some rows need your help. For each row below, do TWO things:
1. Map the label to the EXACT canonical name from this list:
   {json.dumps(canonical_labels)}

2. Parse the raw numeric values and fill them into the correct fields:
   {json.dumps(numeric_fields)}
   Rules:
   - Numbers may have commas (e.g. "1,23,456" → 123456.0)
   - Bracketed numbers are negative: "(5000)" → -5000.0
   - "NIL" or "-" → 0.0
   - Do NOT guess values that are not in the raw text

AMBIGUOUS ROWS:
{rows_summary}

SUPPORTING RAW TEXT (from same page):
{raw_text_snippet[:2000]}

Return ONLY a valid JSON array like:
[
  {{
    "asset_type": "<exact canonical name>",
    "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0,
    "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0,
    "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0,
    "net_opening": 0.0, "net_closing": 0.0
  }}
]
"""


def _build_block_d_prompt(ambiguous_rows: List[Dict[str, Any]], raw_text_snippet: str) -> str:
    canonical_labels = [r["item_name"] for r in schemas.BLOCK_D_CANONICAL_ROWS]

    rows_summary = "\n".join(
        f"  - Label: '{r['_label_raw']}' | Raw values: {r.get('_raw', {})}"
        for r in ambiguous_rows
    )

    return f"""You are a Financial Data Analyst. I have partially extracted a Working Capital schedule (Block D) from a scanned balance sheet PDF using OCR.

Some rows need clarification. For each row:
1. Map the label to the EXACT canonical name:
   {json.dumps(canonical_labels)}
2. Parse "opening_rs" and "closing_rs" from the raw values.
   Rules: commas are thousands separators, "(value)" = negative, "NIL"/"-" = 0.0

AMBIGUOUS ROWS:
{rows_summary}

SUPPORTING RAW TEXT:
{raw_text_snippet[:2000]}

Return ONLY a valid JSON array:
[
  {{"item_name": "<exact canonical name>", "opening_rs": 0.0, "closing_rs": 0.0}}
]
"""


# ─────────────────────────────────────────────────────────────────────────────
# LLM response handler
# ─────────────────────────────────────────────────────────────────────────────

def _parse_llm_rows(response: Optional[str], block_type: str) -> List[Dict[str, Any]]:
    if not response:
        return []
    # Model may return array wrapped in object
    text = response.strip()
    start_arr = text.find("[")
    end_arr   = text.rfind("]") + 1
    if start_arr != -1 and end_arr > start_arr:
        text = text[start_arr:end_arr]
    try:
        rows = json.loads(text)
        if isinstance(rows, list):
            return rows
        if isinstance(rows, dict):
            key = "block_c" if block_type == "block_c" else "block_d"
            return rows.get(key, [])
    except json.JSONDecodeError as e:
        logger.warning("LLM JSON parse failed: %s", e)
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Main agent function
# ─────────────────────────────────────────────────────────────────────────────

def run(
    block_c_rows: List[Dict[str, Any]],
    block_d_rows: List[Dict[str, Any]],
    raw_text_by_page: Dict[int, str],
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Entry point for Agent 1.

    Args:
        block_c_rows:      RapidFuzz-mapped Block C rows (may contain _needs_llm=True)
        block_d_rows:      RapidFuzz-mapped Block D rows
        raw_text_by_page:  {page_num: raw_ocr_text} for context

    Returns:
        {"block_c": [...], "block_d": [...]} — completed rows
    """
    logger.info("Agent 1 — starting extraction refinement")

    # ── Check Ollama availability once ───────────────────────────────────────
    from utils.ollama_client import is_ollama_alive
    ollama_available = is_ollama_alive()
    if not ollama_available:
        logger.warning(
            "Agent 1: Ollama not available — LLM disambiguation skipped. "
            "RapidFuzz-mapped rows will be used as-is. "
            "Install Ollama + pull gemma3:4b for better accuracy on ambiguous rows."
        )
        return {"block_c": block_c_rows, "block_d": block_d_rows}

    # ── Block C ──────────────────────────────────────────────────────────────
    c_ambiguous = [r for r in block_c_rows if r.get("_needs_llm")]
    if c_ambiguous:
        logger.info("Agent 1: %d Block C rows need LLM clarification", len(c_ambiguous))
        page_nums   = list({r.get("_page_num", 1) for r in c_ambiguous})
        snippet     = " ".join(raw_text_by_page.get(p, "") for p in page_nums)
        prompt      = _build_block_c_prompt(c_ambiguous, snippet)
        response    = call_extractor(config.EXTRACTOR_MODEL, prompt)
        llm_rows    = _parse_llm_rows(response, "block_c")

        llm_by_label = {r.get("asset_type", ""): r for r in llm_rows}
        for row in block_c_rows:
            if row.get("_needs_llm") and row.get("asset_type") in llm_by_label:
                llm_row = llm_by_label[row["asset_type"]]
                for field in schemas.NUMERIC_ZERO:
                    if field in llm_row and llm_row[field] != 0.0:
                        row[field] = llm_row[field]
                row["_confidence"]["llm_assisted"] = True
                logger.debug("LLM patched Block C row: %s", row["asset_type"])
    else:
        logger.info("Agent 1: all Block C rows mapped with high confidence — no LLM needed")

    # ── Block D ──────────────────────────────────────────────────────────────
    d_ambiguous = [r for r in block_d_rows if r.get("_needs_llm")]
    if d_ambiguous:
        logger.info("Agent 1: %d Block D rows need LLM clarification", len(d_ambiguous))
        page_nums   = list({r.get("_page_num", 1) for r in d_ambiguous})
        snippet     = " ".join(raw_text_by_page.get(p, "") for p in page_nums)
        prompt      = _build_block_d_prompt(d_ambiguous, snippet)
        response    = call_extractor(config.EXTRACTOR_MODEL, prompt)
        llm_rows    = _parse_llm_rows(response, "block_d")

        llm_by_label = {r.get("item_name", ""): r for r in llm_rows}
        for row in block_d_rows:
            if row.get("_needs_llm") and row.get("item_name") in llm_by_label:
                llm_row = llm_by_label[row["item_name"]]
                for field in ("opening_rs", "closing_rs"):
                    if field in llm_row and llm_row[field] != 0.0:
                        row[field] = llm_row[field]
                row["_confidence"]["llm_assisted"] = True
    else:
        logger.info("Agent 1: all Block D rows mapped with high confidence — no LLM needed")

    logger.info("Agent 1 — extraction refinement complete")
    return {"block_c": block_c_rows, "block_d": block_d_rows}
