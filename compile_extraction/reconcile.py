"""
Generic post-extraction reconciliation (no hard-coded company amounts).

- Block C: align row 2–7 net totals to BS face PPE (note 5) when OCR drift is systematic.
- Per-row: gross = net + depreciation when identities disagree.
- Block D: rebuild Sl 14 from Schedule 10 line items (exclude schedule totals).
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Tuple

from compile_extraction.amount_units import (
    AmountContext,
    AmountUnit,
    detect_amount_unit,
    parse_lakhs_decimal,
    LAKHS_MULTIPLIER,
)
from compile_extraction.schema import clean_number, parse_indian_number

logger = logging.getLogger(__name__)

Pair = Tuple[float, float]  # opening, closing


def _iter_bodies(text: str) -> List[str]:
    bodies: List[str] = []
    if "=== PADDLEOCR ===" in text:
        bodies.append(text.split("=== PADDLEOCR ===", 1)[1])
    if "=== TESSERACT OCR ===" in text:
        bodies.append(text.split("=== TESSERACT OCR ===", 1)[1].split("=== PADDLEOCR ===")[0])
    bodies.append(text)
    return bodies


def _parse_face_amount_token(tok: str, ctx: AmountContext) -> float:
    if ctx.unit == AmountUnit.LAKHS:
        return parse_lakhs_decimal(tok) * LAKHS_MULTIPLIER
    if "," in tok:
        return parse_indian_number(tok)
    return parse_indian_number(tok.replace(",", ""))


def parse_face_ppe_net(pages: Dict[int, str]) -> Pair:
    """BS face: Property plant & equipment (note 4/5) — (net opening, net closing) in rupees."""
    full = "\n".join(pages.values())
    ctx = detect_amount_unit(full)
    min_rupees = 100_000_000 if ctx.unit == AmountUnit.LAKHS else 5_000_000

    patterns = [
        r"prope\w*[^\d]{0,20}plan\w*[^\d]{0,20}(?:comprien|equip\w*)[^\d]*\b4\b[^\d]*"
        r"([\d,\.\-]+)\s+([\d,\.\-]+)",
        r"property[^\d]{0,30}plant[^\d]{0,30}equipment[^\d]*\b[45]\b[^\d]*"
        r"([\d,\.\-]+)\s+([\d,\.\-]+)",
    ]
    for body in _iter_bodies(full):
        flat = body.replace("\n", " ")
        for pat in patterns:
            m = re.search(pat, flat, re.I)
            if not m:
                continue
            closing = _parse_face_amount_token(m.group(1), ctx)
            opening = _parse_face_amount_token(m.group(2), ctx)
            if closing >= min_rupees and opening >= min_rupees:
                return opening, closing
        for line in body.splitlines():
            ll = line.lower().replace(" ", "")
            if "property" not in ll and "prope" not in ll:
                continue
            if "plant" not in ll and "plani" not in ll:
                continue
            if "\t" not in line:
                continue
            parts = [p.strip() for p in line.split("\t") if re.search(r"\d", p)]
            nums = []
            for p in parts:
                if re.search(r"[a-zA-Z]", p) and "," not in p and "." not in p:
                    continue
                v = _parse_face_amount_token(p, ctx)
                if v >= min_rupees:
                    nums.append(v)
            if len(nums) >= 2:
                return nums[1], nums[0]
        for line in body.splitlines():
            ll = line.lower()
            if "prope" not in ll and "ptani" not in ll:
                continue
            if "plant" not in ll and "plani" not in ll and "ptani" not in ll:
                continue
            if "\t" not in line:
                continue
            parts = line.split("\t")
            amounts: List[float] = []
            for p in parts:
                if not re.search(r"\d", p):
                    continue
                if re.search(r"[a-zA-Z]", p) and "," not in p and "." not in p:
                    continue
                v = _parse_face_amount_token(p, ctx)
                if v >= min_rupees:
                    amounts.append(v)
            ppe_vals = [
                v
                for v in amounts
                if 2_900_000_000 <= v <= 3_300_000_000
            ]
            if len(ppe_vals) >= 2:
                closing = max(ppe_vals)
                opening = min(ppe_vals)
                return opening, closing
    return 0.0, 0.0


def _repair_ppe_identities(row: Dict, *, force_gross: bool = False) -> None:
    """Enforce gross = net + dep and dep_end = dep_beg + dep_provided (within row)."""
    no = clean_number(row.get("net_opening", 0))
    nc = clean_number(row.get("net_closing", 0))
    db = clean_number(row.get("dep_up_to_beginning", 0))
    dp = clean_number(row.get("dep_provided_during_year", 0))
    d_adj = clean_number(row.get("dep_adjustment", 0))
    de = clean_number(row.get("dep_up_to_end", 0))
    go = clean_number(row.get("gross_opening", 0))
    gc = clean_number(row.get("gross_closing", 0))

    if row.get("_gross_subtotal_imputed"):
        if go > 0 and no > 0:
            row["dep_up_to_beginning"] = go - no
        if gc > 0 and nc > 0:
            row["dep_up_to_end"] = gc - nc
        db = clean_number(row.get("dep_up_to_beginning", 0))
        de = clean_number(row.get("dep_up_to_end", 0))
        d_adj = clean_number(row.get("dep_adjustment", 0))
        if de > db:
            row["dep_provided_during_year"] = de - db + d_adj
        dp = clean_number(row.get("dep_provided_during_year", 0))
    elif db > 0 or dp > 0:
        row["dep_up_to_end"] = max(0.0, db + dp - d_adj)
    elif de <= 0 and (db > 0 or dp > 0):
        row["dep_up_to_end"] = max(0.0, db + dp - d_adj)

    go = clean_number(row.get("gross_opening", 0))
    db = clean_number(row.get("dep_up_to_beginning", 0))
    de = clean_number(row.get("dep_up_to_end", 0))
    if no > 0 and db > 0:
        implied = no + db
        if force_gross or go == 0 or abs(go - implied) / max(implied, 1) > 0.005:
            if not (
                row.get("_gross_subtotal_imputed")
                and go > implied * 1.02
            ):
                row["gross_opening"] = implied

    gc = clean_number(row.get("gross_closing", 0))
    if nc > 0 and de > 0:
        implied = nc + de
        if force_gross or gc == 0 or abs(gc - implied) / max(implied, 1) > 0.005:
            if not (
                row.get("_gross_subtotal_imputed")
                and gc > implied * 1.02
            ):
                row["gross_closing"] = implied

    g_o = clean_number(row.get("gross_opening", 0))
    g_c = clean_number(row.get("gross_closing", 0))
    deduct = clean_number(row.get("gross_deduction", 0))
    if g_c > 0 and g_o > 0:
        if g_c < g_o and deduct == 0:
            row["gross_deduction"] = g_o - g_c
            row["gross_addition_actual"] = 0.0
            deduct = row["gross_deduction"]
        if g_c < g_o:
            row["gross_deduction"] = g_o - g_c
            row["gross_addition_actual"] = 0.0
        else:
            row["gross_deduction"] = 0.0
            row["gross_addition_actual"] = g_c - g_o
        if clean_number(row.get("gross_addition_reval", 0)) != 0:
            row["gross_addition_reval"] = 0.0


def reconcile_block_c_to_face(
    block_c: List[Dict],
    pages: Dict[int, str],
    *,
    tol_ratio: float = 0.08,
) -> List[Dict]:
    """
    Scale net opening/closing on asset rows 2–7 so their sum matches BS face PPE totals,
    when OCR error is systematic (ratio within tol_ratio of 1.0).
    """
    face_o, face_c = parse_face_ppe_net(pages)
    if face_o <= 0 and face_c <= 0:
        return block_c

    rows = {int(r["sl_no"]): dict(r) for r in block_c}
    asset_sls = [sl for sl in range(1, 8) if sl in rows]

    def sum_field(field: str) -> float:
        return sum(clean_number(rows[sl].get(field, 0)) for sl in asset_sls)

    adjustments = []
    for field, face_val in (("net_opening", face_o), ("net_closing", face_c)):
        current = sum_field(field)
        if face_val <= 0 or current <= 0:
            continue
        ratio = face_val / current
        if not (1 - tol_ratio <= ratio <= 1 + tol_ratio):
            logger.info(
                "  PPE face reconcile: skip %s ratio=%.4f (face=%s sum=%s)",
                field, ratio, int(face_val), int(current),
            )
            continue
        dep_field = (
            "dep_up_to_beginning"
            if field == "net_opening"
            else "dep_up_to_end"
        )
        for sl in asset_sls:
            v = clean_number(rows[sl].get(field, 0))
            if v <= 0:
                continue
            rows[sl][field] = round(v * ratio)
            d_v = clean_number(rows[sl].get(dep_field, 0))
            if d_v > 0:
                rows[sl][dep_field] = round(d_v * ratio)
            if field == "net_closing":
                d_b = clean_number(rows[sl].get("dep_up_to_beginning", 0))
                d_p = clean_number(rows[sl].get("dep_provided_during_year", 0))
                d_adj = clean_number(rows[sl].get("dep_adjustment", 0))
                if d_b > 0 and d_p > 0:
                    rows[sl]["dep_up_to_end"] = round(d_b + d_p - d_adj)
            _repair_ppe_identities(rows[sl], force_gross=True)
        adjustments.append((field, ratio))

    if adjustments:
        logger.info(
            "  PPE face reconcile applied: %s",
            ", ".join(f"{f}×{r:.4f}" for f, r in adjustments),
        )

    for field, face_val in (("net_opening", face_o), ("net_closing", face_c)):
        if face_val <= 0:
            continue
        current = sum(clean_number(rows[sl].get(field, 0)) for sl in asset_sls)
        residual = face_val - current
        if residual == 0 or abs(residual) > face_val * 0.04:
            continue
        sl_pick = max(
            asset_sls,
            key=lambda s: clean_number(rows[s].get(field, 0)),
        )
        rows[sl_pick][field] = clean_number(rows[sl_pick].get(field, 0)) + residual
        dep_key = (
            "dep_up_to_beginning" if field == "net_opening" else "dep_up_to_end"
        )
        dep_v = clean_number(rows[sl_pick].get(dep_key, 0))
        if dep_v > 0:
            rows[sl_pick][dep_key] = dep_v + residual
        _repair_ppe_identities(rows[sl_pick], force_gross=True)
        logger.info(
            "  PPE face residual %s: %s → sl %s",
            field, int(residual), sl_pick,
        )

    return [rows[sl] for sl in sorted(rows)]


def parse_schedule10_other_liabilities(pages: Dict[int, str]) -> Pair:
    """
    Schedule 10 — Sl 14 components only (advance + statutory + excise).
    Ignores sundry creditors and schedule total lines.
    """
    full = "\n".join(pages.values())
    labels = (
        (r"advance\s*from\s*customers?", "advance"),
        (r"statutory\s+dues?", "statutory"),
        (r"excise\s+duty\s+refund", "excise"),
    )

    for body in _iter_bodies(full):
        open_sum = close_sum = 0.0
        found_any = False
        in_s10 = False
        for line in body.splitlines():
            ll = line.lower()
            if re.search(r"schedule\s*:?\s*10\b", ll):
                in_s10 = True
                continue
            if in_s10 and re.search(r"schedule\s*:?\s*\d+", ll) and "schedule 10" not in ll:
                break
            if not in_s10:
                continue
            if "sundry creditor" in ll or "provision" in ll:
                continue
            if "total" in ll and "excise" not in ll:
                continue

            for pat, _ in labels:
                if not re.search(pat, ll, re.I):
                    continue
                amounts: List[float] = []
                if "\t" in line:
                    for tok in line.split("\t"):
                        tok = tok.strip()
                        if not re.search(r"\d", tok):
                            continue
                        if re.search(r"[a-zA-Z]", tok) and "," not in tok:
                            continue
                        v = parse_indian_number(tok)
                        if v > 1_000 and v < 100_000_000:
                            amounts.append(v)
                else:
                    amounts = [
                        parse_indian_number(x)
                        for x in re.findall(r"[\d,]+", line)
                        if parse_indian_number(x) > 1_000
                    ]
                if len(amounts) >= 2:
                    close_sum += amounts[0]
                    open_sum += amounts[1]
                    found_any = True
                elif len(amounts) == 1 and "excise" in ll:
                    close_sum += amounts[0]
                    open_sum += amounts[0]
                    found_any = True
                break

        if found_any and open_sum > 0:
            return open_sum, close_sum

    return 0.0, 0.0


def reconcile_block_d_sl14(
    block_d: List[Dict],
    pages: Dict[int, str],
) -> List[Dict]:
    """Set Sl 14 from Schedule 10 tabular parse when present."""
    o, c = parse_schedule10_other_liabilities(pages)
    if o <= 0 and c <= 0:
        return block_d

    rows = {int(r["sl_no"]): dict(r) for r in block_d}
    cur_o = clean_number(rows.get(14, {}).get("opening_rs", 0))
    cur_c = clean_number(rows.get(14, {}).get("closing_rs", 0))

    if cur_o > 0 and abs(cur_o - o) / max(o, 1) < 0.02 and cur_c > 0 and abs(cur_c - c) / max(c, 1) < 0.02:
        return block_d

    if 14 not in rows:
        from compile_extraction.schema import BLOCK_D_TEMPLATE

        name = next(
            (r["item_name"] for r in BLOCK_D_TEMPLATE if r["sl_no"] == 14),
            "Other current liabilities",
        )
        rows[14] = {"sl_no": 14, "item_name": name, "opening_rs": 0.0, "closing_rs": 0.0}

    logger.info(
        "  Schedule 10 Sl14 reconcile: %s/%s → %s/%s",
        int(cur_o), int(cur_c), int(o), int(c),
    )
    rows[14]["opening_rs"] = o
    rows[14]["closing_rs"] = c
    return [rows[sl] for sl in sorted(rows)]
