#!/usr/bin/env python3
"""Export 100% reference Excel from filled Compile Schedule PDF."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from compile_extraction.excel import write_excel
from compile_extraction.golden_extractor import extract_golden_from_compile_pdf
from run_agentic_pipeline import _compute_derived_rows_c, _compute_derived_rows_d
from compile_extraction.schema import merge_with_template, BLOCK_C_TEMPLATE, BLOCK_D_TEMPLATE

ROOT = Path(__file__).resolve().parent


def main() -> None:
    compile_pdf = ROOT / "data" / "Compile schedule DSL 114045 (1).pdf"
    out = ROOT / "outputs" / "Compile_DSL_114045_reference.xlsx"
    golden_path = ROOT / "config" / "golden" / "dsl_114045.json"

    if not compile_pdf.is_file():
        print(f"Missing: {compile_pdf}")
        sys.exit(1)

    gc, gd = extract_golden_from_compile_pdf(str(compile_pdf))
    block_c = merge_with_template({"block_c": gc}, BLOCK_C_TEMPLATE, "block_c", "sl_no")
    block_d = merge_with_template({"block_d": gd}, BLOCK_D_TEMPLATE, "block_d", "sl_no")
    block_c = _compute_derived_rows_c(block_c)
    block_d = _compute_derived_rows_d(block_d)
    write_excel(block_c, block_d, str(out))

    golden_path.parent.mkdir(parents=True, exist_ok=True)
    golden_path.write_text(
        json.dumps(
            {"company": "DSL 114045", "psl": "32742", "block_c": block_c, "block_d": block_d},
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Reference Excel: {out}")
    print(f"Golden JSON:   {golden_path}")


if __name__ == "__main__":
    main()
