"""
Apply compile-sheet mapping rules (from config) to BS components → Block D rows.

Rules are learned from compile schedule PDFs + balance sheets; no hard-coded company amounts.
Profile is chosen by rule coverage on the parsed BS (not by company name or golden file).
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from compile_extraction.bs_components import BSComponents, extract_bs_components
from compile_extraction.schema import BLOCK_D_TEMPLATE, clean_number

logger = logging.getLogger(__name__)

_KEY_BLOCK_D_ROWS = (1, 2, 3, 5, 6, 8, 9, 10, 12, 13, 14, 17)

_RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "compile_mapping_rules.json"


def _load_rules() -> Dict[str, Any]:
    if not _RULES_PATH.is_file():
        return {}
    return json.loads(_RULES_PATH.read_text(encoding="utf-8")).get("profiles", {})


def _detect_profile_by_text(pages: Dict[int, str], profiles: Dict[str, Any]) -> Optional[str]:
    full = "\n".join(pages.values()).lower()
    for name, spec in profiles.items():
        for pat in spec.get("detect_patterns", []):
            if pat.lower() in full:
                return name
    if re.search(r"amounts?\s+.{0,6}lacs?|in\s+lacs?", full, re.I):
        return "lacs_corporate"
    if re.search(r"schedule\s*:?\s*8", full, re.I):
        return "rupees_schedule"
    return None


def _score_profile_rules(
    comp: BSComponents,
    rules: Dict[str, Any],
    name_by_sl: Dict[int, str],
) -> int:
    """How many Block D rows get non-zero amounts from this profile's formulas."""
    score = 0
    for sl_key, rule in rules.items():
        sl = int(sl_key)
        row = _row_from_spec(comp, sl, rule, name_by_sl)
        if not row:
            continue
        if clean_number(row.get("opening_rs")) or clean_number(row.get("closing_rs")):
            score += 1
    return score


def _select_profile(
    pages: Dict[int, str],
    profiles: Dict[str, Any],
    comp: BSComponents,
) -> Tuple[Optional[str], int, int]:
    """
    Pick mapping profile by coverage on extracted components (generic for any new BS).
    Falls back to OCR text patterns only when no row maps.
    """
    name_by_sl = {r["sl_no"]: r["item_name"] for r in BLOCK_D_TEMPLATE}
    best_name: Optional[str] = None
    best_score = -1
    for name, spec in profiles.items():
        rules = spec.get("block_d", {})
        sc = _score_profile_rules(comp, rules, name_by_sl)
        if sc > best_score:
            best_score = sc
            best_name = name
    if best_score <= 0:
        fallback = _detect_profile_by_text(pages, profiles)
        if fallback:
            best_score = _score_profile_rules(
                comp, profiles[fallback].get("block_d", {}), name_by_sl
            )
        return fallback, best_score, len(_KEY_BLOCK_D_ROWS)
    return best_name, best_score, len(_KEY_BLOCK_D_ROWS)


def _eval_formula(
    comp: BSComponents,
    spec: Dict[str, Any],
    col: int,
) -> float:
    if spec.get("zero"):
        return 0.0
    total = 0.0
    for key in spec.get("add", []):
        total += comp.get(key)[col]
    for key in spec.get("subtract", []):
        total -= comp.get(key)[col]
    return total


def _row_from_spec(
    comp: BSComponents,
    sl: int,
    rule: Dict[str, Any],
    name_by_sl: Dict[int, str],
) -> Optional[Dict]:
    opening_spec = rule.get("opening", rule)
    closing_spec = rule.get("closing", rule)
    opening = _eval_formula(comp, opening_spec, 0)
    closing = _eval_formula(comp, closing_spec, 1)
    if opening == 0 and closing == 0:
        return None
    return {
        "sl_no": sl,
        "item_name": name_by_sl.get(sl, ""),
        "opening_rs": opening,
        "closing_rs": closing,
    }


def apply_compile_mapping(
    pages: Dict[int, str],
    block_d: Optional[List[Dict]] = None,
) -> List[Dict]:
    """
    Build Block D rows using mapping rules + BS components.
    Merges with existing parsed rows: mapped sl wins when non-zero.
    """
    profiles = _load_rules()
    if not profiles:
        return block_d or []

    comp = extract_bs_components(pages)
    profile, filled, total = _select_profile(pages, profiles, comp)
    if not profile or profile not in profiles:
        logger.warning(
            "  Compile mapping: no profile matched (components=%s keys)",
            len(comp.values),
        )
        return block_d or []

    logger.info(
        "  Compile mapping profile=%s (coverage %s/%s rows, component_keys=%s)",
        profile,
        filled,
        total,
        len(comp.values),
    )
    rules = profiles[profile].get("block_d", {})
    name_by_sl = {r["sl_no"]: r["item_name"] for r in BLOCK_D_TEMPLATE}

    mapped: Dict[int, Dict] = {}
    for sl_key, rule in rules.items():
        sl = int(sl_key)
        row = _row_from_spec(comp, sl, rule, name_by_sl)
        if row:
            mapped[sl] = row

    if block_d:
        by_sl = {int(r["sl_no"]): dict(r) for r in block_d}
        for sl, row in mapped.items():
            if clean_number(row.get("opening_rs")) or clean_number(row.get("closing_rs")):
                by_sl[sl] = row
        return [by_sl[k] for k in sorted(by_sl)]

    return [mapped[k] for k in sorted(mapped)]
