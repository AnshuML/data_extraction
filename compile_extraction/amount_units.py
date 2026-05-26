"""
Unified amount parsing: Lacs / Lakhs, Crores, and plain Rupees → rupees.

All compile-sheet amounts are stored in rupees. Page context (headers) picks the unit.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from compile_extraction.schema import parse_indian_number

LAKHS_MULTIPLIER = 100_000.0
CRORE_MULTIPLIER = 10_000_000.0


class AmountUnit(str, Enum):
    RUPEES = "rupees"
    LAKHS = "lakhs"
    CRORES = "crores"


@dataclass(frozen=True)
class AmountContext:
    unit: AmountUnit
    multiplier: float

    @staticmethod
    def from_unit(unit: AmountUnit) -> "AmountContext":
        mult = {
            AmountUnit.RUPEES: 1.0,
            AmountUnit.LAKHS: LAKHS_MULTIPLIER,
            AmountUnit.CRORES: CRORE_MULTIPLIER,
        }[unit]
        return AmountContext(unit=unit, multiplier=mult)


def detect_amount_unit(text: str) -> AmountContext:
    """Infer reporting unit from page/schedule headers (generic)."""
    t = text.lower().replace(" ", "")
    if re.search(r"amounts?\s*in\s*crores?|in\s*crores?|₹?\s*in\s*cr\b", t):
        return AmountContext.from_unit(AmountUnit.CRORES)
    if re.search(
        r"amounts?\s*(?:in|m)\s*lacs?|amounts?\s*in\s*lakhs?|in\s*lacs?|in\s*lakhs?",
        t,
    ):
        return AmountContext.from_unit(AmountUnit.LAKHS)
    # Mizoram-style Schedule 5: 8-column grid with crore-scale Indian grouping
    if "netblock" in t and "grossblock" in t:
        return AmountContext.from_unit(AmountUnit.RUPEES)
    if re.search(r"property,plant|property\.plant", t) and "schedule" in t:
        if re.search(r"lacs?|lakhs?", t):
            return AmountContext.from_unit(AmountUnit.LAKHS)
    return AmountContext.from_unit(AmountUnit.RUPEES)


def parse_lakhs_decimal(val: str) -> float:
    """
    Parse a single token in Lacs (e.g. 4,656.93 / 30.505.25 / 3784.04) → lakh amount.
    Never uses Indian crore grouping (that is for rupees-only).
    """
    s = str(val).strip()
    s = re.sub(r":(?=\d)", ".", s)
    s = s.replace(":", ".")
    if not s:
        return 0.0
    s = re.sub(r"[^\d,\.]", "", s)
    if not s:
        return 0.0
    m_oc = re.match(r"^(\d)\.(\d{3})\.(\d)\.(\d)$", s)
    if m_oc:
        return float(f"{m_oc.group(1)}{m_oc.group(2)}.{m_oc.group(3)}{m_oc.group(4)}")
    m_triple = re.match(r"^(\d),(\d{3}),(\d{2,3})$", s)
    if m_triple:
        frac = m_triple.group(3)[:2]
        return float(f"{m_triple.group(1)}{m_triple.group(2)}.{frac}")
    # OCR: 30,50525 → 30505.25 (comma before last 2 fractional digits)
    m_lacs_comma = re.match(r"^(\d{1,3}),(\d{3})(\d{2})$", s)
    if m_lacs_comma and "." not in s:
        return float(f"{m_lacs_comma.group(1)}{m_lacs_comma.group(2)}.{m_lacs_comma.group(3)}")
    if s.count(".") > 1:
        parts = s.split(".")
        s = "".join(parts[:-1]) + "." + parts[-1]
    if s.count(",") >= 2:
        digits = re.sub(r"[^\d]", "", s)
        if len(digits) >= 4:
            return float(digits[:-2] + "." + digits[-2:]) if len(digits) > 2 else float(digits)
    if re.match(r"^\d{3,5}$", s) and "." not in s:
        n = float(s)
        if n >= 10_000:
            return n / 100.0
        return n
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return 0.0


def parse_token_to_rupees(
    token: str,
    ctx: Optional[AmountContext] = None,
    *,
    page_text: str = "",
) -> float:
    """Parse one OCR token to rupees using page unit context."""
    if ctx is None:
        ctx = detect_amount_unit(page_text) if page_text else AmountContext.from_unit(AmountUnit.RUPEES)
    s = str(token).strip()
    if not s or s in ("-", "—", "."):
        return 0.0

    if ctx.unit == AmountUnit.LAKHS:
        return parse_lakhs_decimal(s) * ctx.multiplier

    if ctx.unit == AmountUnit.CRORES:
        v = parse_lakhs_decimal(s) if "." in s or ("," in s and len(s) < 20) else parse_indian_number(s)
        if v < 1_000_000:
            return v * ctx.multiplier
        return v

    # Rupees: Indian lakh/crore grouping for multi-comma; else plain
    if "," in s and len([p for p in s.split(",") if re.search(r"\d", p)]) >= 2:
        return parse_indian_number(s)
    return parse_indian_number(s.replace(",", "")) if "," in s else float(
        re.sub(r"[^\d.]", "", s) or 0
    )


def parse_line_tokens_to_rupees(
    line: str,
    ctx: Optional[AmountContext] = None,
    *,
    page_text: str = "",
    min_rupees: float = 1_000.0,
) -> List[float]:
    """Extract all amounts from a line as rupees."""
    if ctx is None:
        ctx = detect_amount_unit(page_text) if page_text else AmountContext.from_unit(AmountUnit.RUPEES)
    out: List[float] = []
    for tok in re.findall(r"[\d,\.]+", line):
        if not tok or tok == ".":
            continue
        if re.search(r"[a-zA-Z]", tok) and "," not in tok and "." not in tok:
            continue
        v = parse_token_to_rupees(tok, ctx, page_text=page_text)
        if v in (2022.0, 2023.0, 2024.0, 2025.0):
            continue
        if 12_020 <= v <= 12_030:
            continue
        if v >= min_rupees:
            out.append(v)
    return out


def merge_page_contexts(pages: dict) -> AmountContext:
    """Pick dominant unit across PDF pages (summary page wins for lacs)."""
    votes: dict[AmountUnit, int] = {}
    for text in pages.values():
        u = detect_amount_unit(text).unit
        votes[u] = votes.get(u, 0) + 1
    if not votes:
        return AmountContext.from_unit(AmountUnit.RUPEES)
    # Prefer LAKHS if any summary page says so
    for text in pages.values():
        if re.search(r"balance\s+sheet", text, re.I) and re.search(
            r"in\s+lacs?|in\s+lakhs?", text, re.I
        ):
            return AmountContext.from_unit(AmountUnit.LAKHS)
    return AmountContext.from_unit(max(votes, key=votes.get))


def coerce_rupees_if_misscaled(
    value: float,
    ctx: AmountContext,
    *,
    ref_rupees: float = 0.0,
) -> float:
    """
    Fix values parsed with wrong unit (e.g. lacs token run through Indian grouping).
    """
    if value <= 0:
        return 0.0
    if ctx.unit != AmountUnit.LAKHS:
        return value
    if ref_rupees > 0 and _close_ratio(value, ref_rupees, 0.05):
        return value
    # 4656930 should be 465693000 (×100) when 100× too small vs peers
    if 1_000_000 < value < 50_000_000 and ref_rupees > 100_000_000:
        scaled = value * 100.0
        if _close_ratio(scaled, ref_rupees, 0.15):
            return scaled
    return value


def _close_ratio(a: float, b: float, tol: float) -> bool:
    if a == 0 and b == 0:
        return True
    return abs(a - b) / max(abs(a), abs(b), 1.0) <= tol
