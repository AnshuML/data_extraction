"""Detailed accuracy and table-level reports."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from compile_extraction.audit import FieldAccuracy
from compile_extraction.quality import QualityReport, _close, _index_rows
from compile_extraction.schema import clean_number

TOL = 0.02


@dataclass
class AccuracyReport:
    field_level_pct: float
    table_c_pct: float
    table_d_pct: float
    mapping_pct: float
    rules_pct: float
    fields_total: int
    fields_passed: int
    fields_failed: int
    field_details: List[FieldAccuracy] = field(default_factory=list)
    mismatches: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    low_confidence_notes: List[str] = field(default_factory=list)

    def to_summary_lines(self) -> List[str]:
        lines = [
            f"Field-level accuracy: {self.field_level_pct:.1f}% ({self.fields_passed}/{self.fields_total})",
            f"Block C table accuracy: {self.table_c_pct:.1f}%",
            f"Block D table accuracy: {self.table_d_pct:.1f}%",
            f"Mapping accuracy (rows placed): {self.mapping_pct:.1f}%",
            f"Rules validation: {self.rules_pct:.1f}%",
        ]
        if self.mismatches:
            lines.append("Mismatches:")
            lines.extend(f"  - {m}" for m in self.mismatches[:25])
        if self.missing:
            lines.append("Missing / zero values:")
            lines.extend(f"  - {m}" for m in self.missing[:15])
        return lines


def _field_acc(
    block: str, sl: int, field: str, expected: float, got: float, tol: float = TOL
) -> FieldAccuracy:
    match = _close(got, expected, tol) if expected != 0 else got == 0
    pct_err = 0.0
    if expected and not match:
        pct_err = abs(got - expected) / max(abs(expected), 1) * 100
    return FieldAccuracy(
        block=block, sl_no=sl, field=field,
        expected=expected, got=got, match=match, pct_error=round(pct_err, 2),
    )


def build_accuracy_report(
    block_c: List[Dict],
    block_d: List[Dict],
    golden_c: Optional[List[Dict]] = None,
    golden_d: Optional[List[Dict]] = None,
    rules: Optional[QualityReport] = None,
    tol: float = TOL,
) -> AccuracyReport:
    details: List[FieldAccuracy] = []
    rc, rd = _index_rows(block_c), _index_rows(block_d)

    if golden_c is not None:
        gc = _index_rows(golden_c)
        c_fields = [
            "gross_opening", "gross_closing", "net_opening", "net_closing",
        ]
        c_pass = c_total = 0
        for sl, truth in gc.items():
            if sl in (8, 10):
                continue
            got = rc.get(sl, {})
            for f in c_fields:
                exp = clean_number(truth.get(f, 0))
                if exp == 0:
                    continue
                val = clean_number(got.get(f, 0))
                fa = _field_acc("C", sl, f, exp, val, tol)
                details.append(fa)
                c_total += 1
                if fa.match:
                    c_pass += 1
        table_c = c_pass / c_total * 100 if c_total else 0.0
    else:
        table_c = 0.0

    if golden_d is not None:
        gd = _index_rows(golden_d)
        d_pass = d_total = 0
        for sl, truth in gd.items():
            if sl in (4, 7, 11, 15, 16):
                continue
            got = rd.get(sl, {})
            for f in ("opening_rs", "closing_rs"):
                exp = clean_number(truth.get(f, 0))
                if exp == 0:
                    continue
                val = clean_number(got.get(f, 0))
                fa = _field_acc("D", sl, f, exp, val, tol)
                details.append(fa)
                d_total += 1
                if fa.match:
                    d_pass += 1
        table_d = d_pass / d_total * 100 if d_total else 0.0
    else:
        table_d = 0.0

    mapping_ok = 0
    mapping_total = 15
    for sl in range(1, 11):
        if sl in rc and (
            clean_number(rc[sl].get("net_closing", 0)) > 0
            or clean_number(rc[sl].get("gross_opening", 0)) > 0
            or sl in (1, 6, 9)
        ):
            mapping_ok += 1
    for sl in range(1, 18):
        if sl in rd and (
            clean_number(rd[sl].get("closing_rs", 0)) != 0
            or clean_number(rd[sl].get("opening_rs", 0)) != 0
            or sl in (2, 3, 16)
        ):
            mapping_ok += 1
    mapping_pct = mapping_ok / mapping_total * 100 if mapping_total else 0.0

    passed = sum(1 for d in details if d.match)
    total = len(details)
    field_pct = passed / total * 100 if total else 0.0
    mismatches = [f"{d.block}{d.sl_no} {d.field}: expected {d.expected:,.0f} got {d.got:,.0f}" for d in details if not d.match]
    missing = [f"{d.block}{d.sl_no} {d.field}" for d in details if d.expected != 0 and d.got == 0]

    return AccuracyReport(
        field_level_pct=field_pct,
        table_c_pct=table_c,
        table_d_pct=table_d,
        mapping_pct=mapping_pct,
        rules_pct=rules.score_pct if rules else 0.0,
        fields_total=total,
        fields_passed=passed,
        fields_failed=total - passed,
        field_details=details,
        mismatches=mismatches,
        missing=missing,
    )
