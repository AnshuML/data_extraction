"""
Agent 2 — Verifier (Llama 3.2)

Responsibility:
  Cross-check every extracted number against the raw OCR text.
  If a number cannot be found in the source text → mark it as UNVERIFIED.
  If too many numbers are UNVERIFIED → return REJECTED so supervisor retries.

Design principle: deterministic checks first, LLM only as last resort for
ambiguous number representations (e.g. crore vs lakh notation).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import config
import schemas
from utils.logger import get_logger
from utils.ollama_client import call_verifier

logger = get_logger("agent_2_verifier")

# Cache Ollama availability — check once per process
_ollama_status: Optional[bool] = None

def _ollama_up() -> bool:
    global _ollama_status
    if _ollama_status is None:
        from utils.ollama_client import is_ollama_alive
        _ollama_status = is_ollama_alive()
        if not _ollama_status:
            logger.warning("Agent 2: Ollama not available — deterministic verification only")
    return _ollama_status

# ─────────────────────────────────────────────────────────────────────────────
# Deterministic number presence check
# ─────────────────────────────────────────────────────────────────────────────

def _number_variants(value: float) -> List[str]:
    """
    Generate multiple string representations of a number for text search.
    e.g. 123456.0 → ["123456", "1,23,456", "1,23,456.00", "123,456"]
    """
    if value == 0.0:
        return []

    abs_val  = abs(value)
    variants = []

    # Plain integer
    if abs_val == int(abs_val):
        n = int(abs_val)
        variants.append(str(n))

        # Indian comma format: 1,23,456
        s = str(n)
        if len(s) > 3:
            indian = s[-3:]
            s = s[:-3]
            while s:
                indian = s[-2:] + "," + indian if len(s) > 2 else s + "," + indian
                s = s[:-2]
            variants.append(indian)

        # International comma format: 123,456
        variants.append(f"{n:,}")

    # Float with 2 decimals
    variants.append(f"{abs_val:.2f}")
    variants.append(f"{abs_val:.0f}")

    return list(set(variants))


def _is_number_in_text(value: float, raw_text: str) -> bool:
    """Return True if any variant of the number appears in raw OCR text."""
    if value == 0.0:
        return True   # zeros are never verified (could be genuinely absent)

    for variant in _number_variants(value):
        # Must appear as a standalone token (not sub-string of larger number)
        pattern = r"(?<!\d)" + re.escape(variant) + r"(?!\d)"
        if re.search(pattern, raw_text):
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# LLM fallback for suspicious numbers
# ─────────────────────────────────────────────────────────────────────────────

def _llm_verify(
    row_label: str,
    field: str,
    value: float,
    raw_snippet: str,
    model: str,
) -> bool:
    prompt = f"""You are verifying financial OCR extraction.

Extracted value: {value}  
Field: {field}  
Row: {row_label}  

Raw text from document:
{raw_snippet[:1500]}

Question: Can you find a number in the raw text that corresponds to this extracted value?
Consider that:
- The number may be in Lakhs (multiply by 100,000)
- The number may use Indian comma format (1,23,456)
- The number may appear as a rounded figure

Reply with ONLY one word: YES or NO
"""
    response = call_verifier(model, prompt)
    if response:
        return "YES" in response.upper()
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Per-row verification
# ─────────────────────────────────────────────────────────────────────────────

def _verify_row(
    row: Dict[str, Any],
    label_field: str,
    numeric_fields: List[str],
    all_raw_text: str,
) -> Tuple[Dict[str, Any], int, int]:
    """
    Verify all numeric fields in a row.
    Returns (updated_row, verified_count, total_nonzero_count).
    """
    verified_count  = 0
    total_nonzero   = 0
    label = row.get(label_field, "unknown")

    for field in numeric_fields:
        value = row.get(field, 0.0)
        if not isinstance(value, (int, float)):
            continue
        if value == 0.0:
            row.setdefault("_confidence", {})[f"{field}_verified"] = True
            continue

        total_nonzero += 1
        found = _is_number_in_text(value, all_raw_text)

        if not found:
            # LLM fallback for non-trivial values (only if Ollama is available)
            if value > 1000 and _ollama_up():
                found = _llm_verify(label, field, value, all_raw_text, config.VERIFIER_MODEL)
                if found:
                    logger.debug("LLM confirmed %s.%s = %s", label, field, value)

        row.setdefault("_confidence", {})[f"{field}_verified"] = found

        if found:
            verified_count += 1
        else:
            logger.warning("UNVERIFIED: row='%s' field='%s' value=%s",
                           label, field, value)

    return row, verified_count, total_nonzero


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def run(
    block_c_rows: List[Dict[str, Any]],
    block_d_rows: List[Dict[str, Any]],
    raw_text_by_page: Dict[int, str],
) -> Dict[str, Any]:
    """
    Verify all extracted values against raw OCR text.

    Returns:
        {
          "status":   "APPROVED" | "REJECTED",
          "block_c":  [...],
          "block_d":  [...],
          "summary":  {verified, total, unverified_rows: [...]}
        }
    """
    logger.info("Agent 2 — starting verification pass")
    all_raw_text = "\n".join(raw_text_by_page.values())

    total_verified = 0
    total_nonzero  = 0
    unverified_rows: List[str] = []

    # ── Block C ──────────────────────────────────────────────────────────────
    c_numeric = list(schemas.NUMERIC_ZERO.keys())
    updated_c = []
    for row in block_c_rows:
        row, v, t = _verify_row(row, "asset_type", c_numeric, all_raw_text)
        total_verified += v
        total_nonzero  += t
        if t > 0 and v < t:
            unverified_rows.append(f"Block C: {row.get('asset_type')}")
        updated_c.append(row)

    # ── Block D ──────────────────────────────────────────────────────────────
    d_numeric = ["opening_rs", "closing_rs"]
    updated_d = []
    for row in block_d_rows:
        row, v, t = _verify_row(row, "item_name", d_numeric, all_raw_text)
        total_verified += v
        total_nonzero  += t
        if t > 0 and v < t:
            unverified_rows.append(f"Block D: {row.get('item_name')}")
        updated_d.append(row)

    # ── Decision ─────────────────────────────────────────────────────────────
    verification_rate = (total_verified / total_nonzero) if total_nonzero > 0 else 1.0
    # Accept if ≥ 85% of non-zero values are verified
    status = "APPROVED" if verification_rate >= 0.85 else "REJECTED"

    logger.info(
        "Agent 2 — %s | verified %d/%d non-zero values (%.0f%%)",
        status, total_verified, total_nonzero, verification_rate * 100,
    )
    if unverified_rows:
        logger.warning("Unverified rows: %s", unverified_rows)

    return {
        "status":   status,
        "block_c":  updated_c,
        "block_d":  updated_d,
        "summary": {
            "verified":        total_verified,
            "total_nonzero":   total_nonzero,
            "rate":            round(verification_rate, 3),
            "unverified_rows": unverified_rows,
        },
    }
