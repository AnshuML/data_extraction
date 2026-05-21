"""Fixed Block C / Block D compile sheet schema and utilities."""
from __future__ import annotations

import json
import logging
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

BLOCK_C_TEMPLATE: List[Dict] = [
    {"sl_no": 1, "asset_type": "Land", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0},
    {"sl_no": 2, "asset_type": "Building", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0},
    {"sl_no": 3, "asset_type": "Plant and Machinery", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0},
    {"sl_no": 4, "asset_type": "Transport Equipment", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0},
    {"sl_no": 5, "asset_type": "Computer Equipment & software", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0},
    {"sl_no": 6, "asset_type": "Pollution control equipment", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0},
    {"sl_no": 7, "asset_type": "Others", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0},
    {"sl_no": 8, "asset_type": "Sub-total(2 to 7)", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0},
    {"sl_no": 9, "asset_type": "Capital Work in Progress", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0},
    {"sl_no": 10, "asset_type": "Total(1+8+9)", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0},
]

BLOCK_D_TEMPLATE: List[Dict] = [
    {"sl_no": 1, "item_name": "Raw Materials & Components and Packing materials", "opening_rs": 0.0, "closing_rs": 0.0},
    {"sl_no": 2, "item_name": "Fuels & Lubricants", "opening_rs": 0.0, "closing_rs": 0.0},
    {"sl_no": 3, "item_name": "Spares, Stores & others", "opening_rs": 0.0, "closing_rs": 0.0},
    {"sl_no": 4, "item_name": "Sub-Total(1 to 3)", "opening_rs": 0.0, "closing_rs": 0.0},
    {"sl_no": 5, "item_name": "Semi-finished goods/work in progress", "opening_rs": 0.0, "closing_rs": 0.0},
    {"sl_no": 6, "item_name": "Finished goods", "opening_rs": 0.0, "closing_rs": 0.0},
    {"sl_no": 7, "item_name": "Total inventory(4 to 6)", "opening_rs": 0.0, "closing_rs": 0.0},
    {"sl_no": 8, "item_name": "Cash in Hand & at Bank", "opening_rs": 0.0, "closing_rs": 0.0},
    {"sl_no": 9, "item_name": "Sundry Debtors", "opening_rs": 0.0, "closing_rs": 0.0},
    {"sl_no": 10, "item_name": "Other current assests", "opening_rs": 0.0, "closing_rs": 0.0},
    {"sl_no": 11, "item_name": "Total current assets(7 to 10)", "opening_rs": 0.0, "closing_rs": 0.0},
    {"sl_no": 12, "item_name": "Sundry creditors", "opening_rs": 0.0, "closing_rs": 0.0},
    {"sl_no": 13, "item_name": "Over draft,cash credit, other short term loan from banks & other financial institutions", "opening_rs": 0.0, "closing_rs": 0.0},
    {"sl_no": 14, "item_name": "Other current liabilities", "opening_rs": 0.0, "closing_rs": 0.0},
    {"sl_no": 15, "item_name": "Total Current liabilities(12 to 14)", "opening_rs": 0.0, "closing_rs": 0.0},
    {"sl_no": 16, "item_name": "Working Capital(11-15)", "opening_rs": 0.0, "closing_rs": 0.0},
    {"sl_no": 17, "item_name": "Outstanding loans(excluding interest but including deposits)", "opening_rs": 0.0, "closing_rs": 0.0},
]

_TEXT_FIELDS = {"asset_type", "item_name"}


def clean_number(val) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    try:
        s = str(val).replace(",", "").replace(" ", "").replace("-", "0")
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def extract_json_from_response(text: str) -> Optional[Dict]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if code_block:
        try:
            return json.loads(code_block.group(1))
        except json.JSONDecodeError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    return None


def merge_with_template(
    llm_data: Dict, template: List[Dict], key: str, id_field: str
) -> List[Dict]:
    result = [row.copy() for row in template]
    for llm_row in llm_data.get(key, []):
        match_val = llm_row.get(id_field)
        if match_val is None:
            continue
        for template_row in result:
            if template_row.get(id_field) == match_val:
                for k, v in llm_row.items():
                    if k == id_field or v is None:
                        continue
                    if k in _TEXT_FIELDS:
                        if isinstance(v, str) and v.strip():
                            template_row[k] = v.strip()
                    else:
                        template_row[k] = clean_number(v)
                break
    return result
