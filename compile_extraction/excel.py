"""Excel output for compile sheets."""
from __future__ import annotations

import logging
import os
from typing import Dict, List

import pandas as pd

from compile_extraction.schema import (
    BLOCK_C_TEMPLATE,
    BLOCK_D_TEMPLATE,
    BLOCK_C_FIELDS,
    BLOCK_D_FIELDS,
    clean_number,
    sanitize_block_c,
    sanitize_block_d,
)

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


def _restore_text_label(
    sl_no: int, value, name_map: Dict[int, str]
) -> str:
    """Keep labels from template when Excel stored them as 0/empty."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    if value not in (None, "", 0, 0.0):
        return str(value)
    return name_map.get(sl_no, "")


def normalize_block_c_from_excel(records: List[Dict]) -> List[Dict]:
    """Map Excel column headers back to internal Block C keys."""
    name_map = {r["sl_no"]: r["asset_type"] for r in BLOCK_C_TEMPLATE}
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
            row["asset_type"] = _restore_text_label(
                row["sl_no"], row.get("asset_type"), name_map
            )
            out.append(row)
    return out


def normalize_block_d_from_excel(records: List[Dict]) -> List[Dict]:
    """Map Excel column headers back to internal Block D keys."""
    name_map = {r["sl_no"]: r["item_name"] for r in BLOCK_D_TEMPLATE}
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
            row["item_name"] = _restore_text_label(
                row["sl_no"], row.get("item_name"), name_map
            )
            out.append(row)
    return out


_TEXT_FIELDS = {"asset_type", "item_name"}


def write_excel(block_c: List[Dict], block_d: List[Dict], output_path: str) -> None:
    logger.info("Writing Excel: %s", output_path)
    block_c = sanitize_block_c(block_c)
    block_d = sanitize_block_d(block_d)
    cols_c = [k for k in COLS_C if k in BLOCK_C_FIELDS]
    df_c = pd.DataFrame(
        [{k: r.get(k) for k in cols_c} for r in block_c],
        columns=cols_c,
    )
    df_d = pd.DataFrame(
        [{k: r.get(k) for k in ("sl_no", "item_name", "opening_rs", "closing_rs")} for r in block_d],
        columns=["sl_no", "item_name", "opening_rs", "closing_rs"],
    )
    for col in COLS_C:
        if col in _TEXT_FIELDS or col == "sl_no":
            continue
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
