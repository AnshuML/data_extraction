"""
Single source of truth for Block C and Block D schemas.
Every other file imports from here — never duplicate schema definitions.
"""
from typing import List, Dict, Any
import copy

# ─────────────────────────────────────────────────────────────────────────────
# BLOCK C — Fixed Assets Schedule (10 rows × 13 columns)
# ─────────────────────────────────────────────────────────────────────────────

BLOCK_C_COLUMN_ALIASES: Dict[str, List[str]] = {
    "gross_opening":            ["opening", "gross opening", "opening as on", "opening value", "value at beginning"],
    "gross_addition_reval":     ["revaluation", "reval", "addition due to reval", "addition reval"],
    "gross_addition_actual":    ["actual addition", "addition", "actual", "additions during"],
    "gross_deduction":          ["deduction", "deductions", "sold", "discarded", "adjustment deduction"],
    "gross_closing":            ["closing", "gross closing", "closing as on", "closing value", "value at end"],
    "dep_up_to_beginning":      ["dep opening", "depreciation opening", "dep upto beginning", "accum dep opening",
                                 "depreciation up to beginning", "up to year beginning"],
    "dep_provided_during_year": ["dep during year", "depreciation during year", "provided during",
                                 "dep for the year", "depreciation for year"],
    "dep_adjustment":           ["dep adj", "dep on sold", "dep on discarded", "depreciation adjustment",
                                 "adj for sold"],
    "dep_up_to_end":            ["dep closing", "dep upto end", "depreciation closing", "dep up to end",
                                 "accum dep closing", "up to year end", "upto end of year",
                                 "total depreciation", "accumulated depreciation"],
    "net_opening":              ["net opening", "wdv opening", "net value opening", "wdv at beginning",
                                 "written down value opening", "book value opening"],
    "net_closing":              ["net closing", "wdv closing", "net value closing", "wdv at end", "net block",
                                 "written down value closing", "book value closing", "wdv"],
}

BLOCK_C_CANONICAL_ROWS: List[Dict[str, Any]] = [
    {"sl_no": 1,  "asset_type": "Land"},
    {"sl_no": 2,  "asset_type": "Building"},
    {"sl_no": 3,  "asset_type": "Plant and Machinery"},
    {"sl_no": 4,  "asset_type": "Transport Equipment"},
    {"sl_no": 5,  "asset_type": "Computer Equipment & Software"},
    {"sl_no": 6,  "asset_type": "Pollution Control Equipment"},
    {"sl_no": 7,  "asset_type": "Others"},
    {"sl_no": 8,  "asset_type": "Sub-total (2 to 7)"},
    {"sl_no": 9,  "asset_type": "Capital Work in Progress"},
    {"sl_no": 10, "asset_type": "Total (1+8+9)"},
]

BLOCK_C_ROW_ALIASES: Dict[str, List[str]] = {
    "Land":                            ["land", "freehold land", "leasehold land"],
    "Building":                        ["building", "buildings", "factory building", "office building"],
    "Plant and Machinery":             ["plant and machinery", "plant & machinery", "plant & mach",
                                        "p & m", "p&m", "machinery", "plant"],
    "Transport Equipment":             ["transport equipment", "transport", "vehicles", "vehicle",
                                        "motor vehicle", "motor vehicles"],
    "Computer Equipment & Software":   ["computer equipment", "computer & software", "computers",
                                        "it equipment", "computer equipment & software",
                                        "computer equipment and software"],
    "Pollution Control Equipment":     ["pollution control", "pollution control equipment", "etp",
                                        "effluent treatment"],
    "Others":                          ["others", "other assets", "miscellaneous", "misc assets",
                                        "other fixed assets", "furniture", "furniture & fixtures",
                                        "office equipment"],
    "Sub-total (2 to 7)":              ["sub total", "sub-total", "subtotal", "sub total (2 to 7)",
                                        "sub-total(2 to 7)"],
    "Capital Work in Progress":        ["capital work in progress", "cwip", "capital wip",
                                        "work in progress", "wip"],
    "Total (1+8+9)":                   ["total", "grand total", "total (1+8+9)", "total(1+8+9)",
                                        "total fixed assets"],
}

NUMERIC_ZERO: Dict[str, float] = {
    "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0,
    "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0,
    "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0,
    "net_opening": 0.0, "net_closing": 0.0,
}


def make_block_c_template() -> List[Dict[str, Any]]:
    """Returns a fresh 10-row Block C template with all values = 0.0."""
    rows = []
    for r in BLOCK_C_CANONICAL_ROWS:
        row = {**r, **copy.deepcopy(NUMERIC_ZERO), "_confidence": {}}
        rows.append(row)
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# BLOCK D — Working Capital (17 rows × 3 columns)
# ─────────────────────────────────────────────────────────────────────────────

BLOCK_D_COLUMN_ALIASES: Dict[str, List[str]] = {
    "opening_rs": ["opening", "opening balance", "opening rs", "opening (rs)", "as at beginning",
                   "previous year", "as at 01/04"],
    "closing_rs": ["closing", "closing balance", "closing rs", "closing (rs)", "as at end",
                   "current year", "as at 31/03"],
}

