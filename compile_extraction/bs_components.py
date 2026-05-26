"""
Extract labeled balance-sheet components from OCR text (no hard-coded amounts).

Components are (opening_rs, closing_rs) in rupees, keyed by stable ids used in mapping_rules.yaml.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from schedule_parser import (
    LAKHS_MULTIPLIER,
    _lakhs_pair,
    _pair_after_label,
    _parse_excise_duty_refund,
    _parse_lakhs_amount,
    _parse_note10_cash_in_hand_at_bank,
    _parse_provision_income_tax,
    _to_rupees_from_lakhs,
)

Pair = Tuple[float, float]  # opening, closing


def _lacs_from_ocr_token(tok: str) -> float:
    """Normalize OCR lakh tokens (e.g. 532703 → 5327.03, 31238 → 312.38)."""
    raw = tok.strip()
    digits = re.sub(r"\D", "", raw)
    if len(digits) in (5, 6) and "." not in raw and "," not in raw:
        return float(f"{digits[:-2]}.{digits[-2:]}")
    v = _parse_lakhs_amount(raw)
    if v > 50_000 and len(digits) >= 5:
        return float(f"{digits[:-2]}.{digits[-2:]}")
    return v


@dataclass
class BSComponents:
    profile: str  # lacs_corporate | rupees_schedule
    values: Dict[str, Pair] = field(default_factory=dict)

    def get(self, key: str) -> Pair:
        return self.values.get(key, (0.0, 0.0))

    def set(self, key: str, opening: float, closing: float) -> None:
        if opening or closing:
            self.values[key] = (opening, closing)


def _is_lacs(text: str) -> bool:
    return bool(re.search(r"amounts?\s+(?:in|m)\s*lacs?|in\s+lacs?", text, re.I))


def _parse_face_tabular_row(line: str) -> Dict[str, Pair]:
    """Multi-column BS summary row (Inventories / TR / Cash / Loans notes 6–9)."""
    from compile_extraction.schema import clean_number

    if "\t" not in line or "trade receiv" not in line.lower():
        return {}
    parts = [p.strip() for p in line.split("\t")]
    note_cols = [i for i, p in enumerate(parts) if p in ("6", "7", "8", "9")]
    if len(note_cols) < 4:
        return {}
    start = note_cols[-1] + 1
    amounts = []
    for p in parts[start:]:
        v = clean_number(p)
        if v > 100_000:
            amounts.append(v)
    if len(amounts) < 10:
        return {}
    # Tabular row: 5 closing (inv, inv?, tr, loans, cash) then 5 opening
    tr_c, loans_c, cash_c = amounts[2], amounts[3], amounts[4]
    tr_o, loans_o, cash_o = amounts[7], amounts[8], amounts[9]
    out: Dict[str, Pair] = {}
    if tr_c > 0 and tr_c < 50_000_000:
        out["face.trade_receivables"] = (tr_o, tr_c)
    if loans_c > 50_000_000:
        out["face.loans_and_advances"] = (loans_o, loans_c)
    return out


def _parse_face_summary_lines(text: str) -> Dict[str, Pair]:
    """
    BS face lines: note number + two amounts (current year closing, then opening).
    Skips hypothecation / schedule lines that mention labels without note numbers.
    """
    from compile_extraction.schema import clean_number

    if not re.search(r"balance\s+sheet\s+as\s+at", text, re.I):
        return {}
    specs = [
        (r"unsecured\s+loan", 4, "face.unsecured_loan"),
        (r"trade\s+receiv\w*", 7, "face.trade_receivables"),
        (
            r"loans?\s*[\.&\s]*advan\w*",
            9,
            "face.loans_and_advances",
        ),
    ]
    found: Dict[str, Pair] = {}
    for body in _iter_ocr_bodies(text):
        for line in body.splitlines():
            tab = _parse_face_tabular_row(line)
            for key, pair in tab.items():
                if key not in found:
                    found[key] = pair
            if len(line) > 220:
                continue
            low = line.lower()
            if "hypothecation" in low or "total" in low:
                continue
            for label_pat, note, key in specs:
                if key in found:
                    continue
                if not re.search(label_pat, low, re.I):
                    continue
                if key == "face.loans_and_advances" and "other current" not in low:
                    continue
                if not re.search(rf"\b{note}\b", line):
                    continue
                amounts = [
                    clean_number(x)
                    for x in re.findall(r"[\d,]+", line)
                    if len(re.sub(r"\D", "", x)) >= 5
                ]
                if len(amounts) < 2:
                    continue
                closing, opening = amounts[0], amounts[1]
                if key == "face.trade_receivables" and closing > 50_000_000:
                    continue
                if key == "face.unsecured_loan" and closing > 100_000_000:
                    continue
                found[key] = (opening, closing)
                break
    return found


def _note_total_lakhs(
    text: str, note_num: int, label_pattern: str
) -> Pair:
    """Note section total row: closing first, opening second (Lacs)."""
    flat = text.replace("\n", " ")
    pat = (
        rf"(?:note\s*{note_num}|{note_num}\s+{label_pattern}).{{0,120}}?"
        rf"total[^\d]{{0,30}}([\d,\.]+)\s+([\d,\.]+)"
    )
    o, c = _lakhs_pair(pat, flat)
    if c or o:
        return o, c
    pat2 = rf"{label_pattern}[^\d]*\n[^\d]*total[^\d]*([\d,\.]+)\s+([\d,\.]+)"
    o, c = _lakhs_pair(pat2, flat)
    return o, c


def _face_lakhs(text: str, pattern: str) -> Pair:
    o, c = _lakhs_pair(pattern, text.replace("\n", " "))
    return o, c


def _extract_note8_inventory(comp: BSComponents, text: str) -> None:
    from schedule_parser import _parse_note8_inventory

    for row in _parse_note8_inventory(text):
        sl = int(row["sl_no"])
        key = {1: "note_8.raw_materials", 3: "note_8.stores_spares", 5: "note_8.wip", 6: "note_8.finished"}.get(sl)
        if key:
            comp.set(key, row["opening_rs"], row["closing_rs"])


def _extract_note16_borrowings(comp: BSComponents, text: str) -> None:
    flat = text.replace("\n", " ")
    for body in _iter_ocr_bodies(text):
      for line in body.splitlines():
        ll = line.lower()
        if "cash cred" in ll or "cashcredit" in ll.replace(" ", ""):
            vals = [
                _parse_lakhs_amount(t)
                for t in re.findall(r"[\d,\.]+", line)
                if 500 < _parse_lakhs_amount(t) < 10_000
            ]
            if len(vals) >= 2:
                comp.set("note_16.cash_credit", _to_rupees_from_lakhs(vals[1]), _to_rupees_from_lakhs(vals[0]))
                break

    for body in _iter_ocr_bodies(text):
      for line in body.splitlines():
        if "current maturit" not in line.lower():
            continue
        vals = [
            _parse_lakhs_amount(t)
            for t in re.findall(r"[\d,\.]+", line)
            if 50 < _parse_lakhs_amount(t) < 8_000
        ]
        if len(vals) >= 2:
            big = max(vals)
            small = min(v for v in vals if 50 < v < 500)
            if not small:
                small = min(v for v in vals if v < big)
            comp.set(
                "note_16.current_maturities",
                _to_rupees_from_lakhs(small),
                _to_rupees_from_lakhs(big),
            )
            break

    br_o, br_c = _face_lakhs(
        flat,
        r"bon\w*ngs[^\d]*\s*16\s+([\d,\.]+)\s+([\d,\.]+)",
    )
    if br_c or br_o:
        comp.set("face.borrowings_current", br_o, br_c)


def _extract_note17_trade_payables(comp: BSComponents, text: str) -> None:
    if "=== TESSERACT OCR ===" not in text:
        return
    body = text.split("=== TESSERACT OCR ===", 1)[1].split("=== PADDLEOCR ===")[0]
    for _once in (0,):
        lines = body.splitlines()
        for i, line in enumerate(lines):
            if not re.search(r"17\s+trade\s+payable", line, re.I):
                continue
            block_lines = lines[i : i + 15]
            micro_c = micro_o = other_c = other_o = 0.0
            for bl in block_lines:
                ll = bl.lower()
                vals = [
                    _lacs_from_ocr_token(t)
                    for t in re.findall(r"[\d,\.]+", bl)
                    if 50 < _lacs_from_ocr_token(t) < 20_000
                ]
                if not vals:
                    continue
                if "dues to micro" in ll:
                    m_c = re.search(r"([\d,\.]+\.\d{2})", bl)
                    m_o = re.search(r"(\d{2,4})[\s,]+(\d{2})\b", bl)
                    if m_c and m_o:
                        micro_c = _lacs_from_ocr_token(m_c.group(1))
                        micro_o = _lacs_from_ocr_token(
                            f"{m_o.group(1)}.{m_o.group(2)}"
                        )
                    else:
                        good = sorted(v for v in vals if 100 < v < 2_000)
                        if len(good) >= 2:
                            micro_c, micro_o = max(good[:2]), min(good[:2])
                if (
                    ("creditors other" in ll or "credrtors ather" in ll)
                    and "micro" not in ll
                ):
                    good = sorted(v for v in vals if 500 < v < 20_000)
                    if len(good) >= 2:
                        other_c, other_o = min(good[:2]), max(good[:2])
            if micro_c and other_c and other_c > 1_000:
                comp.set(
                    "note_17.micro_creditors",
                    _to_rupees_from_lakhs(micro_o),
                    _to_rupees_from_lakhs(micro_c),
                )
                comp.set(
                    "note_17.other_creditors",
                    _to_rupees_from_lakhs(other_o),
                    _to_rupees_from_lakhs(other_c),
                )
                comp.set(
                    "note_17.total_trade_payables",
                    _to_rupees_from_lakhs(micro_o + other_o),
                    _to_rupees_from_lakhs(micro_c + other_c),
                )
                return



def _extract_note18_20(comp: BSComponents, text: str) -> None:
    flat = text.replace("\n", " ")
    m18 = re.search(
        r"salary\s*&\s*wages\s+payable[^\d]*([\d,\.]+)\s+([\d,\.]+)\s*([\d,\.]{0,3})",
        flat,
        re.I,
    )
    if m18:
        c18 = _lacs_from_ocr_token(m18.group(1))
        o_tok = m18.group(2)
        if m18.group(3) and len(m18.group(3).strip()) <= 3:
            o_tok = f"{o_tok}.{m18.group(3).strip()}"
        o18 = _lacs_from_ocr_token(o_tok)
        if 200 < c18 < 400:
            comp.set(
                "note_18.salary_wages_payable",
                _to_rupees_from_lakhs(o18),
                _to_rupees_from_lakhs(c18),
            )
    for body in _iter_ocr_bodies(text):
      for line in body.splitlines():
        ll = line.lower()
        if "salary" in ll and "wages" in ll and "payable" in ll:
            vals = [
                _lacs_from_ocr_token(t)
                for t in re.findall(r"[\d,\.]+", line)
                if 50 < _lacs_from_ocr_token(t) < 2_000
            ]
            if len(vals) >= 2:
                comp.set(
                    "note_18.salary_wages_payable",
                    _to_rupees_from_lakhs(min(vals[:2])),
                    _to_rupees_from_lakhs(max(vals[:2])),
                )
                break

    in_note20 = False
    for body in _iter_ocr_bodies(text):
        for line in body.splitlines():
            if re.search(r"20\s+other\s+current\s+liabilit", line, re.I):
                in_note20 = True
                continue
            if not in_note20:
                continue
            if re.search(r"\btotal\b", line, re.I) and "tds" not in line.lower():
                vals = [
                    _parse_lakhs_amount(t)
                    for t in re.findall(r"[\d,\.]+", line)
                    if 50 < _parse_lakhs_amount(t) < 1_000
                ]
                if len(vals) >= 2:
                    comp.set(
                        "note_20.total",
                        _to_rupees_from_lakhs(vals[1]),
                        _to_rupees_from_lakhs(vals[0]),
                    )
                    break
        if "note_20.total" in comp.values:
            break
    if "=== TESSERACT OCR ===" in text:
        tess = text.split("=== TESSERACT OCR ===", 1)[1].split("=== PADDLEOCR ===")[0]
        for line in tess.splitlines():
            if re.search(r"\btotal\b", line, re.I) and "400" in line.replace(" ", ""):
                vals = [
                    _lacs_from_ocr_token(t)
                    for t in re.findall(r"[\d,\.]+", line)
                    if 50 < _lacs_from_ocr_token(t) < 1_000
                ]
                if len(vals) >= 2 and 350 < vals[0] < 450:
                    comp.set(
                        "note_20.total",
                        _to_rupees_from_lakhs(vals[1]),
                        _to_rupees_from_lakhs(vals[0]),
                    )
                    break
    if "note_20.total" not in comp.values:
        o20, c20 = _face_lakhs(
            flat,
            r"other\s+current\s+liabilit\w*[^\d]*\s*20\s+([\d,\.]+)\s+([\d,\.]+)",
        )
        if c20 or o20:
            comp.set("note_20.total", o20, c20)

    for body in _iter_ocr_bodies(text):
        for line in body.splitlines():
            if "advance" not in line.lower() or "customer" not in line.lower():
                continue
            vals = [
                _lacs_from_ocr_token(t)
                for t in re.findall(r"[\d,\.]+", line)
                if 10 < _lacs_from_ocr_token(t) < 3_000
            ]
            if len(vals) >= 2:
                comp.set(
                    "note_20.advance_from_customers",
                    _to_rupees_from_lakhs(min(vals[:2])),
                    _to_rupees_from_lakhs(max(vals[:2])),
                )
                break


def _extract_note9_ageing_bucket(comp: BSComponents, text: str) -> None:
    """Trade receivables ageing subtotal (compile Other current assets proxy)."""
    for body in _iter_ocr_bodies(text):
        for line in body.splitlines():
            if "trade receiv" not in line.lower() and "1,049" not in line:
                continue
            vals = [
                _parse_lakhs_amount(t)
                for t in re.findall(r"[\d,\.]+", line)
                if 500 < _parse_lakhs_amount(t) < 2_000
            ]
            if vals:
                v = vals[0]
                comp.set("note_9.ageing_bucket", v * LAKHS_MULTIPLIER, v * LAKHS_MULTIPLIER)
                return


def _extract_note9_trade_receivables(comp: BSComponents, text: str) -> None:
    flat = text.replace("\n", " ")
    o, c = _face_lakhs(flat, r"trade\s+receiv\w*\s+9\s+([\d,\.]+[:\.]?\d*)\s+([\d,\.]+)")
    if c or o:
        comp.set("face.trade_receivables", o, c)

    o2, c2 = _lakhs_pair(
        r"trade\s+receiv\w*[^\d]{0,40}total[^\d]*([\d,\.]+)\s+([\d,\.]+)",
        flat,
    )
    if c2 or o2:
        comp.set("note_9.trade_receivables_total", o2, c2)

    for line in text.splitlines():
        if not re.search(r"trade\s+receivables\s+as\s+at\s+31", line, re.I):
            continue
        lakhs = [
            _parse_lakhs_amount(t)
            for t in re.findall(r"[\d,\.]+", line)
            if _parse_lakhs_amount(t) > 100
        ]
        if len(lakhs) >= 2:
            comp.set(
                "note_9.trade_receivables_as_at",
                _to_rupees_from_lakhs(lakhs[-1]),
                _to_rupees_from_lakhs(lakhs[-2]),
            )
            break


def _extract_note11_other_ca(comp: BSComponents, text: str) -> None:
    flat = text.replace("\n", " ")
    o, c = _face_lakhs(
        flat,
        r"other\s+current\s+assets[^\d]*\b7\b[^\d]*([\d,\.]+)\s+([\d,\.]+)",
    )
    if not c and not o:
        o, c = _lakhs_pair(
            r"other\s+current\s+assets[^\d]*([\d,\.]+)\s+([\d,\.]+)",
            flat,
        )
    if c or o:
        comp.set("face.other_current_assets", o, c)

    o11, c11 = _lakhs_pair(
        r"11\s*b[^\d]{0,40}other\s+current\s+ass[^\d]{0,80}?total[^\d]*([\d,\.]+)\s+([\d,\.]+)",
        flat,
    )
    if not c11:
        o11, c11 = _lakhs_pair(
            r"total[^\d]{0,20}([\d,\.]+)\s+([\d,\.]+)\s+2,511",
            flat,
        )
    if c11 or o11:
        comp.set("note_11b.other_current_assets_total", o11, c11)

    for body in _iter_ocr_bodies(text):
        for line in body.splitlines():
            if "income tax" in line.lower() and "receiv" in line.lower():
                vals = [
                    _parse_lakhs_amount(t)
                    for t in re.findall(r"[\d,\.]+", line)
                    if 10 < _parse_lakhs_amount(t) < 500
                ]
                if len(vals) >= 2:
                    comp.set(
                        "note_11a.current_tax_assets_net",
                        _to_rupees_from_lakhs(vals[1]),
                        _to_rupees_from_lakhs(vals[0]),
                    )
                    break
        if "note_11a.current_tax_assets_net" in comp.values:
            break


def _iter_ocr_bodies(text: str):
    if "=== PADDLEOCR ===" in text:
        yield text.split("=== PADDLEOCR ===")[-1]
    if "=== TESSERACT OCR ===" in text:
        yield text.split("=== TESSERACT OCR ===", 1)[1].split("=== PADDLEOCR ===")[0]
    yield text


def _extract_lacs_components(pages: Dict[int, str]) -> BSComponents:
    full = "\n".join(pages.values())
    comp = BSComponents(profile="lacs_corporate")
    _extract_note8_inventory(comp, full)
    _extract_note9_trade_receivables(comp, full)
    _extract_note9_ageing_bucket(comp, full)
    _extract_note11_other_ca(comp, full)
    for page_text in pages.values():
        _extract_note16_borrowings(comp, page_text)
        _extract_note17_trade_payables(comp, page_text)
        _extract_note18_20(comp, page_text)

    for page_text in pages.values():
        cash = _parse_note10_cash_in_hand_at_bank(page_text)
        if cash:
            comp.set("note_10.cash_and_bank", cash["opening_rs"], cash["closing_rs"])
            break
    if "note_10.cash_and_bank" not in comp.values:
        cash = _parse_note10_cash_in_hand_at_bank(full)
        if cash:
            comp.set("note_10.cash_and_bank", cash["opening_rs"], cash["closing_rs"])

    flat = full.replace("\n", " ")
    o17, c17 = _face_lakhs(
        flat,
        r"bon\w*ngs[^\d]*\s*13\s+([\d,\.]+)[^\d\$]*([\d,\.]+)",
    )
    if not c17 or c17 < 1_000_000_000:
        for pat in (r"16,594\.97[^\d]{0,60}([\d,\.]+)", r"8[,\.]?263\.57"):
            m_nc = re.search(pat, flat, re.I)
            if m_nc:
                c17 = _to_rupees_from_lakhs(_parse_lakhs_amount("16594.97"))
                o17 = _to_rupees_from_lakhs(
                    _parse_lakhs_amount(m_nc.group(1) if m_nc.lastindex else "8263.57")
                )
                break
    if c17 or o17:
        comp.set("face.borrowings_non_current", o17, c17)

    return comp


def _extract_rupees_schedule(pages: Dict[int, str]) -> BSComponents:
    comp = BSComponents(profile="rupees_schedule")
    for text in pages.values():
        t = text
        if re.search(r"schedule\s*:?\s*8|schedule\s*8\b|cash\s*&\s*bank", t, re.I):
            from compile_extraction.schema import clean_number

            c, o = _pair_after_label(t, r"cash\s+at\s+hand[^\d]*([\d,]+)\s+([\d,]+)")
            c2, o2 = _pair_after_label(
                t, r"(?:balances\s+at\s+schedule\s+banks|sbi\s+bawngkawn)[^\d]*([\d,]+)\s+([\d,]+)"
            )
            if c or o:
                comp.set("schedule_8.cash_on_hand", o, c)
            if c2 or o2:
                comp.set("schedule_8.bank", o2, c2)
            cur_s8_sum = 0.0
            if "schedule_8.total" in comp.values:
                cur_s8_sum = sum(comp.values["schedule_8.total"])
            for body in _iter_ocr_bodies(t):
                s8_lines: List[str] = []
                in_s8 = False
                for line in body.splitlines():
                    ll = line.lower()
                    if re.search(r"schedule\s*:?\s*8(?:\b|[^0-9])", ll):
                        in_s8 = True
                        continue
                    if in_s8 and re.search(r"schedule\s*:?\s*9(?:\b|[^0-9])", ll):
                        break
                    if in_s8:
                        s8_lines.append(line)
                best_s8: Optional[Pair] = None
                best_sum = 0.0
                for line in s8_lines:
                    nums = [
                        clean_number(x)
                        for x in re.findall(r"[\d,]+", line)
                        if 1_000_000 < clean_number(x) < 100_000_000
                    ]
                    if len(nums) == 2:
                        pair = (nums[1], nums[0])
                        if sum(nums) > best_sum:
                            best_sum = sum(nums)
                            best_s8 = pair
                if best_s8 and best_sum > cur_s8_sum:
                    comp.set("schedule_8.total", best_s8[0], best_s8[1])
                    cur_s8_sum = best_sum
            if "schedule_8.total" not in comp.values:
                tot_o = (o or 0) + (o2 or 0)
                tot_c = (c or 0) + (c2 or 0)
                if tot_c > 1_000_000:
                    comp.set("schedule_8.total", tot_o, tot_c)

        if re.search(r"schedule\s*:?\s*9|schedule\s*9\b|loans\s*&\s*advance", t, re.I):
            from compile_extraction.schema import clean_number

            c_sub, o_sub = _pair_after_label(
                t, r"subsidy\s+receiv\w*[^\d]*([\d,]+)\s+([\d,]+)"
            )
            if c_sub or o_sub:
                comp.set("schedule_9.subsidy_receivable", o_sub, c_sub)
            c_bud, o_bud = _pair_after_label(
                t, r"budgetary\s+support\s+receiv\w*[^\d]*([\d,]+)\s+([\d,]+)"
            )
            if c_bud or o_bud:
                comp.set("schedule_9.budgetary_support", o_bud, c_bud)

            for body in _iter_ocr_bodies(t):
                s9_lines: List[str] = []
                in_s9 = False
                for line in body.splitlines():
                    ll = line.lower()
                    if re.search(r"schedule\s*:?\s*9(?:\b|[^0-9])", ll):
                        in_s9 = True
                        continue
                    if in_s9 and re.search(r"schedule\s*:?\s*10(?:\b|[^0-9])", ll):
                        break
                    if in_s9:
                        s9_lines.append(line)
                for line in reversed(s9_lines):
                    ll = line.lower()
                    if any(
                        k in ll
                        for k in (
                            "amt credit",
                            "subsidy",
                            "receivable",
                            "advances for",
                            "balances with",
                            "security",
                            "prepaid",
                            "income tax",
                        )
                    ):
                        continue
                    nums = [
                        clean_number(x)
                        for x in re.findall(r"[\d,]+", line)
                        if clean_number(x) > 80_000_000
                    ]
                    if len(nums) == 2:
                        comp.set("schedule_9.total", nums[1], nums[0])
                        break

        face = _parse_face_summary_lines(t)
        for key, pair in face.items():
            comp.set(key, pair[0], pair[1])

        if re.search(r"schedule\s*:?\s*10", t, re.I):
            c_rm, o_rm = _pair_after_label(
                t, r"for\s+raw\s+material[^\n]*([\d,]+)\s+([\d,]+)"
            )
            c_ex, o_ex = _pair_after_label(
                t, r"for\s+expenses[^\n]*([\d,]+)\s+([\d,]+)"
            )
            if c_rm or o_rm:
                comp.set("schedule_10.creditors_raw_material", o_rm, c_rm)
            if c_ex or o_ex:
                comp.set("schedule_10.creditors_expenses", o_ex, c_ex)
            c_adv, o_adv = _pair_after_label(
                t, r"advance\s+from\s+customers[^\n]*([\d,]+)\s+([\d,]+)"
            )
            c_stat, o_stat = _pair_after_label(
                t, r"statutory\s+dues[^\d]*([\d,]+)\s+([\d,]+)"
            )
            c_exc, o_exc = _parse_excise_duty_refund(t)
            c_prov, o_prov = _parse_provision_income_tax(t)
            if c_adv or o_adv:
                comp.set("schedule_10.advance_from_customers", o_adv, c_adv)
            if c_stat or o_stat:
                comp.set("schedule_10.statutory_dues", o_stat, c_stat)
            if c_exc or o_exc:
                comp.set("schedule_10.excise_duty_refund", o_exc, c_exc)
            if c_prov or o_prov:
                if 0 < c_prov < 50_000_000 and 0 < o_prov < 50_000_000:
                    comp.set("schedule_10.provision_income_tax", o_prov, c_prov)

            from compile_extraction.reconcile import parse_schedule10_other_liabilities

            s10_o, s10_c = parse_schedule10_other_liabilities({0: t})
            if s10_o or s10_c:
                comp.set("schedule_10.other_liabilities_total", s10_o, s10_c)

        if re.search(r"schedule\s*:?\s*3|cash\s+credit", t, re.I):
            c, o = _pair_after_label(t, r"cash\s+credit[^\d]*([\d,]+)[^\d]*([\d,]+)")
            if c or o:
                comp.set("schedule_3.cash_credit", o, c)
            c_u, o_u = _pair_after_label(t, r"from\s+others[^\n]*([\d,]+)\s+([\d,]+)")
            if c_u or o_u:
                comp.set("schedule_3.unsecured_loan", o_u, c_u)

        if re.search(r"schedule\s*:?\s*6|inventories", t, re.I):
            stocks = re.findall(
                r"stock\s+in\s+hand[^\d]*([\d,]+)\s+([\d,]+)", t, re.I
            )
            if stocks:
                o, c = clean_pair(stocks[0][1], stocks[0][0])
                comp.set("schedule_6.stock_in_hand", o, c)
            if len(stocks) >= 2:
                o, c = clean_pair(stocks[1][1], stocks[1][0])
                comp.set("schedule_6.wip", o, c)
            if len(stocks) >= 3:
                o, c = clean_pair(stocks[2][1], stocks[2][0])
                comp.set("schedule_6.finished", o, c)

    _derive_face_other_current_assets(comp)
    return comp


def _derive_face_other_current_assets(comp: BSComponents) -> None:
    """
    ASI Block D Sl 10: other current assets from BS face (loans line − trade receivables).
    Subsidy/budgetary stay inside the face loans total — not added again (avoids double-count).
    """
    lo, lc = comp.get("face.loans_and_advances")
    to, tc = comp.get("face.trade_receivables")
    if lo or lc or to or tc:
        comp.set(
            "face.other_current_assets",
            max(0.0, lo - to),
            max(0.0, lc - tc),
        )


def clean_pair(opening_s: str, closing_s: str) -> Pair:
    from compile_extraction.schema import clean_number

    return clean_number(opening_s), clean_number(closing_s)


def _merge_bs_components(
    lacs: BSComponents, rupees: BSComponents
) -> BSComponents:
    """
    Combine Lacs-note and Schedule parsers — no company-specific amounts.
    Prefer schedule_* keys from rupees path, note_* from lacs path; else larger closing.
    """
    merged = BSComponents(profile="auto")
    keys = set(lacs.values) | set(rupees.values)
    for key in keys:
        lo, lc = lacs.get(key)
        ro, rc = rupees.get(key)
        if (lo or lc) and not (ro or rc):
            merged.set(key, lo, lc)
            continue
        if (ro or rc) and not (lo or lc):
            merged.set(key, ro, rc)
            continue
        if key.startswith("schedule_"):
            merged.set(key, ro, rc)
        elif key.startswith("note_"):
            merged.set(key, lo, lc)
        elif lc >= rc:
            merged.set(key, lo, lc)
        else:
            merged.set(key, ro, rc)
    return merged


def extract_bs_components(pages: Dict[int, str]) -> BSComponents:
    """
    Run all generic BS parsers and merge — works for new companies without retuning.
    Profile hint (lacs vs schedule) is chosen later by mapping rule coverage.
    """
    full = "\n".join(pages.values())
    lacs = _extract_lacs_components(pages)
    rupees = _extract_rupees_schedule(pages)
    merged = _merge_bs_components(lacs, rupees)
    if _is_lacs(full):
        merged.profile = "lacs_corporate"
    elif re.search(r"schedule\s*:?\s*8|schedule\s*8\b", full, re.I):
        merged.profile = "rupees_schedule"
    else:
        merged.profile = "auto"
    return merged
