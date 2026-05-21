"""Extract golden Block C/D from filled Compile Schedule PDF (reference form)."""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from compile_extraction.schema import BLOCK_C_TEMPLATE, BLOCK_D_TEMPLATE, clean_number

_C_ROW_NAMES = {
    1: "land",
    2: "building",
    3: "plant",
    4: "transport",
    5: "computer",
    6: "pollution",
    7: "other",
    8: "sub-total",
    9: "capital work",
    10: "total",
}


def _signed_amount(token: str) -> float:
    t = token.strip().replace(",", "")
    if not t or t in ("-", "0", "00"):
        return 0.0
    neg = t.startswith("-") or t.startswith("(")
    val = clean_number(t.lstrip("-(").rstrip(")"))
    return -val if neg and val else val


def _parse_indian_amounts(line: str) -> List[float]:
    return [
        _signed_amount(x)
        for x in re.findall(r"-?[\d,]+", line)
        if x.strip() not in ("", "-")
    ]


def parse_block_d_from_compile_schedule(text: str) -> List[Dict]:
    """
    Parse Block D from compile schedule OCR (page with 'Block D: Working Capital').
    Format: sl_no | item | opening | closing (tab-separated).
    """
    if "block d" not in text.lower():
        return []
    rows: List[Dict] = []
    name_by_sl = {r["sl_no"]: r["item_name"] for r in BLOCK_D_TEMPLATE}

    merged = text.replace("\ninstitutions", " institutions")
    for line in merged.splitlines():
        ll = line.lower().strip()
        if not ll or ll.startswith("(") or ("sin" in ll[:6] and "items" in ll):
            continue
        m = re.match(
            r"^(\d{1,2})\s+(.+?)\s+(-?[\d,]+)\s+(-?[\d,]+)\s*$",
            line.strip(),
        )
        if m:
            sl = int(m.group(1))
            opening = _signed_amount(m.group(3))
            closing = _signed_amount(m.group(4))
        else:
            parts = re.split(r"\t+", line.strip())
            if len(parts) < 3:
                continue
            try:
                sl = int(float(parts[0].strip()))
            except ValueError:
                continue
            tail = _parse_indian_amounts(line)
            if len(tail) >= 2:
                opening, closing = tail[-2], tail[-1]
            else:
                continue
        if sl < 1 or sl > 17:
            continue
        if sl == 9 and "finished" in ll and not any(r["sl_no"] == 6 for r in rows):
            sl = 6
        rows.append({
            "sl_no": sl,
            "item_name": name_by_sl.get(sl, ""),
            "opening_rs": opening,
            "closing_rs": closing,
        })
    by_sl = {r["sl_no"]: r for r in rows}
    if 13 not in by_sl:
        m13 = re.search(
            r"13\s+Overdraft[^\d]*([\d,]+)\s+([\d,]+)",
            merged,
            re.I | re.S,
        )
        if m13:
            by_sl[13] = {
                "sl_no": 13,
                "item_name": name_by_sl[13],
                "opening_rs": clean_number(m13.group(1)),
                "closing_rs": clean_number(m13.group(2)),
            }
    if 2 not in by_sl:
        m2 = re.search(r"^2\s+Fuels[^\d]*0\s+0", merged, re.I | re.M)
        if m2:
            by_sl[2] = {
                "sl_no": 2, "item_name": name_by_sl[2],
                "opening_rs": 0.0, "closing_rs": 0.0,
            }
    return [by_sl[k] for k in sorted(by_sl)]


