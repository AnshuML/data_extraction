#!/usr/bin/env python3
"""Compare compile-mapped Block D vs golden JSON."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from compile_extraction.compile_mapper import apply_compile_mapping
from compile_extraction.schema import BLOCK_D_TEMPLATE, merge_with_template
from run_agentic_pipeline import _compute_derived_rows_d


def read_pages(prefix: str):
    d = ROOT / "data" / f"{prefix}_ocr_debug"
    return {
        i: (d / f"page_{i}.txt").read_text(encoding="utf-8", errors="replace")
        for i in range(1, 25)
        if (d / f"page_{i}.txt").exists()
    }


def main():
    pairs = [
        ("114045", "Balance Sheet of DSL 114045 (1)"),
        ("118184", "Balance Sheet of DSL 118184 (1)"),
    ]
    for co, prefix in pairs:
        pages = read_pages(prefix)
        block_d = merge_with_template(
            {"block_d": apply_compile_mapping(pages)},
            BLOCK_D_TEMPLATE,
            "block_d",
            "sl_no",
        )
        block_d = _compute_derived_rows_d(block_d)
        golden = {
            r["sl_no"]: r
            for r in json.load(open(ROOT / f"config/golden/dsl_{co}.json", encoding="utf-8"))[
                "block_d"
            ]
        }
        ok = tot = 0
        print(f"\n=== DSL {co}")
        for sl in range(1, 18):
            if sl not in golden:
                continue
            row = next((r for r in block_d if r["sl_no"] == sl), None)
            if not row:
                continue
            for col in ("opening_rs", "closing_rs"):
                g = golden[sl][col]
                e = row[col]
                tot += 1
                if g == 0 and e == 0:
                    ok += 1
                    continue
                if abs(e - g) <= max(abs(g), 1) * 0.02:
                    ok += 1
                    st = "OK"
                else:
                    st = "MISS"
                if st == "MISS" or sl >= 8:
                    print(
                        f"  sl{sl} {col[-7:]} {st} golden={int(g):>12} mapped={int(e):>12}"
                    )
        print(f"  Match: {ok}/{tot} ({100*ok/tot:.1f}%)")


if __name__ == "__main__":
    main()
