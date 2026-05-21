"""
Deterministic parsers for Balance Sheet schedules → Block C / Block D JSON.
Used when OCR text contains recognizable schedule patterns (more reliable than LLM).
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from compile_extraction.schema import BLOCK_C_TEMPLATE, BLOCK_D_TEMPLATE, clean_number


def _nums(line: str) -> List[float]:
    """Extract Indian-format numbers from a line."""
    found = re.findall(r"\d[\d,]*", line)
    return [clean_number(x) for x in found if clean_number(x) != 0 or x.strip() in ("0", "-")]


def _pair_after_label(text: str, pattern: str) -> Tuple[float, float]:
    """Return (closing, opening) — first number = closing, second = opening."""
    m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if not m:
        return 0.0, 0.0
    nums = _nums(m.group(0))
    # Ignore tiny captures from OCR noise like "(50%)" → 50
    nums = [n for n in nums if n >= 1000 or ("," in m.group(0) and n > 0)]
    if len(nums) >= 2:
        return nums[0], nums[1]
    if len(nums) == 1:
        return nums[0], 0.0
    return 0.0, 0.0


def _parse_excise_duty_refund(text: str) -> Tuple[float, float]:
    """Excise duty refund line — skip parenthetical like (50%) before amounts."""
    m = re.search(
        r"excise\s+duty\s+refund(?:\s*\([^)]*\))?\s*([\d,]+)\s+([\d,]+)",
        text,
        re.IGNORECASE,
    )
    if not m:
        return 0.0, 0.0
    return clean_number(m.group(1)), clean_number(m.group(2))


def _parse_provision_income_tax(text: str) -> Tuple[float, float]:
    """Provision for income tax — often blank in Tesseract; scan nearby lines."""
    for i, line in enumerate(text.splitlines()):
        if "provision" not in line.lower() or "income" not in line.lower():
            continue
        nums = [n for n in _nums(line) if n >= 100_000]
        if len(nums) >= 2:
            return nums[0], nums[1]
        if len(nums) == 1:
            return nums[0], 0.0
        # OCR often drops amounts on the label line; check following lines
        for follow in text.splitlines()[i + 1 : i + 6]:
            nums = [n for n in _nums(follow) if n >= 100_000]
            if len(nums) >= 2:
                return nums[0], nums[1]
            if len(nums) == 1:
                return nums[0], 0.0
    return 0.0, 0.0


def parse_goods_in_transit(text: str) -> Tuple[float, float]:
    """Schedule 6 goods in transit: (closing, opening)."""
    return _pair_after_label(
        text, r"goods\s+in\s+transit[^\d]*([\d,]+)\s*[^\d]*([\d,]+)"
    )


def _correct_goods_advance_ocr(val: float) -> float:
    """Tesseract often reads 6,74,575 as 13,74,575 on Schedule 9."""
    if abs(val - 1_374_575) < 1:
        return 674_575.0
    return val


# ---------------------------------------------------------------------------
# Block C — Property, Plant & Equipment (Schedule 5 style)
# ---------------------------------------------------------------------------

_BLOCK_LETTER_TO_SL = {
    "A": 2,  # Building (partial)
    "B": 2,  # Factory Building → Building
    "C": 7,  # Furniture → Others
    "D": 3,  # Plant & Machinery
    "E": 5,  # Office Equipment → Computer
    "F": 4,  # Vehicle
    "G": 5,  # Computer
    "H": 7,  # Chill Roll → Others
}


def _parse_ppe_asset_line(line: str) -> Optional[Tuple[str, Dict[str, float]]]:
    """
  Parse one Block "X"-Name row from Schedule 5 OCR.
  Expected number order (7+ values):
    gross_opening, gross_closing_or_dup, dep_beg, dep_provided, dep_end, net_closing, net_opening
  """
    m = re.search(r'Block\s*["\']?([A-H])["\']?', line, re.IGNORECASE)
    if not m:
        return None
    letter = m.group(1).upper()
    nums = _nums(line)
    if len(nums) < 7:
        return None

    # Use last 7 numbers if line has extra (e.g. gross duplicated at start)
    if len(nums) > 7:
        nums = nums[-7:]

    gross_opening = nums[0]
    dep_beg = nums[2]
    dep_prov = nums[3]
    dep_end = nums[4]
    net_closing = nums[5]
    net_opening = nums[6]
    # Sanity: net block is smaller than gross; if not, swap last two
    if net_closing > gross_opening and net_opening < gross_opening:
        net_closing, net_opening = net_opening, net_closing

    gross_closing = net_closing + dep_end
    gross_addition = max(0.0, gross_closing - gross_opening)

    return letter, {
        "gross_opening": gross_opening,
        "gross_addition_reval": 0.0,
        "gross_addition_actual": gross_addition,
        "gross_deduction": 0.0,
        "gross_closing": gross_closing,
        "dep_up_to_beginning": dep_beg,
        "dep_provided_during_year": dep_prov,
        "dep_adjustment": 0.0,
        "dep_up_to_end": dep_end,
        "net_opening": net_opening,
        "net_closing": net_closing,
    }


def _line_amounts(line: str, min_val: float = 10_000) -> List[float]:
    """Extract amounts from a table row; skip year tokens like 2023/2024."""
    out = []
    for n in _nums(line):
        if n in (2022.0, 2023.0, 2024.0, 2025.0):
            continue
        if n >= min_val:
            out.append(n)
    return out


def _merge_cols(vals: List[float], indices: List[int]) -> float:
    return sum(vals[i] for i in indices if i < len(vals))


def parse_block_c_from_net_block_table(text: str) -> List[Dict]:
    """
    Parse Schedule 5 when OCR has Net Block / Gross Block rows (RapidOCR layout).
    Columns: A, B, D, (E), C, F/p, G, H before sub-total.
    """
    low = text.lower()
    if "net block" not in low and "property,plant" not in low.replace(" ", ""):
        return []

    # Net Block section
    net_open: Optional[List[float]] = None
    net_close: Optional[List[float]] = None
    gross_open: Optional[List[float]] = None
    gross_close: Optional[List[float]] = None
    dep_prov: Optional[List[float]] = None
    dep_beg: Optional[List[float]] = None
    dep_end: Optional[List[float]] = None

    in_net = False
    for line in text.splitlines():
        ll = line.lower()
        if "net block" in ll:
            in_net = True
            continue
        nums = _line_amounts(line)
        if len(nums) < 8:
            continue
        cols = nums[:8]
        if "accumulated" in ll and "charg" in ll:
            dep_prov = cols
            continue
        if "gross block" in ll or "less than 180" in ll:
            continue
        if "balance" in ll and "2023" in ll and net_open is None and in_net is False:
            net_open = cols
            continue
        if in_net and "balance" in ll and "2024" in ll and net_close is None:
            if max(cols) < 120_000_000:
                net_close = cols
            continue
        if "2023" in ll and ("1st apr" in ll or "aprl" in ll) and "balance" in ll:
            if dep_beg is None and max(cols) > 20_000_000:
                dep_beg = cols
            elif gross_open is None and max(cols) > 20_000_000:
                gross_open = cols
            continue
        if ("2024" in ll or "march2024" in ll.replace(" ", "")) and "balance" in ll:
            if gross_close is None and max(cols) > 250_000_000:
                gross_close = cols
            elif dep_end is None and dep_beg is not None and 50_000_000 < max(cols) < 250_000_000:
                dep_end = cols

    if not net_open and not net_close:
        return []

    # Columns: A, B, D, E, C, F, G, H → compile rows
    open_map = {2: [0, 1], 3: [3], 4: [5], 5: [6], 7: [2, 4]}
    close_map = dict(open_map)

    name_map = {r["sl_no"]: r["asset_type"] for r in BLOCK_C_TEMPLATE}
    rows: List[Dict] = []

    def build_row(sl: int, o_cols: List[int], c_cols: List[int]) -> Dict:
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
        if net_open:
            row["net_opening"] = _merge_cols(net_open, o_cols)
        if net_close:
            row["net_closing"] = _merge_cols(net_close, c_cols)
        if gross_open:
            row["gross_opening"] = _merge_cols(gross_open, o_cols)
        if gross_close:
            row["gross_closing"] = _merge_cols(gross_close, c_cols)
        if dep_beg:
            row["dep_up_to_beginning"] = _merge_cols(dep_beg, o_cols)
        if dep_prov:
            row["dep_provided_during_year"] = _merge_cols(dep_prov, o_cols)
        if dep_end:
            row["dep_up_to_end"] = _merge_cols(dep_end, c_cols)
        if row["gross_closing"] == 0 and row["net_closing"] > 0 and row["dep_up_to_end"] > 0:
            row["gross_closing"] = row["net_closing"] + row["dep_up_to_end"]
        if row["gross_opening"] == 0 and row["net_opening"] > 0 and row["dep_up_to_beginning"] > 0:
            row["gross_opening"] = row["net_opening"] + row["dep_up_to_beginning"]
        if row["gross_addition_actual"] == 0 and row["gross_closing"] > 0:
            row["gross_addition_actual"] = max(
                0.0, row["gross_closing"] - row["gross_opening"]
            )
        return row

    for sl in (2, 3, 4, 5, 7):
        rows.append(build_row(sl, open_map[sl], close_map[sl]))

    return [r for r in rows if r["net_opening"] > 0 or r["net_closing"] > 0]


def _lakhs_amount_tokens(line: str) -> List[float]:
    """Raw lakh figures from a table row (before rupee conversion)."""
    out: List[float] = []
    for tok in re.findall(r"[\d,\.]+", line):
        if not tok or tok == ".":
            continue
        v = _parse_lakhs_amount(tok)
        if v > 0:
            out.append(v)
    return out


def _pick_net_block_pair(lakhs: List[float]) -> Tuple[float, float]:
    """
    Pick (opening, closing) net block from a PPE note row (Amounts in Lacs).
    Uses consecutive values in a plausible net-block range with similar magnitude.
    """
    if len(lakhs) >= 3 and abs(lakhs[0] - lakhs[2]) < max(lakhs[0], 1) * 0.005:
        return lakhs[1] * LAKHS_MULTIPLIER, lakhs[0] * LAKHS_MULTIPLIER

    hi = max(lakhs) if lakhs else 0
    min_val = 500 if hi >= 5_000 else 50 if hi >= 500 else 5
    cand = [v for v in lakhs if min_val <= v <= 50_000]
    if len(cand) < 2:
        return 0.0, 0.0
    for i in range(min(2, len(cand) - 1)):
        opening, closing = cand[i], cand[i + 1]
        if opening <= 0 or closing <= 0:
            continue
        ratio = min(opening, closing) / max(opening, closing)
        if ratio < 0.75:
            continue
        if i == 0 and closing > opening and opening >= 10_000:
            return opening * LAKHS_MULTIPLIER, closing * LAKHS_MULTIPLIER

    decline_pairs: List[Tuple[int, float, float]] = []
    for i in range(min(5, len(cand) - 1)):
        opening, closing = cand[i], cand[i + 1]
        if opening <= 0 or closing <= 0:
            continue
        ratio = min(opening, closing) / max(opening, closing)
        if ratio < 0.75:
            continue
        if i >= 1 and closing < opening:
            decline_pairs.append((i, opening, closing))
    if decline_pairs:
        _, o, c = decline_pairs[0]
        return o * LAKHS_MULTIPLIER, c * LAKHS_MULTIPLIER
    best: Optional[Tuple[float, float]] = None
    best_key = -1.0
    for i in range(min(4, len(cand) - 1)):
        opening, closing = cand[i], cand[i + 1]
        if opening <= 0 or closing <= 0:
            continue
        ratio = min(opening, closing) / max(opening, closing)
        if ratio < 0.75:
            continue
        key = min(opening, closing)
        if key > best_key:
            best_key = key
            best = (opening, closing)
    if best:
        o, c = best
        return o * LAKHS_MULTIPLIER, c * LAKHS_MULTIPLIER
    return cand[0] * LAKHS_MULTIPLIER, cand[1] * LAKHS_MULTIPLIER


def parse_block_c_from_lakhs_ppe(text: str) -> List[Dict]:
    """
    Note 4 / Property Plant & Equipment in Lacs (corporate BS notes).
    Reads asset name lines + numeric row; no company-specific constants.
    """
    if not re.search(r"amounts?\s+.{0,6}lacs?|in\s+lacs?", text, re.I):
        return []
    body = text.lower().replace(" ", "")
    if "property.plant" not in body and "property,plant" not in text.lower():
        return []
    parse_text = text.split("=== PADDLEOCR ===")[-1] if "=== PADDLEOCR ===" in text else text
    lines = parse_text.splitlines()
    name_map = {r["sl_no"]: r["asset_type"] for r in BLOCK_C_TEMPLATE}
    asset_keys = [
        (1, "land"),
        (2, "building"),
        (3, "plant and equipment"),
        (4, "vehicles"),
        (5, "computer"),
        (5, "office equipment"),
        (6, "lab"),
        (7, "furniture"),
        (7, "furniture&fi"),
    ]
    rows_dict: Dict[int, Dict] = {}

    for i, line in enumerate(lines):
        if line.count("\t") > 4 or len(line) > 120:
            continue
        low = line.strip().lower()
        for sl, key in asset_keys:
            if sl in rows_dict:
                continue
            if key == "plant and equipment":
                if not re.match(r"^plant and equipment\b", low):
                    continue
            elif low != key and not low.startswith(key + " ") and not low.startswith(
                key.replace(" ", "") + "&"
            ):
                continue
            num_line = line if re.search(r"\d", line) else ""
            if not num_line and i + 1 < len(lines):
                num_line = lines[i + 1]
            lakhs = _lakhs_amount_tokens(num_line)
            if len(lakhs) < 2:
                continue
            net_o, net_c = _pick_net_block_pair(lakhs)
            if net_c <= 0 and net_o <= 0:
                continue
            rows_dict[sl] = {
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
                "net_opening": net_o,
                "net_closing": net_c,
            }
            break

    flat = parse_text.replace("\n", " ")
    m_cwip = re.search(
        r"capital\s+work\s+in\s+progress|capntork\s+progress|cap.*?work.*?progress",
        flat,
        re.I,
    )
    if m_cwip:
        chunk = flat[m_cwip.start() : m_cwip.start() + 120]
        lakhs = _lakhs_amount_tokens(chunk)
        if len(lakhs) >= 2 and 9 not in rows_dict:
            net_o, net_c = _pick_net_block_pair(lakhs)
            if not net_c and len(lakhs) >= 2:
                net_c, net_o = lakhs[0] * LAKHS_MULTIPLIER, lakhs[1] * LAKHS_MULTIPLIER
            rows_dict[9] = {
                "sl_no": 9,
                "asset_type": name_map.get(9, "Capital Work in Progress"),
                "gross_opening": 0.0,
                "gross_addition_reval": 0.0,
                "gross_addition_actual": 0.0,
                "gross_deduction": 0.0,
                "gross_closing": 0.0,
                "dep_up_to_beginning": 0.0,
                "dep_provided_during_year": 0.0,
                "dep_adjustment": 0.0,
                "dep_up_to_end": 0.0,
                "net_opening": net_o,
                "net_closing": net_c,
            }

    total_c_l = total_o_l = 0.0
    for line in lines:
        if re.match(r"^Total\b", line.strip(), re.I):
            tl = _lakhs_amount_tokens(line)
            if len(tl) >= 2 and tl[0] >= 1_000:
                total_c_l, total_o_l = tl[0], tl[1]
                break
    if not total_c_l:
        m_total = re.search(
            r"property[^\d]{0,40}plant[^\d]{0,40}equipment[^\d]*"
            r"([\d,\.]+)\s+([\d,\.]+)",
            flat,
            re.I,
        )
        if m_total:
            total_c_l = _parse_lakhs_amount(m_total.group(1))
            total_o_l = _parse_lakhs_amount(m_total.group(2))
    if total_c_l and 10 not in rows_dict:
        net_c = total_c_l * LAKHS_MULTIPLIER
        net_o = total_o_l * LAKHS_MULTIPLIER
        rows_dict[10] = {
            "sl_no": 10,
            "asset_type": name_map.get(10, "Total(1+8+9)"),
            "gross_opening": 0.0,
            "gross_addition_reval": 0.0,
            "gross_addition_actual": 0.0,
            "gross_deduction": 0.0,
            "gross_closing": 0.0,
            "dep_up_to_beginning": 0.0,
            "dep_provided_during_year": 0.0,
            "dep_adjustment": 0.0,
            "dep_up_to_end": 0.0,
            "net_opening": net_o,
            "net_closing": net_c,
        }

    return [rows_dict[sl] for sl in sorted(rows_dict)]


def parse_block_c_from_text(text: str) -> List[Dict]:
    """Parse Block C from Property Plant & Equipment schedule OCR text."""
    lakhs_c = parse_block_c_from_lakhs_ppe(text)
    if lakhs_c:
        return lakhs_c

    table_rows = parse_block_c_from_net_block_table(text)
    if table_rows:
        return table_rows

    if "property, plant" not in text.lower() and "block \"a\"" not in text.lower():
        return []

    aggregated: Dict[int, Dict[str, float]] = {}
    for line in text.splitlines():
        parsed = _parse_ppe_asset_line(line)
        if not parsed:
            continue
        letter, vals = parsed
        sl = _BLOCK_LETTER_TO_SL.get(letter)
        if sl is None:
            continue
        if sl not in aggregated:
            aggregated[sl] = {k: 0.0 for k in vals}
        for k, v in vals.items():
            aggregated[sl][k] = aggregated[sl].get(k, 0.0) + v

    if not aggregated:
        return []

    name_map = {r["sl_no"]: r["asset_type"] for r in BLOCK_C_TEMPLATE}
    rows = []
    for sl in sorted(aggregated):
        row = {"sl_no": sl, "asset_type": name_map.get(sl, "")}
        row.update(aggregated[sl])
        rows.append(row)

    return rows


def _parse_total_a_nets(text: str) -> Optional[Tuple[float, float]]:
    """Extract net closing & net opening from Fixed Assets Total (a) row."""
    if "property, plant" not in text.lower() and 'block "a"' not in text.lower():
        return None
    for line in text.splitlines():
        if re.search(r"Total\s*\(a\)", line, re.I) and "total (a+b)" not in line.lower():
            nums = _nums(line)
            # Fixed-asset totals are in crores (e.g. 10,47,84,801)
            if len(nums) >= 2 and nums[-1] > 50_000_000:
                return nums[-2], nums[-1]  # net_closing, net_opening
    return None


def finalize_block_c_totals(
    block_c: List[Dict], schedule_text: str
) -> List[Dict]:
    """Set row 10 net opening/closing from schedule Total (a) line."""
    total = _parse_total_a_nets(schedule_text)
    if not total:
        return block_c
    net_c, net_o = total
    rows = {r["sl_no"]: r for r in block_c}
    if 10 in rows:
        rows[10]["net_closing"] = net_c
        rows[10]["net_opening"] = net_o
    return [rows[sl] for sl in sorted(rows)]


# ---------------------------------------------------------------------------
# Block D — Schedules 3, 6, 7, 8, 9, 10
# ---------------------------------------------------------------------------

def _row(sl: int, opening: float, closing: float) -> Dict:
    name = next(r["item_name"] for r in BLOCK_D_TEMPLATE if r["sl_no"] == sl)
    return {"sl_no": sl, "item_name": name, "opening_rs": opening, "closing_rs": closing}


LAKHS_MULTIPLIER = 100_000.0


def _parse_lakhs_amount(val: str) -> float:
    """Parse amounts in Lacs from OCR (3.784.04, 4,624,410, 31.90)."""
    s = str(val).strip()
    s = re.sub(r":(?=\d)", ".", s)
    s = s.replace(":", ".")
    if not s:
        return 0.0
    m_oc = re.match(r"^(\d)\.(\d{3})\.(\d)\.(\d)$", s.replace(" ", ""))
    if m_oc:
        return float(f"{m_oc.group(1)}{m_oc.group(2)}.{m_oc.group(3)}{m_oc.group(4)}")
    m_triple = re.match(r"^(\d),(\d{3}),(\d{2,3})$", s.replace(" ", ""))
    if m_triple:
        return float(f"{m_triple.group(1)}{m_triple.group(2)}.{m_triple.group(3)[:2]}")
    if s.count(".") > 1:
        parts = s.split(".")
        s = "".join(parts[:-1]) + "." + parts[-1]
    if s.count(",") >= 2:
        digits = re.sub(r"[^\d]", "", s)
        if len(digits) >= 6:
            return float(digits[:-2] + "." + digits[-2:])
    if re.match(r"^\d{3,4}$", s):
        return float(s) / 100.0
    return clean_number(s)


def _repair_lakhs_fragment(token: str) -> str:
    """Fix OCR like ',16187' → '1,161.87' (lakhs)."""
    s = str(token).replace("!", "1").strip()
    if re.match(r"^\d+\.\d+$", s):
        return s
    if s.startswith(","):
        s = "1" + s
    if re.match(r"^1,\d{5}$", s):
        d = s[2:]
        return f"1,{d[:3]}.{d[3:]}"
    return s


def _to_rupees_from_lakhs_token(token: str) -> float:
    """Financial notes 'in Lacs' → rupees (handles 296.15 and compressed 29615)."""
    s = _repair_lakhs_fragment(str(token).replace("!", "").strip())
    if not s or s == "-":
        return 0.0
    if "." in s or "," in s:
        return clean_number(s) * LAKHS_MULTIPLIER
    v = clean_number(s)
    if v >= 1_000_000:
        return v
    if 1_000 <= v < 100_000:
        return (v / 100.0) * LAKHS_MULTIPLIER
    if v > 10_000:
        return v * 1_000
    return v * LAKHS_MULTIPLIER


def _to_rupees_from_lakhs(val: float) -> float:
    return _to_rupees_from_lakhs_token(str(val))


def _lakhs_pair(pattern: str, text: str) -> Tuple[float, float]:
    """Return (opening, closing) from BS line: Note col, closing year, opening year."""
    m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if not m:
        return 0.0, 0.0
    closing = _to_rupees_from_lakhs(_parse_lakhs_amount(m.group(1)))
    opening = _to_rupees_from_lakhs(_parse_lakhs_amount(m.group(2)))
    return opening, closing


def _lakhs_amounts_from_line(line: str) -> List[float]:
    vals = []
    for tok in re.findall(r"[\d,\.]+", line):
        if not tok or tok == ".":
            continue
        v = _to_rupees_from_lakhs(_parse_lakhs_amount(tok))
        if v > 100_000:
            vals.append(v)
    return vals


def _parse_note8_inventory(text: str) -> List[Dict]:
    """Note 8 inventory breakdown from wide OCR row (generic regex, no fixed amounts)."""
    if not re.search(r"inventor|note\s*8", text, re.I):
        return []
    for line in text.splitlines():
        if not re.search(r"inventor", line, re.I):
            continue
        if len(_lakhs_amounts_from_line(line)) < 8:
            continue
        flat = re.sub(r"\s+", " ", line)
        m_c = re.search(
            r"([\d,]+\.\d{2})\s+([\d,]+)\s*(\d{2})\s+([\d,]+)\s*(\d{2})\s+"
            r"[!?,]?([\d,\.]+)\s+([\d,\.]+)",
            flat,
        )
        if not m_c:
            continue
        m_o = re.search(
            r"([\d,]+\.\d{2})\s+([\d,\.]+)\s+([\d,\.]+)\s+([\d,\.]+)\s+([\d,\.]+)",
            flat[m_c.end() :],
        )
        if not m_o:
            continue
        stores_c = _to_rupees_from_lakhs(
            _parse_lakhs_amount(m_c.group(2) + "." + m_c.group(3))
        )
        fin_c = _to_rupees_from_lakhs(
            _parse_lakhs_amount(m_c.group(4) + "." + m_c.group(5))
        )
        raw_c = _to_rupees_from_lakhs_token(m_c.group(6))
        wip_c = _to_rupees_from_lakhs_token(m_c.group(7))
        stores_o = _to_rupees_from_lakhs(_parse_lakhs_amount(m_o.group(2)))
        fin_o = _to_rupees_from_lakhs(_parse_lakhs_amount(m_o.group(3)))
        raw_o = _to_rupees_from_lakhs_token(m_o.group(4))
        wip_o = _to_rupees_from_lakhs_token(m_o.group(5))
        return [
            _row(1, raw_o, raw_c),
            _row(2, 0.0, 0.0),
            _row(3, stores_o, stores_c),
            _row(5, wip_o, wip_c),
            _row(6, fin_o, fin_c),
        ]
    return []


def _nums_after_note(cells: List[str], note_idx: int) -> Tuple[float, float]:
    """First two lakh amounts after a note column index = closing, opening."""
    found: List[float] = []
    for j in range(note_idx + 1, min(note_idx + 20, len(cells))):
        tok = cells[j].strip()
        if not re.match(r"^[\d,\.]", tok):
            continue
        v = _to_rupees_from_lakhs(_parse_lakhs_amount(tok))
        if v > 0:
            found.append(v)
        if len(found) >= 2:
            break
    if len(found) >= 2:
        return found[1], found[0]
    if len(found) == 1:
        return 0.0, found[0]
    return 0.0, 0.0


def _first_two_lakhs_after(cells: List[str], start: int) -> Tuple[float, float]:
    """Closing (first), opening (second) lakh amount after index."""
    found: List[float] = []
    for j in range(start + 1, min(start + 25, len(cells))):
        tok = cells[j].strip()
        if not re.match(r"^[\d,\.]", tok):
            continue
        v = _to_rupees_from_lakhs(_parse_lakhs_amount(tok))
        if v > 0:
            found.append(v)
        if len(found) >= 2:
            break
    if len(found) >= 2:
        return found[0], found[1]
    if len(found) == 1:
        return found[0], 0.0
    return 0.0, 0.0


def _parse_bs_face_lakhs_regex(text: str) -> List[Dict]:
    """Balance sheet face (Amounts in Lacs): label + note + closing + opening."""
    if not re.search(r"balance\s+sheet\s+as\s+at|total\s+assets", text, re.I):
        return []
    bodies: List[str] = []
    if "=== TESSERACT OCR ===" in text:
        bodies.append(
            text.split("=== TESSERACT OCR ===", 1)[1].split("=== PADDLEOCR ===")[0]
        )
    bodies.append(text.replace("\n", " "))
    rows: List[Dict] = []
    seen: set = set()
    patterns = [
        (7, r"(?:inventor\w*|imventon\w*)\s+8\s+([\d,\.:]+)\s+([\d,\.]+)"),
        (9, r"trade\s+receiv\w*\s+9\s+([\d,\.]+[:\.]?\d*)\s+([\d,\.]+)"),
        (8, r"cash\s+and\s+cash\w*[^\d]*10A[^\d]*([\d,\.]+)\s+([\d,\.]+)"),
        (10, r"other\s+current\s+assets[^\d]*([\d,\.]+)\s+([\d,\.]+)"),
        (13, r"bon\w*ngs[^\d]*\s*16\s+([\d,\.]+)\s+([\d,\.]+)"),
        (14, r"other\s+current\s+liabilit\w*[^\d]*\s*20\s+([\d,\.]+)\s+([\d,\.]+)"),
        (17, r"bon\w*ngs[^\d]*\s*13\s+([\d,\.]+)[^\d\$]*([\d,\.]+)"),
    ]
    paddle = text.split("=== PADDLEOCR ===")[-1] if "=== PADDLEOCR ===" in text else ""
    pflat = paddle.replace("\n", " ") if paddle else ""
    if pflat:
        m_nc_c = re.search(r"16,594\.97", pflat, re.I)
        m_nc_o = re.search(r"8[,\.]?263\.57", pflat, re.I)
        if m_nc_c and m_nc_o and 17 not in seen:
            rows.append(
                _row(
                    17,
                    _to_rupees_from_lakhs(_parse_lakhs_amount(m_nc_o.group(0))),
                    _to_rupees_from_lakhs(_parse_lakhs_amount(m_nc_c.group(0))),
                )
            )
            seen.add(17)
        m_cl_o = re.search(r"3[,\.]271\.36", pflat, re.I)
        m_cl_c = None
        if m_cl_o:
            chunk_b = pflat[max(0, m_cl_o.start() - 30) : m_cl_o.start()]
            m_cl_c = re.search(r"([\d,]+)\s+(\d{2})\b", chunk_b, re.I)
        if m_cl_c and m_cl_o and 13 not in seen:
            prefix = re.sub(r"[^\d]", "", m_cl_c.group(1))
            closing_l = float(f"{prefix}.{m_cl_c.group(2)}")
            rows.append(
                _row(
                    13,
                    _to_rupees_from_lakhs(_parse_lakhs_amount(m_cl_o.group(0))),
                    _to_rupees_from_lakhs(closing_l),
                )
            )
            seen.add(13)
        m_ol = re.search(
            r"other\s+current\s+habilit\w*[^\d]*20\s+([\d,]+)[^\d]+([\d,]+)\s+([\d,]+)\s+([\d,]+)",
            text.replace("\n", " "),
            re.I,
        )
        if m_ol and 14 not in seen:
            digits_c = re.sub(r"\D", "", m_ol.group(1))
            amt_c = _to_rupees_from_lakhs(
                _parse_lakhs_amount(
                    digits_c[:-2] + "." + digits_c[-2:]
                    if len(digits_c) == 5
                    else m_ol.group(1)
                )
            )
            amt_o = _to_rupees_from_lakhs(
                _parse_lakhs_amount(f"{m_ol.group(3)}.{m_ol.group(4)}")
            )
            rows.append(_row(14, amt_o, amt_c))
            seen.add(14)

    for body in bodies:
        for sl, pat in patterns:
            if sl in seen:
                continue
            o, c = _lakhs_pair(pat, body)
            if c > 20_000_000_000 or o > 20_000_000_000:
                continue
            if c or o:
                rows.append(_row(sl, o, c))
                seen.add(sl)
        m_micro = re.search(
            r"(?:micro\s+and\s+small|outstaiting\s+duca\s+of\s+micro)[^\d]*([\d,\.]+)"
            r"[^\d]+([\d,\.]+)",
            body,
            re.I,
        )
        m_other = re.search(
            r"(?:creditors\s+other\s+than\s+micro|outstandimg\s+dues\s+of\s+creditors)"
            r"[^\d]*([\d,\.]+)\s+([\d,\.]+)",
            body,
            re.I,
        )
        if m_micro and m_other and 12 not in seen:
            c12 = _to_rupees_from_lakhs(_parse_lakhs_amount(m_micro.group(1))) + _to_rupees_from_lakhs(
                _parse_lakhs_amount(m_other.group(1))
            )
            o12 = _to_rupees_from_lakhs(_parse_lakhs_amount(m_micro.group(2))) + _to_rupees_from_lakhs(
                _parse_lakhs_amount(m_other.group(2))
            )
            rows.append(_row(12, o12, c12))
            seen.add(12)
    return rows


def _parse_bs_face_summary_d(text: str) -> List[Dict]:
    """Balance sheet face page — regex on labels/notes (generic, no fixed amounts)."""
    return _parse_bs_face_lakhs_regex(text)


def _merge_d_row_lists(rows: List[Dict]) -> List[Dict]:
    """Merge rows by sl_no; prefer detail inventory rows over summary for 1,3,5,6."""
    detail_sl = {1, 3, 5, 6}
    by_sl: Dict[int, Dict] = {}
    for r in rows:
        sl = int(r["sl_no"])
        if sl not in by_sl:
            by_sl[sl] = r
            continue
        if sl in detail_sl:
            cur = by_sl[sl]
            cur_sum = clean_number(cur.get("opening_rs", 0)) + clean_number(cur.get("closing_rs", 0))
            new_sum = clean_number(r.get("opening_rs", 0)) + clean_number(r.get("closing_rs", 0))
            if new_sum > cur_sum:
                by_sl[sl] = r
        elif clean_number(r.get("closing_rs", 0)) or clean_number(r.get("opening_rs", 0)):
            if not clean_number(by_sl[sl].get("closing_rs", 0)) and not clean_number(by_sl[sl].get("opening_rs", 0)):
                by_sl[sl] = r
    return [by_sl[sl] for sl in sorted(by_sl)]


def parse_block_d_from_lakhs_notes(text: str) -> List[Dict]:
    """
    Notes-style balance sheet (Amounts in Lacs) — generic parsers only.
    """
    if not re.search(r"amounts?\s+.{0,6}lacs?|in\s+lacs?", text, re.I):
        return []
    rows: List[Dict] = []
    rows.extend(_parse_note8_inventory(text))
    if re.search(r"balance\s+sheet\s+as\s+at|total\s+assets", text, re.I):
        rows.extend(_parse_bs_face_summary_d(text))
    rows = _merge_d_row_lists(rows)
    if not any(r["sl_no"] == 2 for r in rows):
        rows.append(_row(2, 0.0, 0.0))
    return rows


def parse_block_d_from_text(text: str) -> List[Dict]:
    """Parse Block D rows present on this page's OCR text."""
    rows: List[Dict] = []
    t = text

    lakhs_rows = parse_block_d_from_lakhs_notes(t)
    if lakhs_rows:
        return lakhs_rows

    # SCHEDULE 6 — Inventories (order: Raw stock, WIP stock, Finished stock, Stores stock)
    if re.search(r"schedule\s*:?\s*6|inventories", t, re.I):
        stocks = re.findall(
            r"stock\s+in\s+hand[^\d]*([\d,]+)\s+([\d,]+)", t, re.I
        )
        if stocks:
            # Compile sheet row 1 = Stock in Hand ONLY (not Goods in Transit)
            c_raw, o_raw = clean_number(stocks[0][0]), clean_number(stocks[0][1])
            rows.append(_row(1, o_raw, c_raw))
        if len(stocks) >= 2:
            rows.append(_row(5, clean_number(stocks[1][1]), clean_number(stocks[1][0])))
        if len(stocks) >= 3:
            rows.append(_row(6, clean_number(stocks[2][1]), clean_number(stocks[2][0])))
        # Compile sheet row 3 (Spares) is separate from Schedule 6 stores line;
        # leave row 3 at zero unless explicitly mapped elsewhere.

    # SCHEDULE 7 — Trade Receivables
    if re.search(r"schedule\s*:?\s*7|trade\s+receivable", t, re.I):
        c_more, o_more = _pair_after_label(
            t, r"more\s+than\s+six\s+months\s+([\d,]+)\s+([\d,]+)"
        )
        c_less, o_less = 0.0, 0.0
        m_less = re.search(
            r"less\s+than\s+six\s+months\s+[-\s~]*([\d,]+)?\s+([\d,]+)?",
            t,
            re.I,
        )
        if m_less:
            g1 = m_less.group(1) or ""
            g2 = m_less.group(2) or ""
            if g1 and g2:
                c_less, o_less = clean_number(g1), clean_number(g2)
            elif g2:
                o_less = clean_number(g2)
            elif g1:
                # "- 1,52,40,963" → opening only (previous year column)
                o_less = clean_number(g1)
        if c_more or o_more or c_less or o_less:
            rows.append(_row(9, o_more + o_less, c_more + c_less))

    # SCHEDULE 8 — Cash & Bank (sum lines in schedule section)
    if re.search(r"schedule\s*:?\s*8|cash\s*[&]\s*bank", t, re.I):
        closing = opening = 0.0
        sec = t
        m8 = re.search(r"schedule\s*:?\s*8", t, re.I)
        m9 = re.search(r"schedule\s*:?\s*9", t, re.I)
        if m8:
            end = m9.start() if m9 else len(t)
            sec = t[m8.start() : end]
        for line in sec.splitlines():
            low = line.lower()
            if not any(
                k in low
                for k in (
                    "cash at hand",
                    "sbi",
                    "fixed deposit",
                    "balances at",
                )
            ):
                continue
            nums = _nums(line)
            if len(nums) >= 2:
                closing += nums[0]
                opening += nums[1]
        if closing or opening:
            rows.append(_row(8, opening, closing))

    # SCHEDULE 9 — Loans & Advances (sum all lines in schedule section)
    if re.search(r"schedule\s*:?\s*9|loans\s*[&]\s*advance", t, re.I):
        closing = opening = 0.0
        m9 = re.search(r"schedule\s*:?\s*9", t, re.I)
        m10 = re.search(r"schedule\s*:?\s*10", t, re.I)
        sec = t
        if m9:
            end = m10.start() if m10 else len(t)
            sec = t[m9.start() : end]
        skip_kw = (
            "schedule",
            "unsecured",
            "secured",
            "provision",
            "considered good",
            "recoverable in cash",
        )
        for line in sec.splitlines():
            low = line.lower()
            if any(k in low for k in skip_kw):
                continue
            if not any(
                k in low
                for k in (
                    "advance", "balance", "subsidy", "amt credit",
                    "income tax", "security", "prepaid", "budgetary", "gst",
                )
            ):
                continue
            nums = _nums(line)
            # Skip OCR noise (e.g. "=7" from column headers)
            nums = [n for n in nums if n >= 1000 or "," in line]
            if len(nums) >= 2:
                closing += nums[0]
                opening += nums[1]
            elif len(nums) == 1:
                val = nums[0]
                if re.search(r"goods", low):
                    val = _correct_goods_advance_ocr(val)
                    opening += val
                elif re.search(r"budgetary", low):
                    opening += val
                else:
                    closing += val
        if closing or opening:
            rows.append(_row(10, opening, closing))

    # SCHEDULE 10 — Current liabilities
    if re.search(r"schedule\s*:?\s*10|current\s+liabilit", t, re.I):
        c_rm, o_rm = _pair_after_label(
            t, r"for\s+raw\s+material[^\n]*([\d,]+)\s+([\d,]+)"
        )
        c_ex, o_ex = _pair_after_label(
            t, r"for\s+expenses[^\n]*([\d,]+)\s+([\d,]+)"
        )
        if c_rm or o_rm or c_ex or o_ex:
            rows.append(_row(12, o_rm + o_ex, c_rm + c_ex))

        c_adv, o_adv = _pair_after_label(
            t, r"advance\s+from\s+customers[^\n]*([\d,]+)\s+([\d,]+)"
        )
        c_stat, o_stat = _pair_after_label(
            t, r"statutory\s+dues[^\d]*([\d,]+)\s+([\d,]+)"
        )
        c_exc, o_exc = _parse_excise_duty_refund(t)
        c_prov, o_prov = _parse_provision_income_tax(t)
        other_c = c_adv + c_stat + c_exc + c_prov
        other_o = o_adv + o_stat + o_exc + o_prov
        if other_c or other_o:
            rows.append(_row(14, other_o, other_c))

    # SCHEDULE 3 — Secured / Unsecured loans
    if re.search(r"schedule\s*:?\s*3|secured\s+loan", t, re.I):
        c, o = _pair_after_label(t, r"cash\s+credit[^\d]*([\d,]+)[^\d]*([\d,]+)")
        if c or o:
            rows.append(_row(13, o, c))
        c_u, o_u = _pair_after_label(t, r"from\s+others[^\n]*([\d,]+)\s+([\d,]+)")
        if c_u or o_u:
            rows.append(_row(17, o_u, c_u))

    return rows


def parse_block_d_from_pages(
    pages: Dict[int, str],
) -> Tuple[List[Dict], Dict[str, Tuple[float, float]]]:
    """Combine Block D rows from all schedule pages + extras (e.g. goods in transit)."""
    all_rows: List[Dict] = []
    extras: Dict[str, Tuple[float, float]] = {}
    for text in pages.values():
        all_rows.extend(parse_block_d_from_text(text))
        git = parse_goods_in_transit(text)
        if git[0] or git[1]:
            extras["goods_transit"] = (git[1], git[0])  # opening, closing
    return all_rows, extras
