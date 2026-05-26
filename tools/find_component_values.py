#!/usr/bin/env python3
"""Find BS OCR tokens matching compile golden amounts (rule discovery aid)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from schedule_parser import LAKHS_MULTIPLIER, _parse_lakhs_amount, _to_rupees_from_lakhs


def lakhs_in_text(text: str, target_lacs: float, tol: float = 0.02):
    hits = []
    for m in re.finditer(r"[\d,\.]+", text):
        tok = m.group(0)
        v = _parse_lakhs_amount(tok)
        if v <= 0:
            continue
        if abs(v - target_lacs) <= max(target_lacs, 1) * tol:
            start = max(0, m.start() - 40)
            end = min(len(text), m.end() + 40)
            hits.append((v, text[start:end].replace("\n", " ")))
    return hits


def main():
    co = "114045"
    g = json.load(open(ROOT / f"config/golden/dsl_{co}.json", encoding="utf-8"))["block_d"]
    text = "\n".join(
        (ROOT / "data" / f"Balance Sheet of DSL {co} (1)_ocr_debug" / f"page_{i}.txt").read_text(
            encoding="utf-8", errors="replace"
        )
        for i in range(1, 15)
        if (ROOT / "data" / f"Balance Sheet of DSL {co} (1)_ocr_debug" / f"page_{i}.txt").exists()
    )
    for sl in [9, 10, 12, 14]:
        for col in ("closing_rs", "opening_rs"):
            t = g[sl - 1][col] / LAKHS_MULTIPLIER
            print(f"\n=== sl{sl} {col} target {t:.2f} lacs")
            for v, ctx in lakhs_in_text(text, t)[:8]:
                print(f"  {v:.2f} | ...{ctx}...")


if __name__ == "__main__":
    main()
