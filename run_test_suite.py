#!/usr/bin/env python3
"""
End-to-end test suite:
  1) Validate extraction on BS1 (DSL 118184) vs golden JSON
  2) Build golden from Compile Schedule PDF for BS2 (DSL 114045)
  3) Run pipeline on BS2 balance sheet and measure accuracy
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from compile_extraction.audit import AuditSession
from compile_extraction.config import SETTINGS
from compile_extraction.excel import (
    normalize_block_c_from_excel,
    normalize_block_d_from_excel,
)
from compile_extraction.golden_extractor import extract_golden_from_compile_pdf
from compile_extraction.pipeline import run_pipeline
from compile_extraction.quality import score_against_golden, score_extraction
from compile_extraction.reports import build_accuracy_report

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTPUTS = ROOT / "outputs"
GOLDEN_DIR = ROOT / "config" / "golden"
LOGS = ROOT / "logs"

BS1 = DATA / "Balance Sheet of DSL 118184 (1).pdf"
BS2 = DATA / "Balance Sheet of DSL 114045 (1).pdf"
COMPILE2 = DATA / "Compile schedule DSL 114045 (1).pdf"
GOLDEN1 = GOLDEN_DIR / "dsl_118184.json"
GOLDEN2 = GOLDEN_DIR / "dsl_114045.json"


def _load_golden(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _save_golden(path: Path, company: str, psl: str, block_c, block_d) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"company": company, "psl": psl, "block_c": block_c, "block_d": block_d}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Golden saved: {path}")


def run_one(
    pdf: Path,
    out_xlsx: Path,
    golden_path: Path | None,
    save_ocr: bool = True,
    max_attempts: int = 2,
) -> int:
    print("\n" + "=" * 70)
    print(f"PIPELINE: {pdf.name}")
    print("=" * 70)
    session = AuditSession(str(pdf), base_log_dir=str(LOGS))
    elog = session.extraction_logger
    elog.info("Starting pipeline for %s", pdf.name)

    run_pipeline(
        str(pdf),
        str(out_xlsx),
        max_attempts=max_attempts,
        dpi=SETTINGS.dpi,
        save_ocr=save_ocr,
        audit_session=session,
    )

    import pandas as pd
    df_c = pd.read_excel(out_xlsx, sheet_name="Block C - Fixed Assets")
    df_d = pd.read_excel(out_xlsx, sheet_name="Block D - Working Capital")
    block_c = normalize_block_c_from_excel(df_c.to_dict("records"))
    block_d = normalize_block_d_from_excel(df_d.to_dict("records"))

    rules = score_extraction(block_c, block_d)
    golden_c = golden_d = None
    if golden_path and golden_path.is_file():
        g = _load_golden(golden_path)
        golden_c, golden_d = g.get("block_c", []), g.get("block_d", [])
    g_report = None
    if golden_c or golden_d:
        g_report = score_against_golden(block_c, block_d, golden_c or [], golden_d or [])

    acc = build_accuracy_report(
        block_c, block_d,
        golden_c=golden_c, golden_d=golden_d,
        rules=rules,
    )
    for line in acc.to_summary_lines():
        print(line)
        session.verification_logger.info(line)

    session.audit.mapping_status = {
        "block_c_rows_filled": sum(
            1 for r in block_c
            if r.get("net_closing") or r.get("gross_opening")
        ),
        "block_d_rows_filled": sum(
            1 for r in block_d
            if r.get("closing_rs") or r.get("opening_rs")
        ),
        "field_accuracy_pct": acc.field_level_pct,
    }
    session.finalize(
        result=None,
        output_path=str(out_xlsx),
        rules_report=rules,
        golden_report=g_report,
        field_details=acc.field_details,
    )

    ok = (
        rules.score_pct >= SETTINGS.min_quality_pct
        and (g_report is None or g_report.score_pct >= SETTINGS.min_quality_pct)
        and acc.field_level_pct >= SETTINGS.min_quality_pct
    )
    return 0 if ok else 1


def main() -> None:
    os.chdir(ROOT)
    OUTPUTS.mkdir(exist_ok=True)
    LOGS.mkdir(exist_ok=True)

    if not BS1.is_file():
        print(f"Missing: {BS1}")
        sys.exit(1)
    if not BS2.is_file():
        print(f"Missing: {BS2}")
        sys.exit(1)

    print("STEP 1: Validate BS1 (DSL 118184) — calibrate extraction logic")
    code1 = run_one(
        BS1,
        OUTPUTS / "Compile_DSL_118184_test.xlsx",
        GOLDEN1 if GOLDEN1.is_file() else None,
    )

    print("\nSTEP 2: Build golden for BS2 from Compile Schedule PDF")
    if COMPILE2.is_file():
        gc, gd = extract_golden_from_compile_pdf(str(COMPILE2))
        print(f"  Block C golden rows: {len(gc)}")
        print(f"  Block D golden rows: {len(gd)}")
        _save_golden(GOLDEN2, "DSL 114045", "32742", gc, gd)
        audit = AuditSession(str(COMPILE2), base_log_dir=str(LOGS))
        audit.verification_logger.info("Golden C rows: %s", len(gc))
        audit.verification_logger.info("Golden D rows: %s", len(gd))
        for r in gd:
            audit.mapping_logger.info(
                "D%d opening=%s closing=%s", r["sl_no"], r.get("opening_rs"), r.get("closing_rs")
            )
    else:
        print(f"  Warning: {COMPILE2} not found — skipping golden build")

    print("\nSTEP 3: Test BS2 (DSL 114045) balance sheet extraction")
    code2 = run_one(
        BS2,
        OUTPUTS / "Compile_DSL_114045_test.xlsx",
        GOLDEN2 if GOLDEN2.is_file() else None,
    )

    print("\n" + "=" * 70)
    print("TEST SUITE COMPLETE")
    print(f"  BS1 exit code: {code1}")
    print(f"  BS2 exit code: {code2}")
    print(f"  Logs folder:   {LOGS}")
    print("=" * 70)
    sys.exit(max(code1, code2))


if __name__ == "__main__":
    main()
