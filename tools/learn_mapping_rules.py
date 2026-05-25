#!/usr/bin/env python3
"""Learn compile mapping rules from golden JSON + BS OCR (writes YAML)."""
from __future__ import annotations

import json
import sys
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from compile_extraction.bs_components import extract_bs_components

TOL = 0.02
SLS = list(range(1, 18))


def read_pages(prefix: str) -> Dict[int, str]:
    d = ROOT / "data" / f"{prefix}_ocr_debug"
    return {
        i: (d / f"page_{i}.txt").read_text(encoding="utf-8", errors="replace")
        for i in range(1, 20)
        if (d / f"page_{i}.txt").exists()
    }


def match(target: float, val: float) -> bool:
    if target == 0 and val == 0:
        return True
    return abs(target - val) <= max(abs(target), 1) * TOL


def eval_expr(
    comp,
    keys: List[str],
    signs: List[int],
    col: int,
) -> float:
    total = 0.0
    for k, s in zip(keys, signs):
        pair = comp.get(k)
        total += s * pair[col]
    return total


def find_formula(
    comp,
    target_o: float,
    target_c: float,
    keys: List[str],
    max_terms: int = 4,
) -> Optional[dict]:
    for n in range(1, max_terms + 1):
        for combo in combinations(keys, n):
            for signs in _sign_combos(n):
                vo = eval_expr(comp, list(combo), signs, 0)
                vc = eval_expr(comp, list(combo), signs, 1)
                if match(target_o, vo) and match(target_c, vc):
                    return {
                        "add": [combo[i] for i, s in enumerate(signs) if s > 0],
                        "subtract": [combo[i] for i, s in enumerate(signs) if s < 0],
                    }
    return None


def _sign_combos(n: int):
    from itertools import product

    for bits in product([-1, 1], repeat=n):
        yield list(bits)


def main():
    pairs = [
        ("114045", "Balance Sheet of DSL 114045 (1)", "lacs_corporate"),
        ("118184", "Balance Sheet of DSL 118184 (1)", "rupees_schedule"),
    ]
    profiles: Dict[str, dict] = {}

    for co, prefix, profile in pairs:
        pages = read_pages(prefix)
        comp = extract_bs_components(pages)
        assert comp.profile == profile, (co, comp.profile)
        golden = {
            r["sl_no"]: r
            for r in json.load(open(ROOT / f"config/golden/dsl_{co}.json", encoding="utf-8"))[
                "block_d"
            ]
        }
        keys = sorted(comp.values.keys())
        if profile not in profiles:
            profiles[profile] = {"detect_patterns": [], "block_d": {}}
        if profile == "lacs_corporate":
            profiles[profile]["detect_patterns"] = ["amounts in lacs", "amounts m lacs"]
        else:
            profiles[profile]["detect_patterns"] = ["schedule 8", "schedule 9"]

        print(f"\n=== {co} ({profile}) components={len(keys)}")
        for sl in SLS:
            if sl in (4, 7, 11, 15, 16):
                continue
            go = golden[sl]["opening_rs"]
            gc = golden[sl]["closing_rs"]
            if go == 0 and gc == 0:
                continue
            formula = find_formula(comp, go, gc, keys, max_terms=4)
            if formula:
                print(f"  sl{sl} OK {formula}")
                existing = profiles[profile]["block_d"].get(sl)
                if existing is None:
                    profiles[profile]["block_d"][sl] = formula
                elif existing != formula:
                    profiles[profile]["block_d"][sl] = _merge_formulas(existing, formula)
            else:
                print(f"  sl{sl} MISS golden {go}/{gc}")

    out = ROOT / "config" / "compile_mapping_rules.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"profiles": profiles}, fh, indent=2)
    print(f"\nWrote {out}")


def _merge_formulas(a: dict, b: dict) -> dict:
    if a == b:
        return a
    return {"variants": [a, b]}


if __name__ == "__main__":
    main()
