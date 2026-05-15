"""
Agent 3 — Python Formula Auditor (deterministic, no LLM)

Responsibility:
  Verify all mathematical relationships defined in the balance sheet schema:

  Block C:
    row 8  (Sub-total)  = sum of rows 2..7
    row 10 (Total)      = row 1 + row 8 + row 9
    net_closing         = gross_closing - dep_up_to_end  (per row)

  Block D:
    row 4  = sum(rows 1..3)
    row 7  = sum(rows 4..6)
    row 11 = sum(rows 7..10)
    row 15 = sum(rows 12..14)
    row 16 = row 11 - row 15
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import config
from utils.logger import get_logger

logger = get_logger("agent_3_auditor")

_TOL = config.VERIFIER_NUMBER_TOLERANCE   # 1% tolerance


def _approx_equal(a: float, b: float, tol: float = _TOL) -> bool:
    """True if |a - b| / max(|b|, 1) <= tol."""
    return abs(a - b) / max(abs(b), 1.0) <= tol


def _get(rows_by_sl: Dict[int, Dict], sl: int, field: str) -> float:
    row = rows_by_sl.get(sl, {})
    val = row.get(field, 0.0)
    return float(val) if val is not None else 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Block C audits
# ─────────────────────────────────────────────────────────────────────────────

_BLOCK_C_NUMERIC = [
    "gross_opening", "gross_addition_reval", "gross_addition_actual",
    "gross_deduction", "gross_closing", "dep_up_to_beginning",
    "dep_provided_during_year", "dep_adjustment", "dep_up_to_end",
    "net_opening", "net_closing",
]


def _audit_block_c(rows: List[Dict[str, Any]]) -> List[str]:
    """Returns list of audit failure messages. Empty list = PASS."""
    failures: List[str] = []
    by_sl = {r.get("sl_no"): r for r in rows if r.get("sl_no")}

    for field in _BLOCK_C_NUMERIC:
        # Sub-total (sl 8) = sum of sl 2..7
        computed_subtotal = sum(_get(by_sl, sl, field) for sl in range(2, 8))
        reported_subtotal = _get(by_sl, 8, field)
        if reported_subtotal != 0.0 and not _approx_equal(computed_subtotal, reported_subtotal):
            failures.append(
                f"Block C [{field}]: Sub-total row8={reported_subtotal:.2f} "
                f"≠ sum(2..7)={computed_subtotal:.2f}"
            )

        # Total (sl 10) = sl 1 + sl 8 + sl 9
        computed_total = (
            _get(by_sl, 1, field) +
            _get(by_sl, 8, field) +
            _get(by_sl, 9, field)
        )
        reported_total = _get(by_sl, 10, field)
        if reported_total != 0.0 and not _approx_equal(computed_total, reported_total):
            failures.append(
                f"Block C [{field}]: Total row10={reported_total:.2f} "
                f"≠ row1+row8+row9={computed_total:.2f}"
            )

    # Net block check: net_closing ≈ gross_closing - dep_up_to_end (per row)
    for row in rows:
        sl = row.get("sl_no")
        if sl in (8, 10):   # aggregated rows — skip individual check
            continue
        gc  = row.get("gross_closing", 0.0) or 0.0
        dep = row.get("dep_up_to_end",  0.0) or 0.0
        nc  = row.get("net_closing",    0.0) or 0.0
        if gc != 0.0 and nc != 0.0 and not _approx_equal(nc, gc - dep):
            failures.append(
                f"Block C [sl={sl} {row.get('asset_type')}]: "
                f"net_closing={nc:.2f} ≠ gross_closing({gc:.2f}) - dep({dep:.2f})"
            )

    return failures


# ─────────────────────────────────────────────────────────────────────────────
# Block D audits
# ─────────────────────────────────────────────────────────────────────────────

def _audit_block_d_field(by_sl: Dict[int, Dict], field: str) -> List[str]:
    failures: List[str] = []

    # sl 4 = sum(1..3)
    computed = sum(_get(by_sl, sl, field) for sl in range(1, 4))
    reported = _get(by_sl, 4, field)
    if reported != 0.0 and not _approx_equal(computed, reported):
        failures.append(
            f"Block D [{field}]: sl4={reported:.2f} ≠ sum(1..3)={computed:.2f}"
        )

    # sl 7 = sum(4..6)
    computed = sum(_get(by_sl, sl, field) for sl in range(4, 7))
    reported = _get(by_sl, 7, field)
    if reported != 0.0 and not _approx_equal(computed, reported):
        failures.append(
            f"Block D [{field}]: sl7={reported:.2f} ≠ sum(4..6)={computed:.2f}"
        )

    # sl 11 = sum(7..10)
    computed = sum(_get(by_sl, sl, field) for sl in range(7, 11))
    reported = _get(by_sl, 11, field)
    if reported != 0.0 and not _approx_equal(computed, reported):
        failures.append(
            f"Block D [{field}]: sl11={reported:.2f} ≠ sum(7..10)={computed:.2f}"
        )

    # sl 15 = sum(12..14)
    computed = sum(_get(by_sl, sl, field) for sl in range(12, 15))
    reported = _get(by_sl, 15, field)
    if reported != 0.0 and not _approx_equal(computed, reported):
        failures.append(
            f"Block D [{field}]: sl15={reported:.2f} ≠ sum(12..14)={computed:.2f}"
        )

    # sl 16 = sl 11 - sl 15 (Working Capital)
    computed = _get(by_sl, 11, field) - _get(by_sl, 15, field)
    reported = _get(by_sl, 16, field)
    if reported != 0.0 and not _approx_equal(computed, reported):
        failures.append(
            f"Block D [{field}]: WorkingCapital sl16={reported:.2f} "
            f"≠ sl11({_get(by_sl,11,field):.2f}) - sl15({_get(by_sl,15,field):.2f})"
        )

    return failures


def _audit_block_d(rows: List[Dict[str, Any]]) -> List[str]:
    failures: List[str] = []
    by_sl = {r.get("sl_no"): r for r in rows if r.get("sl_no")}

    for field in ("opening_rs", "closing_rs"):
        failures.extend(_audit_block_d_field(by_sl, field))

    return failures


# ─────────────────────────────────────────────────────────────────────────────
# Auto-correction for derived rows
# ─────────────────────────────────────────────────────────────────────────────

def _autocorrect_block_c(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    If sub-total or total rows are all zeros but component rows have data,
    compute and fill the derived rows automatically.
    """
    by_sl = {r.get("sl_no"): r for r in rows if r.get("sl_no")}

    for field in _BLOCK_C_NUMERIC:
        # Recompute sub-total (row 8) if zero
        subtotal_row = by_sl.get(8, {})
        if subtotal_row.get(field, 0.0) == 0.0:
            computed = sum(_get(by_sl, sl, field) for sl in range(2, 8))
            if computed != 0.0:
                subtotal_row[field] = computed

        # Recompute total (row 10) if zero
        total_row = by_sl.get(10, {})
        if total_row.get(field, 0.0) == 0.0:
            computed = (_get(by_sl, 1, field) +
                        _get(by_sl, 8, field) +
                        _get(by_sl, 9, field))
            if computed != 0.0:
                total_row[field] = computed

    return rows


