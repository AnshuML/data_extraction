#!/usr/bin/env python3
"""Compare compile sheet Excel to golden reference JSON."""
from __future__ import annotations

import argparse
import json
import os
import sys

import pandas as pd

from compile_extraction.config import SETTINGS
from compile_extraction.excel import (
    normalize_block_c_from_excel,
    normalize_block_d_from_excel,
)
from compile_extraction.quality import score_against_golden, score_extraction


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify compile sheet Excel")
    parser.add_argument("excel", help="Path to output Excel")
    parser.add_argument(
        "--golden",
        required=True,
        help="Golden JSON (e.g. config/golden/dsl_118184.json)",
    )
    args = parser.parse_args()

    excel = os.path.abspath(args.excel)
    golden_path = os.path.abspath(args.golden)
    if not os.path.isfile(excel):
        print(f"Excel not found: {excel}")
        sys.exit(1)
    if not os.path.isfile(golden_path):
        print(f"Golden not found: {golden_path}")
        sys.exit(1)

    df_c = pd.read_excel(excel, sheet_name="Block C - Fixed Assets")
    df_d = pd.read_excel(excel, sheet_name="Block D - Working Capital")
    block_c = normalize_block_c_from_excel(df_c.to_dict("records"))
    block_d = normalize_block_d_from_excel(df_d.to_dict("records"))

    with open(golden_path, encoding="utf-8") as fh:
        golden = json.load(fh)

    rules = score_extraction(block_c, block_d)
    acc = score_against_golden(
        block_c, block_d,
        golden.get("block_c", []),
        golden.get("block_d", []),
    )

    print(f"Rules:  {rules.score_pct:.1f}% ({rules.passed}/{rules.total})")
    print(f"Golden: {acc.score_pct:.1f}% ({acc.passed}/{acc.total})")
    if acc.failures:
        print("Mismatches:")
        for f in acc.failures[:25]:
            print(f"  - {f}")
    ok = (
        rules.score_pct >= SETTINGS.min_quality_pct
        and acc.score_pct >= SETTINGS.min_quality_pct
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
