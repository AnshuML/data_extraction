"""Excel output for compile sheets."""
from __future__ import annotations

import logging
import os
from typing import Dict, List

import pandas as pd

from compile_extraction.schema import clean_number

logger = logging.getLogger(__name__)

COLS_C = {
    "sl_no": "Sl No",
    "asset_type": "Types of Asset",
    "gross_opening": "Opening As On 01/04/2023",
    "gross_addition_reval": "Addition - Due to revaluation",
    "gross_addition_actual": "Addition - Actual addition",
    "gross_deduction": "Deduction & adjustment",
    "gross_closing": "Closing as on 31/03/2024",
    "dep_up_to_beginning": "Dep Up to year beginning",
    "dep_provided_during_year": "Dep Provided during the year",
    "dep_adjustment": "Dep Adjustment for sold",
    "dep_up_to_end": "Dep Up to year end",
    "net_opening": "Net Opening as on 01/04/2023",
    "net_closing": "Net Closing as on 31/03/2024",
}

COLS_D = {
    "sl_no": "SlNo.",
    "item_name": "Items",
    "opening_rs": "Opening (Rs.)",
    "closing_rs": "Closing (Rs.)",
}


INV_C = {v: k for k, v in COLS_C.items()}
INV_D = {v: k for k, v in COLS_D.items()}


def _normalize_sl_no(val) -> int:
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return 0


def normalize_block_c_from_excel(records: List[Dict]) -> List[Dict]:
    """Map Excel column headers back to internal Block C keys."""
    out: List[Dict] = []
    for raw in records:
        row: Dict = {}
        for k, v in raw.items():
            key = INV_C.get(k, k)
            if key in _TEXT_FIELDS:
                row[key] = v if v is not None else ""
            elif key != "sl_no":
                row[key] = clean_number(v)
            else:
                row[key] = _normalize_sl_no(v)
        if row.get("sl_no"):
            out.append(row)
    return out


def normalize_block_d_from_excel(records: List[Dict]) -> List[Dict]:
    """Map Excel column headers back to internal Block D keys."""
    out: List[Dict] = []
    for raw in records:
        row: Dict = {}
        for k, v in raw.items():
            key = INV_D.get(k, k)
            if key == "item_name":
                row[key] = str(v) if v is not None else ""
            elif key == "sl_no":
                row[key] = _normalize_sl_no(v)
            else:
                row[key] = clean_number(v)
        if row.get("sl_no"):
            out.append(row)
    return out


_TEXT_FIELDS = {"asset_type", "item_name"}


def write_excel(block_c: List[Dict], block_d: List[Dict], output_path: str) -> None:
    logger.info("Writing Excel: %s", output_path)
    df_c = pd.DataFrame(block_c)
    df_d = pd.DataFrame(block_d)
    for col in COLS_C:
        if col in df_c.columns:
            df_c[col] = pd.to_numeric(df_c[col], errors="coerce").fillna(0)
    for col in ("opening_rs", "closing_rs"):
        if col in df_d.columns:
            df_d[col] = pd.to_numeric(df_d[col], errors="coerce").fillna(0)
    df_c.rename(columns=COLS_C, inplace=True, errors="ignore")
    df_d.rename(columns=COLS_D, inplace=True, errors="ignore")
    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        df_c.to_excel(writer, sheet_name="Block C - Fixed Assets", index=False)
        df_d.to_excel(writer, sheet_name="Block D - Working Capital", index=False)
    logger.info("Excel saved: %s", output_path)
