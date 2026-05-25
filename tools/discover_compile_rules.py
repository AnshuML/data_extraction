#!/usr/bin/env python3
"""Discover BS→compile Block D mapping from golden JSON + BS OCR debug folders."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from schedule_parser import parse_block_d_from_lakhs_notes, parse_block_d_from_text


def load_golden(name: str):
    return json.load(open(ROOT / "config" / "golden" / f"{name}.json", encoding="utf-8"))["block_d"]


def read_pages(prefix: str):
    d = ROOT / "data" / f"{prefix}_ocr_debug"
    return {
        i: (d / f"page_{i}.txt").read_text(encoding="utf-8", errors="replace")
        for i in range(1, 20)
        if (d / f"page_{i}.txt").exists()
    }


def main():
    pairs = [
        ("114045", "Balance Sheet of DSL 114045 (1)"),
        ("118184", "Balance Sheet of DSL 118184 (1)"),
    ]
    for co, prefix in pairs:
        pages = read_pages(prefix)
        full = "\n".join(pages.values())
        g = {r["sl_no"]: r for r in load_golden(f"dsl_{co}")}
        lakhs_rows = parse_block_d_from_lakhs_notes(full) or []
        sched_rows = []
        for t in pages.values():
            sched_rows.extend(parse_block_d_from_text(t))
        lk = {r["sl_no"]: r for r in lakhs_rows}
        sc = {r["sl_no"]: r for r in sched_rows}
        print(f"=== DSL {co}")
        for sl in range(1, 18):
            if sl not in g:
                continue
            go, gc = int(g[sl]["opening_rs"]), int(g[sl]["closing_rs"])
            tags = []
            for tag, m in [("lakhs", lk), ("sched", sc)]:
                if sl in m:
                    o, c = int(m[sl]["opening_rs"]), int(m[sl]["closing_rs"])
                    ok_o = abs(o - go) <= max(go, 1) * 0.02
                    ok_c = abs(c - gc) <= max(gc, 1) * 0.02
                    tags.append(f"{tag}={o}/{c} match={ok_o}/{ok_c}")
            print(f"  sl{sl:2d} golden {go:>12}/{gc:>12}  {' | '.join(tags) if tags else '-'}")


if __name__ == "__main__":
    main()
