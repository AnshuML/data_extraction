"""Internal quality score — compile sheet rules without golden file."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from compile_extraction.schema import clean_number

TOL = 0.02


def _close(a: float, b: float, tol: float = TOL) -> bool:
    if a == 0 and b == 0:
        return True
    if a == 0 or b == 0:
        return abs(a - b) < 1000
    return abs(a - b) / max(abs(a), abs(b)) <= tol


def _g(rows: Dict[int, Dict], sl: int, col: str) -> float:
    return clean_number(rows.get(sl, {}).get(col, 0))


@dataclass
class QualityReport:
    score_pct: float
    passed: int
    total: int
    failures: List[str]

    @property
    def ok(self) -> bool:
        return self.score_pct >= 95.0 and self.passed == self.total


def _index_rows(rows: List[Dict]) -> Dict[int, Dict]:
    out: Dict[int, Dict] = {}
    for r in rows:
        sl = r.get("sl_no")
        try:
            sl = int(float(sl))
        except (TypeError, ValueError):
            continue
        out[sl] = r
    return out


def score_extraction(block_c: List[Dict], block_d: List[Dict]) -> QualityReport:
    """Score extracted data against compile sheet arithmetic rules."""
    checks: List[Tuple[bool, str]] = []
    rc = _index_rows(block_c)
    rd = _index_rows(block_d)

    # Block D — derived rows (compile sheet formulas)
    for col in ("opening_rs", "closing_rs"):
        exp4 = _g(rd, 1, col) + _g(rd, 2, col) + _g(rd, 3, col)
        checks.append((_close(_g(rd, 4, col), exp4), f"D row 4 {col}"))
        exp7 = _g(rd, 4, col) + _g(rd, 5, col) + _g(rd, 6, col)
        checks.append((_close(_g(rd, 7, col), exp7), f"D row 7 {col}"))
        exp11 = _g(rd, 7, col) + _g(rd, 8, col) + _g(rd, 9, col) + _g(rd, 10, col)
        checks.append((_close(_g(rd, 11, col), exp11), f"D row 11 {col}"))
        exp15 = _g(rd, 12, col) + _g(rd, 13, col) + _g(rd, 14, col)
        checks.append((_close(_g(rd, 15, col), exp15), f"D row 15 {col}"))
        exp16 = _g(rd, 11, col) - _g(rd, 15, col)
        checks.append((_close(_g(rd, 16, col), exp16), f"D row 16 {col}"))

    # Block D — key rows must have data
    for sl in (1, 5, 6, 8, 9, 10, 12, 13, 17):
        has = _g(rd, sl, "opening_rs") > 0 or _g(rd, sl, "closing_rs") > 0
        if sl in (2, 3):
            continue
        checks.append((has, f"D row {sl} has data"))

    # Block C — sub-total row 8
    if 8 in rc:
        for col in (
            "gross_opening", "gross_closing", "net_opening", "net_closing",
        ):
            exp = sum(_g(rc, sl, col) for sl in range(2, 8))
            checks.append((_close(_g(rc, 8, col), exp), f"C row 8 {col}"))

    # Block C — row 10 totals
    if 10 in rc and 8 in rc:
        for col in ("net_opening", "net_closing"):
            exp = _g(rc, 1, col) + _g(rc, 8, col) + _g(rc, 9, col)
            checks.append((_close(_g(rc, 10, col), exp), f"C row 10 {col}"))

    # Block C — row 10 net closing from schedule scale (> 1 crore)
    if 10 in rc:
        nc = _g(rc, 10, "net_closing")
        checks.append((nc > 10_000_000, "C row 10 net_closing plausible"))

    failures = [msg for ok, msg in checks if not ok]
    passed = sum(1 for ok, _ in checks if ok)
    total = len(checks)
    pct = passed / total * 100 if total else 0.0
    return QualityReport(score_pct=pct, passed=passed, total=total, failures=failures)


def score_against_golden(
    block_c: List[Dict],
    block_d: List[Dict],
    golden_c: List[Dict],
    golden_d: List[Dict],
    tol: float = TOL,
) -> QualityReport:
    """Compare numeric fields to a reference golden dataset (optional QA)."""
    checks: List[Tuple[bool, str]] = []
    rc = _index_rows(block_c)
    rd = _index_rows(block_d)
    gc = _index_rows(golden_c)
    gd = _index_rows(golden_d)

    c_fields = [
        "gross_opening", "gross_closing", "net_opening", "net_closing",
    ]
    for sl, truth in gc.items():
        if sl in (8, 10):
            continue
        got = rc.get(sl, {})
        for field in c_fields:
            exp = clean_number(truth.get(field, 0))
            if exp == 0:
                continue
            val = clean_number(got.get(field, 0))
            checks.append((_close(val, exp, tol), f"C{sl} {field}"))

    for sl, truth in gd.items():
        if sl in (4, 7, 11, 15, 16):
            continue
        got = rd.get(sl, {})
        for field in ("opening_rs", "closing_rs"):
            exp = clean_number(truth.get(field, 0))
            if exp == 0:
                continue
            val = clean_number(got.get(field, 0))
            checks.append((_close(val, exp, tol), f"D{sl} {field}"))

    failures = [msg for ok, msg in checks if not ok]
    passed = sum(1 for ok, _ in checks if ok)
    total = len(checks)
    pct = passed / total * 100 if total else 0.0
    return QualityReport(score_pct=pct, passed=passed, total=total, failures=failures)