BLOCK_D_CANONICAL_ROWS: List[Dict[str, Any]] = [
    {"sl_no": 1,  "item_name": "Raw Materials & Components and Packing Materials"},
    {"sl_no": 2,  "item_name": "Fuels & Lubricants"},
    {"sl_no": 3,  "item_name": "Spares, Stores & Others"},
    {"sl_no": 4,  "item_name": "Sub-Total (1 to 3)"},
    {"sl_no": 5,  "item_name": "Semi-finished Goods / Work in Progress"},
    {"sl_no": 6,  "item_name": "Finished Goods"},
    {"sl_no": 7,  "item_name": "Total Inventory (4 to 6)"},
    {"sl_no": 8,  "item_name": "Cash in Hand & at Bank"},
    {"sl_no": 9,  "item_name": "Sundry Debtors"},
    {"sl_no": 10, "item_name": "Other Current Assets"},
    {"sl_no": 11, "item_name": "Total Current Assets (7 to 10)"},
    {"sl_no": 12, "item_name": "Sundry Creditors"},
    {"sl_no": 13, "item_name": "Overdraft / Cash Credit / Short Term Loans"},
    {"sl_no": 14, "item_name": "Other Current Liabilities"},
    {"sl_no": 15, "item_name": "Total Current Liabilities (12 to 14)"},
    {"sl_no": 16, "item_name": "Working Capital (11-15)"},
    {"sl_no": 17, "item_name": "Outstanding Loans (excl. interest, incl. deposits)"},
]

BLOCK_D_ROW_ALIASES: Dict[str, List[str]] = {
    "Raw Materials & Components and Packing Materials": [
        "raw materials", "raw material", "raw materials & components", "packing materials",
        "raw materials & components and packing materials", "raw mat"
    ],
    "Fuels & Lubricants": ["fuels", "fuel", "lubricants", "fuels & lubricants", "fuel & lubricants"],
    "Spares, Stores & Others": [
        "spares", "stores", "spare parts", "spares stores", "spares, stores & others",
        "spares and stores"
    ],
    "Sub-Total (1 to 3)": ["sub total", "sub-total", "subtotal", "sub total (1 to 3)", "sub-total(1 to 3)"],
    "Semi-finished Goods / Work in Progress": [
        "semi finished", "semi-finished", "work in progress", "wip", "semi finished goods",
        "semi-finished goods/work in progress"
    ],
    "Finished Goods": ["finished goods", "finished stock", "finished products"],
    "Total Inventory (4 to 6)": [
        "total inventory", "total stock", "inventory total", "total inventory(4 to 6)"
    ],
    "Cash in Hand & at Bank": [
        "cash", "cash in hand", "cash at bank", "cash and bank", "cash in hand & at bank",
        "cash & bank balance"
    ],
    "Sundry Debtors": [
        "sundry debtors", "debtors", "trade debtors", "accounts receivable", "receivables"
    ],
    "Other Current Assets": [
        "other current assets", "other assets", "miscellaneous current assets",
        "prepaid", "advances", "loans and advances"
    ],
    "Total Current Assets (7 to 10)": [
        "total current assets", "current assets total", "total current assets(7 to 10)"
    ],
    "Sundry Creditors": [
        "sundry creditors", "creditors", "trade creditors", "accounts payable", "payables"
    ],
    "Overdraft / Cash Credit / Short Term Loans": [
        "overdraft", "cash credit", "od", "cc", "short term loan", "bank overdraft",
        "over draft", "over draft cash credit", "other short term loan"
    ],
    "Other Current Liabilities": [
        "other current liabilities", "other liabilities", "provisions",
        "outstanding expenses"
    ],
    "Total Current Liabilities (12 to 14)": [
        "total current liabilities", "current liabilities total", "total current liabilities(12 to 14)"
    ],
    "Working Capital (11-15)": [
        "working capital", "net working capital", "working capital(11-15)"
    ],
    "Outstanding Loans (excl. interest, incl. deposits)": [
        "outstanding loans", "loans outstanding", "term loans", "long term loans",
        "outstanding loans excluding interest", "outstanding loans(excluding interest"
    ],
}


def make_block_d_template() -> List[Dict[str, Any]]:
    """Returns a fresh 17-row Block D template with all values = 0.0."""
    rows = []
    for r in BLOCK_D_CANONICAL_ROWS:
        row = {**r, "opening_rs": 0.0, "closing_rs": 0.0, "_confidence": {}}
        rows.append(row)
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def empty_result() -> Dict[str, Any]:
    return {
        "block_c": make_block_c_template(),
        "block_d": make_block_d_template(),
    }


def to_export_dict(result: Dict[str, Any]) -> Dict[str, Any]:
    """Strip internal _confidence keys before export."""
    def clean(rows):
        return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    return {
        "block_c": clean(result.get("block_c", [])),
        "block_d": clean(result.get("block_d", [])),
    }