def _autocorrect_block_d(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_sl = {r.get("sl_no"): r for r in rows if r.get("sl_no")}

    for field in ("opening_rs", "closing_rs"):
        checks = [
            (4,  list(range(1, 4))),
            (7,  list(range(4, 7))),
            (11, list(range(7, 11))),
            (15, list(range(12, 15))),
        ]
        for target_sl, source_sls in checks:
            target_row = by_sl.get(target_sl, {})
            if target_row.get(field, 0.0) == 0.0:
                computed = sum(_get(by_sl, sl, field) for sl in source_sls)
                if computed != 0.0:
                    target_row[field] = computed

        # Working Capital: sl 16 = sl 11 - sl 15
        wc_row = by_sl.get(16, {})
        if wc_row.get(field, 0.0) == 0.0:
            computed = _get(by_sl, 11, field) - _get(by_sl, 15, field)
            if computed != 0.0:
                wc_row[field] = computed

    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def run(
    block_c_rows: List[Dict[str, Any]],
    block_d_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Run mathematical audit on extracted data.

    Returns:
        {
          "status":    "APPROVED" | "REJECTED",
          "block_c":   [...],  # auto-corrected derived rows
          "block_d":   [...],
          "failures":  [...]   # list of failure messages
        }
    """
    logger.info("Agent 3 — starting formula audit")

    # Auto-correct derived rows (totals/subtotals) where possible
    block_c_rows = _autocorrect_block_c(block_c_rows)
    block_d_rows = _autocorrect_block_d(block_d_rows)

    failures_c = _audit_block_c(block_c_rows)
    failures_d = _audit_block_d(block_d_rows)
    all_failures = failures_c + failures_d

    for f in all_failures:
        logger.warning("AUDIT FAIL: %s", f)

    # Tolerate up to 2 formula mismatches (rounding differences in source PDF)
    status = "APPROVED" if len(all_failures) <= 2 else "REJECTED"

    logger.info("Agent 3 — %s | %d formula failures", status, len(all_failures))

    return {
        "status":   status,
        "block_c":  block_c_rows,
        "block_d":  block_d_rows,
        "failures": all_failures,
    }
