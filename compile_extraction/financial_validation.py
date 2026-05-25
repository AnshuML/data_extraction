"""
Enterprise financial integrity — mandatory subtotals, row identities, face anchors.

Target: 100% internal financial validation before Excel export (no golden file required).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from compile_extraction.schema import clean_number

logger = logging.getLogger(__name__)

# Stricter than QA golden compare — compile sheet arithmetic must hold.
FINANCIAL_RATIO_TOL = 0.005
FINANCIAL_ABS_FLOOR = 100.0
DERIVED_ROW_ABS_TOL = 1.0


@dataclass
class FinancialCheck:
    ok: bool
    code: str
    message: str
    block: str = ""
    sl_no: int = 0
    field: str = ""


@dataclass
class FinancialValidationReport:
    passed: int
    total: int
    checks: List[FinancialCheck] = field(default_factory=list)

    @property
    def score_pct(self) -> float:
        return self.passed / self.total * 100 if self.total else 100.0

    @property
    def ok(self) -> bool:
        return self.passed == self.total and self.total > 0

    @property
    def failures(self) -> List[str]:
        return [c.message for c in self.checks if not c.ok]


def _index_rows(rows: List[Dict]) -> Dict[int, Dict]:
    out: Dict[int, Dict] = {}
    for r in rows:
        try:
            sl = int(float(r.get("sl_no", 0)))
        except (TypeError, ValueError):
            continue
        out[sl] = r
    return out


def _fin_close(a: float, b: float, *, strict_derived: bool = False) -> bool:
    if a == 0 and b == 0:
        return True
    if strict_derived:
        return abs(a - b) <= DERIVED_ROW_ABS_TOL
    ref = max(abs(a), abs(b), 1.0)
    abs_tol = max(FINANCIAL_ABS_FLOOR, ref * FINANCIAL_RATIO_TOL)
    return abs(a - b) <= abs_tol


def _g(rows: Dict[int, Dict], sl: int, col: str) -> float:
    return clean_number(rows.get(sl, {}).get(col, 0))


def _add_check(
    checks: List[FinancialCheck],
    ok: bool,
    code: str,
    message: str,
    block: str = "",
    sl_no: int = 0,
    field: str = "",
) -> None:
    checks.append(
        FinancialCheck(ok=ok, code=code, message=message, block=block, sl_no=sl_no, field=field)
    )


def validate_block_c_strict(block_c: List[Dict]) -> List[FinancialCheck]:
    """Mandatory Block C rules — subtotals, totals, PPE row identities."""
    checks: List[FinancialCheck] = []
    rc = _index_rows(block_c)

    filled = sum(
        1 for sl in range(2, 8)
        if _g(rc, sl, "net_closing") > 0 or _g(rc, sl, "net_opening") > 0
    )
    _add_check(
        checks,
        filled >= 3,
        "C_COVERAGE",
        f"Block C: only {filled}/6 asset rows have net block data (need >= 3)",
        "C",
    )

    for sl in range(2, 8):
        if sl not in rc:
            continue
        row = rc[sl]
        g_o = _g(rc, sl, "gross_opening")
        g_c = _g(rc, sl, "gross_closing")
        n_o = _g(rc, sl, "net_opening")
        n_c = _g(rc, sl, "net_closing")
        d_b = _g(rc, sl, "dep_up_to_beginning")
        d_p = _g(rc, sl, "dep_provided_during_year")
        d_adj = _g(rc, sl, "dep_adjustment")
        d_e = _g(rc, sl, "dep_up_to_end")
        add_r = _g(rc, sl, "gross_addition_reval")
        add_a = _g(rc, sl, "gross_addition_actual")
        deduct = _g(rc, sl, "gross_deduction")

        if n_o > 0 and d_b > 0 and g_o > 0:
            implied = n_o + d_b
            _add_check(
                checks,
                _fin_close(g_o, implied),
                "C_GROSS_OPEN_ID",
                f"C{sl} gross_opening {int(g_o):,} != net_open+dep_beg {int(implied):,}",
                "C",
                sl,
                "gross_opening",
            )

        if n_c > 0 and d_e > 0 and g_c > 0:
            implied = n_c + d_e
            _add_check(
                checks,
                _fin_close(g_c, implied),
                "C_GROSS_CLOSE_ID",
                f"C{sl} gross_closing {int(g_c):,} != net_close+dep_end {int(implied):,}",
                "C",
                sl,
                "gross_closing",
            )

        if d_e > 0 and (d_b > 0 or d_p > 0):
            implied_de = d_b + d_p - d_adj
            _add_check(
                checks,
                _fin_close(d_e, implied_de),
                "C_DEP_ROLL",
                f"C{sl} dep_up_to_end {int(d_e):,} != beg+prov-adj {int(implied_de):,}",
                "C",
                sl,
                "dep_up_to_end",
            )

        if g_c > 0 and g_o > 0:
            implied_gc = g_o + add_r + add_a - deduct
            _add_check(
                checks,
                _fin_close(g_c, implied_gc),
                "C_GROSS_MOVE",
                f"C{sl} gross_closing {int(g_c):,} != opening+add-deduct {int(implied_gc):,}",
                "C",
                sl,
                "gross_closing",
            )

        if n_c > 0 and g_c > 0 and d_e > 0:
            _add_check(
                checks,
                d_e <= g_c * 1.001,
                "C_DEP_LE_GROSS",
                f"C{sl} dep_up_to_end ({int(d_e):,}) exceeds gross_closing ({int(g_c):,})",
                "C",
                sl,
                "dep_up_to_end",
            )

    if 8 in rc:
        for col in (
            "gross_opening",
            "gross_closing",
            "gross_addition_actual",
            "gross_deduction",
            "net_opening",
            "net_closing",
            "dep_up_to_beginning",
            "dep_up_to_end",
        ):
            exp = sum(_g(rc, sl, col) for sl in range(2, 8))
            got = _g(rc, 8, col)
            _add_check(
                checks,
                _fin_close(got, exp, strict_derived=True),
                "C_SUBTOTAL_8",
                f"C row 8 {col}: got {int(got):,} expected sum(2-7) {int(exp):,}",
                "C",
                8,
                col,
            )

    if 10 in rc and 8 in rc:
        for col in ("net_opening", "net_closing"):
            exp = _g(rc, 1, col) + _g(rc, 8, col) + _g(rc, 9, col)
            got = _g(rc, 10, col)
            _add_check(
                checks,
                _fin_close(got, exp, strict_derived=True),
                "C_TOTAL_10",
                f"C row 10 {col}: got {int(got):,} expected 1+8+9 {int(exp):,}",
                "C",
                10,
                col,
            )
        nc10 = _g(rc, 10, "net_closing")
        _add_check(
            checks,
            nc10 > 1_000_000,
            "C_TOTAL_SCALE",
            f"C row 10 net_closing {int(nc10):,} below minimum scale (1 crore)",
            "C",
            10,
            "net_closing",
        )

    return checks


def validate_block_d_strict(block_d: List[Dict]) -> List[FinancialCheck]:
    """Mandatory Block D rules — all compile derived rows + WC identity."""
    checks: List[FinancialCheck] = []
    rd = _index_rows(block_d)

    filled = sum(
        1
        for sl in range(1, 18)
        if _g(rd, sl, "opening_rs") > 0 or _g(rd, sl, "closing_rs") > 0
    )
    _add_check(
        checks,
        filled >= 8,
        "D_COVERAGE",
        f"Block D: only {filled}/17 rows have amounts (need >= 8)",
        "D",
    )

    derived_specs = (
        (4, lambda c: _g(rd, 1, c) + _g(rd, 2, c) + _g(rd, 3, c), "sum(1..3)"),
        (7, lambda c: _g(rd, 4, c) + _g(rd, 5, c) + _g(rd, 6, c), "4+5+6"),
        (11, lambda c: _g(rd, 7, c) + _g(rd, 8, c) + _g(rd, 9, c) + _g(rd, 10, c), "7+8+9+10"),
        (15, lambda c: _g(rd, 12, c) + _g(rd, 13, c) + _g(rd, 14, c), "12+13+14"),
        (16, lambda c: _g(rd, 11, c) - _g(rd, 15, c), "11-15"),
    )

    for col in ("opening_rs", "closing_rs"):
        for sl, fn, label in derived_specs:
            if sl not in rd:
                continue
            exp = fn(col)
            got = _g(rd, sl, col)
            _add_check(
                checks,
                _fin_close(got, exp, strict_derived=True),
                f"D_DERIVED_{sl}",
                f"D row {sl} {col}: got {int(got):,} expected {label} {int(exp):,}",
                "D",
                sl,
                col,
            )

    for sl in (1, 5, 6, 8, 9, 10, 12, 13, 14, 17):
        has = _g(rd, sl, "opening_rs") > 0 or _g(rd, sl, "closing_rs") > 0
        if sl in (2, 3):
            continue
        _add_check(
            checks,
            has,
            "D_ROW_DATA",
            f"D row {sl} has no opening/closing amounts",
            "D",
            sl,
        )

    for sl in range(1, 18):
        o = _g(rd, sl, "opening_rs")
        c = _g(rd, sl, "closing_rs")
        if o < 0 or c < 0:
            _add_check(
                checks,
                False,
                "D_NON_NEGATIVE",
                f"D row {sl} negative amount (opening={o}, closing={c})",
                "D",
                sl,
            )

    return checks


def validate_face_anchors(
    block_c: List[Dict],
    pages: Optional[Dict[int, str]],
    *,
    tol_ratio: float = 0.03,
) -> List[FinancialCheck]:
    """Optional: net subtotal (rows 2–7) vs BS face PPE note 5."""
    checks: List[FinancialCheck] = []
    if not pages:
        return checks

    from compile_extraction.reconcile import parse_face_ppe_net

    face_o, face_c = parse_face_ppe_net(pages)
    if face_o <= 0 and face_c <= 0:
        return checks

    rc = _index_rows(block_c)
    sum_no = sum(_g(rc, sl, "net_opening") for sl in range(1, 8))
    sum_nc = sum(_g(rc, sl, "net_closing") for sl in range(1, 8))

    for field, face_val, got in (
        ("net_opening", face_o, sum_no),
        ("net_closing", face_c, sum_nc),
    ):
        if face_val <= 0 or got <= 0:
            continue
        ratio = abs(got - face_val) / face_val
        _add_check(
            checks,
            ratio <= tol_ratio,
            "FACE_PPE",
            f"Face PPE {field}: extracted sum {int(got):,} vs BS face {int(face_val):,} "
            f"({ratio * 100:.2f}% off)",
            "C",
            8,
            field,
        )

    return checks


def validate_financial_integrity(
    block_c: List[Dict],
    block_d: List[Dict],
    pages: Optional[Dict[int, str]] = None,
    *,
    check_face: bool = True,
) -> FinancialValidationReport:
    """Run all mandatory financial checks."""
    checks: List[FinancialCheck] = []
    checks.extend(validate_block_c_strict(block_c))
    checks.extend(validate_block_d_strict(block_d))
    if check_face and pages:
        checks.extend(validate_face_anchors(block_c, pages))

    passed = sum(1 for c in checks if c.ok)
    return FinancialValidationReport(
        passed=passed,
        total=len(checks),
        checks=checks,
    )


def apply_financial_reconciliation(
    block_c: List[Dict],
    block_d: List[Dict],
    pages: Optional[Dict[int, str]] = None,
) -> Tuple[List[Dict], List[Dict]]:
    """
    Auto-reconcile before validation/export:
    - PPE row identities
    - BS face PPE anchor (when pages available)
    - Recompute Block C/D derived subtotal & total rows in Python
    - Schedule 10 → Sl 14
    """
    from compile_extraction.reconcile import (
        _repair_ppe_identities,
        reconcile_block_c_to_face,
        reconcile_block_d_sl14,
    )

    rc = _index_rows(block_c)
    for sl in range(2, 8):
        if sl in rc:
            _repair_ppe_identities(rc[sl], force_gross=True)
    block_c = [rc[sl] for sl in sorted(rc)] if rc else block_c

    if pages:
        block_c = reconcile_block_c_to_face(block_c, pages)
        block_d = reconcile_block_d_sl14(block_d, pages)

    from run_agentic_pipeline import _compute_derived_rows_c, _compute_derived_rows_d

    block_c = _compute_derived_rows_c(block_c)
    block_d = _compute_derived_rows_d(block_d)

    rc = _index_rows(block_c)
    for sl in range(2, 8):
        if sl in rc:
            _repair_ppe_identities(rc[sl], force_gross=True)
    block_c = [rc[sl] for sl in sorted(rc)] if rc else block_c
    block_c = _compute_derived_rows_c(block_c)

    if pages:
        from schedule_parser import apply_schedule5_gross_subtotal_impute

        block_c = apply_schedule5_gross_subtotal_impute(block_c, pages)
        block_c = _compute_derived_rows_c(block_c)

    logger.info("  Financial auto-reconciliation applied (derived rows recomputed)")
    return block_c, block_d
