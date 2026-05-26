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
    """Score extracted data against compile sheet rules (delegates to strict financial validation)."""
    from compile_extraction.financial_validation import validate_financial_integrity

    fin = validate_financial_integrity(block_c, block_d, pages=None, check_face=False)
    return QualityReport(
        score_pct=fin.score_pct,
        passed=fin.passed,
        total=fin.total,
        failures=fin.failures,
    )


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
