#!/usr/bin/env python3
"""Production CLI: Balance Sheet PDF → Compile Sheet Excel."""
from __future__ import annotations

import argparse
import logging
import os
import sys

from compile_extraction.audit import AuditSession
from compile_extraction.config import SETTINGS
from compile_extraction.excel import write_excel
from compile_extraction.pipeline import run_pipeline
from compile_extraction.reports import build_accuracy_report
from compile_extraction.excel import (
    normalize_block_c_from_excel,
    normalize_block_d_from_excel,
)
from compile_extraction.quality import score_against_golden, score_extraction

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract Block C & D compile sheet from balance sheet PDF"
    )
    parser.add_argument("pdf", help="Path to balance sheet PDF")
    parser.add_argument(
        "-o", "--output",
        default=os.path.join("outputs", "Compile_output.xlsx"),
    )
    parser.add_argument("--max-attempts", type=int, default=SETTINGS.max_attempts)
    parser.add_argument("--dpi", type=int, default=SETTINGS.dpi)
    parser.add_argument("--save-ocr", action="store_true")
    parser.add_argument(
        "--quality-only",
        action="store_true",
        help="Score existing Excel without re-running extraction",
    )
    parser.add_argument(
        "--golden",
        default="",
        help="Optional golden JSON for field-level accuracy (e.g. config/golden/dsl_118184.json)",
    )
    args = parser.parse_args()

    pdf_path = os.path.abspath(args.pdf)
    out_path = os.path.abspath(args.output)
    session = None
    result = None

    if args.quality_only:
        import pandas as pd
        df_c = pd.read_excel(out_path, sheet_name="Block C - Fixed Assets")
        df_d = pd.read_excel(out_path, sheet_name="Block D - Working Capital")
        block_c = df_c.to_dict("records")
        block_d = df_d.to_dict("records")
    else:
        if not os.path.isfile(pdf_path):
            logger.error("PDF not found: %s", pdf_path)
            sys.exit(1)
        session = AuditSession(pdf_path, base_log_dir="logs")
        result = run_pipeline(
            pdf_path, out_path,
            max_attempts=args.max_attempts,
            dpi=args.dpi,
            save_ocr=args.save_ocr,
            audit_session=session,
        )
        import pandas as pd
        df_c = pd.read_excel(out_path, sheet_name="Block C - Fixed Assets")
        df_d = pd.read_excel(out_path, sheet_name="Block D - Working Capital")
        block_c = normalize_block_c_from_excel(df_c.to_dict("records"))
        block_d = normalize_block_d_from_excel(df_d.to_dict("records"))

    report = score_extraction(block_c, block_d)
    print("\n" + "=" * 60)
    print("  QUALITY REPORT (compile sheet rules)")
    print("=" * 60)
    print(f"  Score: {report.passed}/{report.total} ({report.score_pct:.1f}%)")
    if report.failures:
        print("  Issues:")
        for f in report.failures[:15]:
            print(f"    - {f}")
    else:
        print("  All internal checks passed.")
    print(f"  Target: >= {SETTINGS.min_quality_pct:.0f}%")

    golden_path = args.golden.strip()
    if not golden_path and "118184" in os.path.basename(pdf_path if not args.quality_only else out_path):
        golden_path = os.path.join("config", "golden", "dsl_118184.json")
    if not golden_path and "114045" in os.path.basename(pdf_path if not args.quality_only else out_path):
        golden_path = os.path.join("config", "golden", "dsl_114045.json")
    exit_code = 0 if report.score_pct >= SETTINGS.min_quality_pct else 1
    g_report = None
    acc_report = None

    if golden_path and os.path.isfile(golden_path):
        import json
        with open(golden_path, encoding="utf-8") as fh:
            golden = json.load(fh)
        g_report = score_against_golden(
            block_c, block_d,
            golden.get("block_c", []),
            golden.get("block_d", []),
        )
        print("\n" + "=" * 60)
        print("  GOLDEN ACCURACY (reference tables)")
        print("=" * 60)
        print(f"  Score: {g_report.passed}/{g_report.total} ({g_report.score_pct:.1f}%)")
        if g_report.failures:
            print("  Mismatches:")
            for f in g_report.failures[:20]:
                print(f"    - {f}")
        print(f"  Target: >= {SETTINGS.min_quality_pct:.0f}%")
        if g_report.score_pct < SETTINGS.min_quality_pct:
            exit_code = 1
        acc_report = build_accuracy_report(
            block_c, block_d,
            golden_c=golden.get("block_c", []),
            golden_d=golden.get("block_d", []),
            rules=report,
        )
        if acc_report:
            print("\n  FIELD-LEVEL ACCURACY")
            for line in acc_report.to_summary_lines()[:12]:
                print(f"  {line}")

    if session is not None:
        session.finalize(
            result=result,
            output_path=out_path,
            rules_report=report,
            golden_report=g_report,
            field_details=acc_report.field_details if acc_report else None,
        )
        print(f"\n  Audit logs: logs/{session.stem}/")

    print("=" * 60 + "\n")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
