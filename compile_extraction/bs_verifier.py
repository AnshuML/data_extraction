"""
Balance-sheet cross-verifier agent (no compile schedule / golden).

Builds validation_result.json from:
  - Internal formula checks
  - Balance sheet face-page / notes re-parse vs filled Block C & D

Re-reads flagged fields from the same PDF OCR text and patches Excel-bound rows.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from compile_extraction.quality import _close, _index_rows
from compile_extraction.schema import clean_number

logger = logging.getLogger(__name__)

TOL = 0.02

# Block D rows that should match balance sheet summary / notes (not derived)
D_BS_ROWS = (1, 2, 3, 5, 6, 8, 9, 10, 12, 13, 14, 17)
# Block C asset rows (not 8, 10 totals)
C_BS_ROWS = tuple(range(1, 8))

# Note / keyword hints for targeted page search
D_FIELD_HINTS: Dict[int, Dict[str, str]] = {
    1: {"opening_rs": "raw material", "closing_rs": "raw material"},
    3: {"opening_rs": "stores", "closing_rs": "stores"},
    5: {"opening_rs": "work in progress", "closing_rs": "work in progress"},
    6: {"opening_rs": "finished", "closing_rs": "finished"},
    8: {"opening_rs": "cash", "closing_rs": "cash"},
    9: {"opening_rs": "trade receivable", "closing_rs": "trade receivable"},
    10: {"opening_rs": "other current asset", "closing_rs": "other current asset"},
    12: {"opening_rs": "trade payable", "closing_rs": "trade payable"},
    13: {"opening_rs": "borrowing", "closing_rs": "borrowing"},
    14: {"opening_rs": "other current liabilit", "closing_rs": "other current liabilit"},
    17: {"opening_rs": "borrowing", "closing_rs": "non-current"},
}


@dataclass
class FieldCheck:
    block: str
    sl_no: int
    field: str
    status: bool
    reason: str
    got: float = 0.0
    expected_from_bs: float = 0.0
    match_score: int = 0
    hint_pages: List[int] = field(default_factory=list)
    hint_note: str = ""
    fixed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationResultDoc:
    pdf_name: str
    generated_at: str
    fields: List[FieldCheck] = field(default_factory=list)
    total_checks: int = 0
    passed: int = 0
    failed: int = 0
    fixed_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["fields"] = [f.to_dict() for f in self.fields]
        return d


def _find_summary_page(pages: Dict[int, str]) -> Optional[int]:
    for pnum, text in pages.items():
        t = text.lower()
        if re.search(r"amounts?\s+.{0,6}lacs?|in\s+lacs?", t, re.I):
            if "balance sheet" in t or "total assets" in t or "current assets" in t:
                return pnum
    for pnum, text in pages.items():
        t = text.lower()
        if "sources of funds" in t and "application of funds" in t:
            return pnum
    return None


def _find_pages_with_keyword(pages: Dict[int, str], keyword: str) -> List[int]:
    kw = keyword.lower()
    return sorted(p for p, t in pages.items() if kw in t.lower())


def _extract_bs_block_d_candidates(pages: Dict[int, str]) -> Dict[Tuple[int, str], float]:
    """Re-parse balance sheet text; return {(sl_no, field): rupees}."""
    from schedule_parser import parse_block_d_from_lakhs_notes, parse_block_d_from_text

    out: Dict[Tuple[int, str], float] = {}
    for pnum, text in sorted(pages.items()):
        for parser in (parse_block_d_from_lakhs_notes, parse_block_d_from_text):
            try:
                rows = parser(text)
            except Exception:
                continue
            for r in rows:
                sl = int(r.get("sl_no", 0))
                if sl not in D_BS_ROWS:
                    continue
                for f in ("opening_rs", "closing_rs"):
                    v = clean_number(r.get(f, 0))
                    if v != 0:
                        key = (sl, f)
                        if key not in out or v != 0:
                            out[key] = v
    return out


def _extract_bs_block_c_candidates(pages: Dict[int, str]) -> Dict[Tuple[int, str], float]:
    from schedule_parser import parse_block_c_from_text

    out: Dict[Tuple[int, str], float] = {}
    for _pnum, text in sorted(pages.items()):
        if not re.search(
            r"schedule\s*:?\s*5|gross block|net block|property, plant|property\.plant",
            text,
            re.I,
        ):
            continue
        try:
            rows = parse_block_c_from_text(text)
        except Exception:
            continue
        for r in rows:
            sl = int(r.get("sl_no", 0))
            if sl not in C_BS_ROWS:
                continue
            for f in C_NUMERIC_FIELDS:
                v = clean_number(r.get(f, 0))
                if v != 0:
                    out[(sl, f)] = v
    return out


def _row_match_score(got: float, expected: float) -> int:
    if got == 0 and expected == 0:
        return 100
    if got == 0 or expected == 0:
        return 40
    if _close(got, expected, TOL):
        return 95
    err = abs(got - expected) / max(abs(expected), 1)
    if err < 0.1:
        return 75
    if err < 0.3:
        return 55
    return 30


def build_validation_result(
    pdf_name: str,
    block_c: List[Dict],
    block_d: List[Dict],
    pages: Dict[int, str],
    validator_errors: Optional[List[Any]] = None,
) -> ValidationResultDoc:
    """Audit filled template vs re-parsed balance sheet (no golden JSON)."""
    checks: List[FieldCheck] = []
    rc, rd = _index_rows(block_c), _index_rows(block_d)
    bs_d = _extract_bs_block_d_candidates(pages)
    bs_c = _extract_bs_block_c_candidates(pages)
    summary_p = _find_summary_page(pages)

    for sl, field in bs_d:
        got = clean_number(rd.get(sl, {}).get(field, 0))
        exp = bs_d[(sl, field)]
        ok = _close(got, exp, TOL) if exp else got == 0
        hints = [summary_p] if summary_p else []
        hints.extend(_find_pages_with_keyword(pages, D_FIELD_HINTS.get(sl, {}).get(field, ""))[:3])
        hints = sorted({h for h in hints if h is not None})
        checks.append(FieldCheck(
            block="D",
            sl_no=sl,
            field=field,
            status=ok,
            reason="bs_reparse_match" if ok else "bs_reparse_mismatch",
            got=got,
            expected_from_bs=exp,
            match_score=_row_match_score(got, exp),
            hint_pages=hints,
            hint_note=D_FIELD_HINTS.get(sl, {}).get(field, ""),
        ))

    for sl, field in bs_c:
        got = clean_number(rc.get(sl, {}).get(field, 0))
        exp = bs_c[(sl, field)]
        ok = _close(got, exp, TOL) if exp else got == 0
        checks.append(FieldCheck(
            block="C",
            sl_no=sl,
            field=field,
            status=ok,
            reason="bs_schedule5_match" if ok else "bs_schedule5_mismatch",
            got=got,
            expected_from_bs=exp,
            match_score=_row_match_score(got, exp),
            hint_pages=_find_pages_with_keyword(pages, "schedule 5")[:3],
            hint_note="schedule 5 / fixed assets",
        ))

    if validator_errors:
        for e in validator_errors:
            block = getattr(e, "block", "D")
            sl = int(getattr(e, "sl_no", 0))
            fld = getattr(e, "field", "coverage")
            checks.append(FieldCheck(
                block=block,
                sl_no=sl,
                field=fld,
                status=False,
                reason="validator_" + getattr(e, "message", "error")[:40],
                got=float(getattr(e, "got", 0)),
                expected_from_bs=float(getattr(e, "expected", 0)),
                match_score=20,
                hint_pages=[],
                hint_note=str(getattr(e, "message", "")),
            ))

    passed = sum(1 for c in checks if c.status)
    return ValidationResultDoc(
        pdf_name=pdf_name,
        generated_at=datetime.now(timezone.utc).isoformat(),
        fields=checks,
        total_checks=len(checks),
        passed=passed,
        failed=len(checks) - passed,
    )


def _recompute_derived_d(block_d: List[Dict]) -> List[Dict]:
    rows = {int(r["sl_no"]): r for r in block_d}

    def g(sl: int, col: str) -> float:
        return clean_number(rows.get(sl, {}).get(col, 0))

    for col in ("opening_rs", "closing_rs"):
        if 4 in rows:
            rows[4][col] = g(1, col) + g(2, col) + g(3, col)
        if 7 in rows and 4 in rows:
            rows[7][col] = rows[4][col] + g(5, col) + g(6, col)
        if 11 in rows and 7 in rows:
            rows[11][col] = rows[7][col] + g(8, col) + g(9, col) + g(10, col)
        if 15 in rows:
            rows[15][col] = g(12, col) + g(13, col) + g(14, col)
        if 16 in rows and 11 in rows and 15 in rows:
            rows[16][col] = rows[11][col] - rows[15][col]
    return [rows[sl] for sl in sorted(rows)]


def _recompute_derived_c(block_c: List[Dict]) -> List[Dict]:
    rows = {r["sl_no"]: r for r in block_c}

    def g(sl: int, col: str) -> float:
        return clean_number(rows.get(sl, {}).get(col, 0))

    cols = (
        "gross_opening", "gross_closing", "net_opening", "net_closing",
    )
    if 8 in rows:
        for col in cols:
            rows[8][col] = sum(g(sl, col) for sl in range(2, 8))
    if 10 in rows and 8 in rows:
        for col in ("net_opening", "net_closing"):
            rows[10][col] = g(1, col) + rows[8][col] + g(9, col)
    return [rows[sl] for sl in sorted(rows)]


def _apply_summary_crosscheck(block_d: List[Dict], summary_text: str) -> List[Dict]:
    from schedule_parser import parse_block_d_from_lakhs_notes

    rows = {r["sl_no"]: r for r in block_d}
    lakhs_rows = parse_block_d_from_lakhs_notes(summary_text)
    for r in lakhs_rows:
        sl = int(r["sl_no"])
        if sl not in rows or sl in (4, 7, 8, 11, 15, 16):
            continue
        for f in ("opening_rs", "closing_rs"):
            v = clean_number(r.get(f, 0))
            if v:
                rows[sl][f] = v
    return [rows[sl] for sl in sorted(rows)]


def save_validation_result(doc: ValidationResultDoc, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(doc.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


class BalanceSheetVerifierAgent:
    """
    Agent 2: reads validation failures, re-extracts from balance sheet OCR,
    patches Block C / D, recomputes derived rows.
    """

    def run(
        self,
        pages: Dict[int, str],
        block_c: List[Dict],
        block_d: List[Dict],
        validation: ValidationResultDoc,
    ) -> Tuple[List[Dict], List[Dict], int]:
        logger.info("=== AGENT: Balance Sheet Verifier (BS cross-check) ===")
        failed = [f for f in validation.fields if not f.status and not f.fixed]
        if not failed:
            logger.info("  No BS mismatches to fix.")
            return block_c, block_d, 0

        logger.info("  Re-verifying %s field(s) against balance sheet text", len(failed))
        bs_d = _extract_bs_block_d_candidates(pages)
        bs_c = _extract_bs_block_c_candidates(pages)

        rc = {r["sl_no"]: r.copy() for r in block_c}
        rd = {r["sl_no"]: r.copy() for r in block_d}
        fixed = 0

        for fc in failed:
            key = (fc.sl_no, fc.field)
            if fc.block == "D" and key in bs_d:
                new_val = bs_d[key]
                if new_val and not _close(clean_number(rd.get(fc.sl_no, {}).get(fc.field, 0)), new_val):
                    if fc.sl_no in rd:
                        rd[fc.sl_no][fc.field] = new_val
                        fc.fixed = True
                        fc.status = True
                        fc.got = new_val
                        fixed += 1
                        logger.info(
                            "  Fixed D%s %s ← BS re-parse: %s",
                            fc.sl_no, fc.field, new_val,
                        )
            elif fc.block == "C" and key in bs_c:
                new_val = bs_c[key]
                if new_val and fc.sl_no in rc:
                    rc[fc.sl_no][fc.field] = new_val
                    fc.fixed = True
                    fc.status = True
                    fc.got = new_val
                    fixed += 1
                    logger.info(
                        "  Fixed C%s %s ← BS re-parse: %s",
                        fc.sl_no, fc.field, new_val,
                    )

        block_c = [rc[sl] for sl in sorted(rc)]
        block_d = [rd[sl] for sl in sorted(rd)]

        if fixed:
            block_d = _recompute_derived_d(block_d)
            block_c = _recompute_derived_c(block_c)
            from schedule_parser import (
                _merge_d_row_lists,
                parse_block_d_from_lakhs_notes,
            )

            note_rows: List[Dict] = []
            for _p, text in sorted(pages.items()):
                note_rows.extend(parse_block_d_from_lakhs_notes(text) or [])
            if note_rows:
                rd = {r["sl_no"]: r for r in block_d}
                for r in _merge_d_row_lists(note_rows):
                    sl = int(r["sl_no"])
                    if sl in (4, 7, 11, 15, 16):
                        continue
                    if sl not in rd:
                        rd[sl] = r
                    else:
                        for f in ("opening_rs", "closing_rs"):
                            v = clean_number(r.get(f, 0))
                            if v and (
                                sl != 8
                                or clean_number(rd[sl].get(f, 0)) < v * 0.5
                            ):
                                rd[sl][f] = v
                block_d = [rd[sl] for sl in sorted(rd)]
                block_d = _recompute_derived_d(block_d)

        validation.fixed_count = fixed
        logger.info("  BS verifier patched %s field(s)", fixed)
        return block_c, block_d, fixed


# ---------------------------------------------------------------------------
# Supervisor agent — loop until BS OCR cross-check + template fill complete
# ---------------------------------------------------------------------------

C_NUMERIC_FIELDS = (
    "gross_opening", "gross_addition_reval", "gross_addition_actual",
    "gross_deduction", "gross_closing", "dep_up_to_beginning",
    "dep_provided_during_year", "dep_adjustment", "dep_up_to_end",
    "net_opening", "net_closing",
)
D_NUMERIC_FIELDS = ("opening_rs", "closing_rs")
C_DERIVED_SL = {8, 10}
D_DERIVED_SL = {4, 7, 11, 15, 16}
D_OPTIONAL_ZERO_SL = {2}
C_ASSET_SL = tuple(range(1, 8)) + (9,)
# Key Block C columns that Note 4 must populate (supervisor strict check)
C_SUPERVISOR_FIELDS = (
    "gross_opening", "gross_closing", "net_opening", "net_closing",
    "dep_up_to_beginning", "dep_up_to_end",
)
# Minimum Block C template fill % before supervisor signs off (gross+dep+net from Note 4)
SUPERVISOR_MIN_C_FILL_PCT = float(
    os.environ.get("SUPERVISOR_MIN_C_FILL_PCT", "0.65")
)


@dataclass
class PendingCell:
    block: str
    sl_no: int
    field: str
    reason: str
    got: float = 0.0
    expected_from_bs: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SupervisorReport:
    complete: bool
    rounds: int
    message: str
    block_c_filled: int
    block_c_total: int
    block_d_filled: int
    block_d_total: int
    validation_passed: int
    validation_total: int
    pending: List[PendingCell] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["pending"] = [p.to_dict() for p in self.pending]
        return d


def _count_template_fill(
    block_c: List[Dict], block_d: List[Dict]
) -> Tuple[int, int, int, int]:
    """Return (c_filled, c_total, d_filled, d_total) for numeric template cells."""
    c_filled = c_total = d_filled = d_total = 0
    for row in block_c:
        sl = int(row["sl_no"])
        for f in C_NUMERIC_FIELDS:
            if sl in C_DERIVED_SL and f not in ("net_opening", "net_closing"):
                continue
            if sl not in C_ASSET_SL and sl not in C_DERIVED_SL:
                continue
            c_total += 1
            if clean_number(row.get(f, 0)) != 0:
                c_filled += 1
    for row in block_d:
        sl = int(row["sl_no"])
        for f in D_NUMERIC_FIELDS:
            d_total += 1
            if sl in D_OPTIONAL_ZERO_SL:
                d_filled += 1
                continue
            if clean_number(row.get(f, 0)) != 0:
                d_filled += 1
    return c_filled, c_total, d_filled, d_total


def _find_pending_cells(
    block_c: List[Dict],
    block_d: List[Dict],
    bs_c: Dict[Tuple[int, str], float],
    bs_d: Dict[Tuple[int, str], float],
) -> List[PendingCell]:
    """Cells still empty or mismatched vs balance-sheet re-parse."""
    pending: List[PendingCell] = []
    rc, rd = _index_rows(block_c), _index_rows(block_d)

    for sl, field in bs_d:
        if sl in D_DERIVED_SL:
            continue
        got = clean_number(rd.get(sl, {}).get(field, 0))
        exp = bs_d[(sl, field)]
        if exp == 0:
            continue
        if got == 0:
            pending.append(PendingCell(
                "D", sl, field, "empty_cell", got, exp,
            ))
        elif not _close(got, exp, TOL):
            pending.append(PendingCell(
                "D", sl, field, "bs_mismatch", got, exp,
            ))

    for sl, field in bs_c:
        if sl not in C_ASSET_SL or field not in C_SUPERVISOR_FIELDS:
            continue
        got = clean_number(rc.get(sl, {}).get(field, 0))
        exp = bs_c[(sl, field)]
        if exp == 0:
            continue
        if got == 0:
            pending.append(PendingCell(
                "C", sl, field, "empty_cell", got, exp,
            ))
        elif not _close(got, exp, TOL):
            pending.append(PendingCell(
                "C", sl, field, "bs_mismatch", got, exp,
            ))

    for sl in C_ASSET_SL:
        row = rc.get(sl, {})
        if not clean_number(row.get("net_opening", 0)) and not clean_number(
            row.get("net_closing", 0)
        ):
            pending.append(PendingCell(
                "C", sl, "net_opening", "asset_row_empty", 0, 0,
            ))

    for sl in range(1, 18):
        if sl in D_DERIVED_SL or sl in D_OPTIONAL_ZERO_SL:
            continue
        row = rd.get(sl, {})
        if not clean_number(row.get("opening_rs", 0)) and not clean_number(
            row.get("closing_rs", 0)
        ):
            pending.append(PendingCell(
                "D", sl, "opening_rs", "row_empty", 0, 0,
            ))

    return pending


def _compile_rows_filled(
    block_c: List[Dict], block_d: List[Dict]
) -> Tuple[bool, str]:
    """Row-level fill gate (not 92-cell template %)."""
    rc, rd = _index_rows(block_c), _index_rows(block_d)
    c_asset = list(range(2, 8))
    c_filled = sum(
        1
        for sl in c_asset
        if clean_number(rc.get(sl, {}).get("net_opening", 0))
        or clean_number(rc.get(sl, {}).get("net_closing", 0))
    )
    d_need = [sl for sl in range(1, 18) if sl not in D_DERIVED_SL]
    d_filled = sum(
        1
        for sl in d_need
        if sl in D_OPTIONAL_ZERO_SL
        or clean_number(rd.get(sl, {}).get("opening_rs", 0))
        or clean_number(rd.get(sl, {}).get("closing_rs", 0))
    )
    c_ok = c_filled >= 5
    d_ok = d_filled >= len(d_need) - 1
    msg = (
        f"compile rows C={c_filled}/{len(c_asset)} D={d_filled}/{len(d_need)}"
    )
    return c_ok and d_ok, msg


def _fill_from_bs(
    block_c: List[Dict],
    block_d: List[Dict],
    bs_c: Dict[Tuple[int, str], float],
    bs_d: Dict[Tuple[int, str], float],
) -> Tuple[List[Dict], List[Dict], int]:
    """Fill empty or patch mismatched cells from BS re-parse (generic)."""
    rc = {int(r["sl_no"]): r.copy() for r in block_c}
    rd = {int(r["sl_no"]): r.copy() for r in block_d}
    n = 0

    for (sl, field), exp in bs_d.items():
        if sl in D_DERIVED_SL or exp == 0:
            continue
        row = rd.setdefault(sl, {"sl_no": sl})
        cur = clean_number(row.get(field, 0))
        if cur > 0 and exp > cur * 20:
            continue
        if cur == 0 or not _close(cur, exp, TOL):
            row[field] = exp
            n += 1

    for (sl, field), exp in bs_c.items():
        if sl not in C_ASSET_SL or exp == 0:
            continue
        row = rc.setdefault(sl, {"sl_no": sl})
        cur = clean_number(row.get(field, 0))
        if cur > 0 and exp > cur * 20:
            continue
        if cur == 0 or not _close(cur, exp, TOL):
            row[field] = exp
            n += 1

    block_c = [rc[sl] for sl in sorted(rc)]
    block_d = [rd[sl] for sl in sorted(rd)]
    if n:
        block_d = _recompute_derived_d(block_d)
        block_c = _recompute_derived_c(block_c)
    return block_c, block_d, n


def save_supervisor_confirmation(report: SupervisorReport, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


class SupervisorAgent:
    """
    Supervisor: cross-verify every filled cell against balance-sheet OCR,
    re-fill mismatches / empty cells from BS re-parse, loop until complete
    or max rounds. Writes supervisor_confirmation.json when done.
    """

    def __init__(self, max_rounds: int = 5) -> None:
        self.max_rounds = max_rounds
        self.verifier = BalanceSheetVerifierAgent()

    def run(
        self,
        pdf_name: str,
        pages: Dict[int, str],
        block_c: List[Dict],
        block_d: List[Dict],
        validator_errors: Optional[List[Any]] = None,
        log_dir: Optional[Path] = None,
    ) -> Tuple[List[Dict], List[Dict], SupervisorReport, ValidationResultDoc]:
        logger.info("=== SUPERVISOR: Balance Sheet cross-verify & fill loop ===")
        validation_doc: Optional[ValidationResultDoc] = None

        for round_no in range(1, self.max_rounds + 1):
            logger.info("  Supervisor round %s/%s", round_no, self.max_rounds)

            bs_d = _extract_bs_block_d_candidates(pages)
            bs_c = _extract_bs_block_c_candidates(pages)

            block_c, block_d, n_fill = _fill_from_bs(block_c, block_d, bs_c, bs_d)
            if n_fill:
                logger.info("  Supervisor filled/patched %s cell(s) from BS OCR", n_fill)

            validation_doc = build_validation_result(
                pdf_name, block_c, block_d, pages, None,
            )
            failed = [f for f in validation_doc.fields if not f.status]
            if failed:
                block_c, block_d, nfix = self.verifier.run(
                    pages, block_c, block_d, validation_doc,
                )
                if nfix:
                    logger.info("  Verifier agent fixed %s field(s)", nfix)
                    validation_doc = build_validation_result(
                        pdf_name, block_c, block_d, pages, None,
                    )

            summary_p = _find_summary_page(pages)
            if summary_p:
                block_d = _apply_summary_crosscheck(
                    block_d, pages[summary_p],
                )
                block_d = _recompute_derived_d(block_d)
                block_c = _recompute_derived_c(block_c)

            bs_c = _extract_bs_block_c_candidates(pages)
            pending = _find_pending_cells(block_c, block_d, bs_c, bs_d)
            c_f, c_t, d_f, d_t = _count_template_fill(block_c, block_d)
            val_ok = validation_doc.failed == 0
            rows_ok, rows_msg = _compile_rows_filled(block_c, block_d)

            if val_ok and not pending and rows_ok:
                report = SupervisorReport(
                    complete=True,
                    rounds=round_no,
                    message=(
                        "Supervisor confirmation: "
                        f"{rows_msg}; BS cross-check pending=0."
                    ),
                    block_c_filled=c_f,
                    block_c_total=c_t,
                    block_d_filled=d_f,
                    block_d_total=d_t,
                    validation_passed=validation_doc.passed,
                    validation_total=validation_doc.total_checks,
                    pending=[],
                )
                logger.info("  %s", report.message)
                if log_dir:
                    save_validation_result(
                        validation_doc, log_dir / "validation_result.json",
                    )
                    save_supervisor_confirmation(
                        report, log_dir / "supervisor_confirmation.json",
                    )
                return block_c, block_d, report, validation_doc

            logger.info(
                "  Round %s: validation %s/%s, pending cells=%s, "
                "template fill C=%s/%s D=%s/%s",
                round_no,
                validation_doc.passed,
                validation_doc.total_checks,
                len(pending),
                c_f, c_t, d_f, d_t,
            )

        pending = _find_pending_cells(
            block_c, block_d,
            _extract_bs_block_d_candidates(pages),
            _extract_bs_block_c_candidates(pages),
        )
        c_f, c_t, d_f, d_t = _count_template_fill(block_c, block_d)
        validation_doc = build_validation_result(
            pdf_name, block_c, block_d, pages, validator_errors,
        )
        rows_ok, rows_msg = _compile_rows_filled(block_c, block_d)
        report = SupervisorReport(
            complete=False,
            rounds=self.max_rounds,
            message=(
                "Supervisor incomplete: "
                f"{rows_msg}; template cells C={c_f}/{c_t} D={d_f}/{d_t}; "
                f"pending OCR fixes={len(pending)}. "
                "See supervisor_confirmation.json."
            ),
            block_c_filled=c_f,
            block_c_total=c_t,
            block_d_filled=d_f,
            block_d_total=d_t,
            validation_passed=validation_doc.passed,
            validation_total=validation_doc.total_checks,
            pending=pending[:50],
        )
        logger.warning("  %s (%s pending)", report.message, len(pending))
        if log_dir:
            save_validation_result(
                validation_doc, log_dir / "validation_result.json",
            )
            save_supervisor_confirmation(
                report, log_dir / "supervisor_confirmation.json",
            )
        return block_c, block_d, report, validation_doc
