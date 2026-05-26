#!/usr/bin/env python3
"""Generate accuracy report JSON from Excel vs golden."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from compile_extraction.excel import (
    normalize_block_c_from_excel,
    normalize_block_d_from_excel,
)
from compile_extraction.quality import score_against_golden, score_extraction
from compile_extraction.reports import build_accuracy_report

ROOT = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("excel")
    parser.add_argument("--golden", required=True)
    parser.add_argument("-o", default="")
    args = parser.parse_args()

    excel = Path(args.excel)
    golden_path = Path(args.golden)
    out = Path(args.o) if args.o else ROOT / "logs" / excel.stem / "accuracy_report.json"

    with open(golden_path, encoding="utf-8") as fh:
        golden = json.load(fh)

    df_c = pd.read_excel(excel, sheet_name="Block C - Fixed Assets")
    df_d = pd.read_excel(excel, sheet_name="Block D - Working Capital")
    block_c = normalize_block_c_from_excel(df_c.to_dict("records"))
    block_d = normalize_block_d_from_excel(df_d.to_dict("records"))

    rules = score_extraction(block_c, block_d)
    g = score_against_golden(
        block_c, block_d,
        golden.get("block_c", []),
        golden.get("block_d", []),
    )
    acc = build_accuracy_report(
        block_c, block_d,
        golden_c=golden.get("block_c", []),
        golden_d=golden.get("block_d", []),
        rules=rules,
    )

    report = {
        "excel": str(excel),
        "golden": str(golden_path),
        "rules_pct": rules.score_pct,
        "golden_pct": g.score_pct,
        "field_level_pct": acc.field_level_pct,
        "block_c_table_pct": acc.table_c_pct,
        "block_d_table_pct": acc.table_d_pct,
        "mapping_pct": acc.mapping_pct,
        "mismatches": acc.mismatches,
        "missing": acc.missing,
        "field_details": [vars(f) for f in acc.field_details],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in report if k != "field_details"}, indent=2))
    sys.exit(0 if acc.field_level_pct >= 95 else 1)


if __name__ == "__main__":
    main()