def parse_block_c_from_compile_schedule(text: str) -> List[Dict]:
    """
    Parse Block C rows from compile schedule OCR (Block C: Fixed Assets page).
    Uses row header lines (1 Land, 2 Building, ...) and trailing numeric groups.
    """
    if "block c" not in text.lower():
        return []
    rows: List[Dict] = []
    name_map = {r["sl_no"]: r["asset_type"] for r in BLOCK_C_TEMPLATE}

    buf = ""
    current_sl: Optional[int] = None
    for line in text.splitlines():
        ll = line.lower()
        m = re.match(r"^(\d{1,2})\s+(Land|Building|Plant|Transport|Computer|Pollution|Others|Sub|Capital|Total)", line, re.I)
        if m:
            if current_sl is not None and buf:
                row = _block_c_row_from_buffer(current_sl, buf, name_map)
                if row:
                    rows.append(row)
            current_sl = int(m.group(1))
            buf = line
        elif current_sl is not None:
            buf += " " + line
    if current_sl is not None and buf:
        row = _block_c_row_from_buffer(current_sl, buf, name_map)
        if row:
            rows.append(row)
    return rows


def _block_c_row_from_buffer(
    sl: int, buf: str, name_map: Dict[int, str]
) -> Optional[Dict]:
    nums = _parse_indian_amounts(buf)
    if sl in (8, 10) and len(nums) < 4:
        return None
    if sl not in (8, 10) and len(nums) < 6:
        return None
    row = {
        "sl_no": sl,
        "asset_type": name_map.get(sl, ""),
        "gross_opening": 0.0,
        "gross_addition_reval": 0.0,
        "gross_addition_actual": 0.0,
        "gross_deduction": 0.0,
        "gross_closing": 0.0,
        "dep_up_to_beginning": 0.0,
        "dep_provided_during_year": 0.0,
        "dep_adjustment": 0.0,
        "dep_up_to_end": 0.0,
        "net_opening": 0.0,
        "net_closing": 0.0,
    }
    if sl in (8, 10):
        if len(nums) >= 4:
            row["net_opening"] = nums[-2]
            row["net_closing"] = nums[-1]
            row["gross_opening"] = nums[0] if len(nums) > 2 else 0
            row["gross_closing"] = nums[-3] if len(nums) > 3 else 0
        return row
    if len(nums) >= 11:
        row["gross_opening"] = nums[0]
        row["gross_addition_reval"] = nums[1] if len(nums) > 1 else 0
        row["gross_addition_actual"] = nums[2] if len(nums) > 2 else 0
        row["gross_deduction"] = nums[3] if len(nums) > 3 else 0
        row["gross_closing"] = nums[4] if len(nums) > 4 else 0
        row["dep_up_to_beginning"] = nums[5] if len(nums) > 5 else 0
        row["dep_provided_during_year"] = nums[6] if len(nums) > 6 else 0
        row["dep_adjustment"] = nums[7] if len(nums) > 7 else 0
        row["dep_up_to_end"] = nums[8] if len(nums) > 8 else 0
        row["net_opening"] = nums[9] if len(nums) > 9 else 0
        row["net_closing"] = nums[10] if len(nums) > 10 else 0
    elif len(nums) >= 4:
        row["gross_opening"] = nums[0]
        row["gross_closing"] = nums[1]
        row["net_opening"] = nums[-2]
        row["net_closing"] = nums[-1]
        row["dep_up_to_end"] = max(0.0, row["gross_closing"] - row["net_closing"])
        row["dep_up_to_beginning"] = max(0.0, row["gross_opening"] - row["net_opening"])
    else:
        return None
    return row


def extract_golden_from_compile_pdf(pdf_path: str, dpi: int = 300) -> Tuple[List[Dict], List[Dict]]:
    """OCR compile schedule PDF and return (block_c, block_d) golden rows."""
    import fitz
    import numpy as np
    from PIL import Image
    from run_agentic_pipeline import _rapidocr_extract

    block_c: List[Dict] = []
    block_d: List[Dict] = []
    doc = fitz.open(pdf_path)
    for i in range(doc.page_count):
        pix = doc[i].get_pixmap(dpi=dpi)
        img = np.array(Image.frombytes("RGB", [pix.width, pix.height], pix.samples))
        text = _rapidocr_extract(img)
        if "block c" in text.lower() and not block_c:
            block_c = parse_block_c_from_compile_schedule(text)
        if "block d" in text.lower() and not block_d:
            block_d = parse_block_d_from_compile_schedule(text)
    doc.close()
    return block_c, block_d
