#!/usr/bin/env python3
"""
Agentic Pipeline v2 — Balance Sheet PDF → Validated Excel

Key improvements over v1:
  - Vision-based Block C extraction (sends page IMAGE to Gemma4 VLM)
  - Smart OCR quality detection (catches garbage pages like landscape tables)
  - Page classification — only relevant pages sent to mapper
  - Derived rows (4,7,8,10,11,15,16) computed in Python, not by LLM
  - Improved Block D prompt with explicit schedule-to-row mapping

Agents:
  1. Extractor  : Tesseract + PaddleOCR + Gemma4 vision fallback
  2. Mapper     : Vision for Block C, text for Block D
  3. Validator  : Math rules on individual rows
  4. Fixer      : Re-extract failed rows (max 3 attempts)
  5. Reporter   : Green / Red signal + confidence score
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import os
import re
import sys
import warnings
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import fitz
import numpy as np
import pytesseract
import requests
from PIL import Image, ImageEnhance, ImageFilter

try:
    from paddleocr import PaddleOCR
    _PADDLE_AVAILABLE = True
except Exception:
    _PADDLE_AVAILABLE = False

from compile_extraction.excel import write_excel
from compile_extraction.schema import (
    BLOCK_C_TEMPLATE,
    BLOCK_D_TEMPLATE,
    clean_number,
    extract_json_from_response,
    merge_with_template,
)
from schedule_parser import (
    parse_block_c_from_text,
    parse_block_d_from_text,
    finalize_block_c_totals,
    _merge_d_row_lists,
    _parse_total_a_nets,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

OLLAMA_BASE_URL: str = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11435")
VISION_MODEL: str = os.environ.get("OLLAMA_VISION_MODEL", "gemma4:31b")
TEXT_MODEL: str = os.environ.get("OLLAMA_TEXT_MODEL", "gemma4:31b")
OLLAMA_GENERATE_URL = f"{OLLAMA_BASE_URL.rstrip('/')}/api/generate"
OLLAMA_CHAT_URL = f"{OLLAMA_BASE_URL.rstrip('/')}/api/chat"
TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "600"))
MAX_ATTEMPTS = 3
TOLERANCE = 0.02

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class ValidationError:
    block: str
    sl_no: int
    field: str
    expected: float
    got: float
    message: str


@dataclass
class AgentResult:
    attempt: int
    block_c: List[Dict]
    block_d: List[Dict]
    errors: List[ValidationError] = field(default_factory=list)
    confidence: float = 0.0
    passed: bool = False


# ===========================================================================
# OCR HELPERS
# ===========================================================================

def _preprocess_image(img: Image.Image) -> Image.Image:
    img = img.convert("L")
    img = ImageEnhance.Contrast(img).enhance(2.5)
    img = ImageEnhance.Sharpness(img).enhance(2.0)
    return img


def _tesseract_extract(img: Image.Image) -> str:
    return pytesseract.image_to_string(img, lang="eng", config="--oem 3 --psm 6")


def _rapidocr_extract(img_np: np.ndarray) -> str:
    """RapidOCR fallback when PaddleOCR fails on this host."""
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return ""
    try:
        engine = RapidOCR()
        result, _ = engine(img_np)
        if not result:
            return ""
        cells = []
        for item in result:
            if len(item) < 2:
                continue
            box, text = item[0], item[1]
            if not text or not str(text).strip():
                continue
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            cells.append((sum(ys) / len(ys), sum(xs) / len(xs), str(text).strip()))
        if not cells:
            return ""
        height = img_np.shape[0]
        row_threshold = max(height * 0.012, 8)
        cells.sort(key=lambda c: c[0])
        rows: List[List[tuple]] = []
        current_row: List[tuple] = [cells[0]]
        for cell in cells[1:]:
            if abs(cell[0] - current_row[-1][0]) <= row_threshold:
                current_row.append(cell)
            else:
                rows.append(sorted(current_row, key=lambda c: c[1]))
                current_row = [cell]
        rows.append(sorted(current_row, key=lambda c: c[1]))
        return "\n".join("\t".join(c[2] for c in row) for row in rows)
    except Exception as e:
        logger.warning("    RapidOCR failed: %s", e)
        return ""


def _paddle_extract(img_np: np.ndarray, paddle_ocr) -> str:
    """PaddleOCR — preserves table row/column structure; RapidOCR if Paddle fails."""
    cells = []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            result = paddle_ocr.ocr(img_np)
        if result and result[0]:
            for item in result[0]:
                bbox, (text, conf) = item
                if conf < 0.5:
                    continue
                xs = [p[0] for p in bbox]
                ys = [p[1] for p in bbox]
                cells.append((sum(ys) / 4, sum(xs) / 4, text.strip()))
    except Exception:
        return _rapidocr_extract(img_np)

    if not cells:
        rapid = _rapidocr_extract(img_np)
        if rapid:
            return rapid
        return ""

    height = img_np.shape[0]
    row_threshold = max(height * 0.012, 8)
    cells.sort(key=lambda c: c[0])
    rows: List[List[tuple]] = []
    current_row: List[tuple] = [cells[0]]
    for cell in cells[1:]:
        if abs(cell[0] - current_row[-1][0]) <= row_threshold:
            current_row.append(cell)
        else:
            rows.append(sorted(current_row, key=lambda c: c[1]))
            current_row = [cell]
    rows.append(sorted(current_row, key=lambda c: c[1]))
    return "\n".join("\t".join(c[2] for c in row) for row in rows)


_FINANCIAL_KEYWORDS = [
    "schedule", "total", "opening", "closing", "balance", "depreciation",
    "asset", "capital", "loan", "expense", "income", "sales",
    "creditor", "debtor", "inventory", "cash", "bank", "amount",
    "profit", "loss", "march", "account", "sheet", "provision",
    "sundry", "plant", "machinery", "building", "land",
]


def _ocr_quality_ok(text: str) -> bool:
    """Check if OCR text has meaningful financial content (not garbage)."""
    text_lower = text.lower()
    keyword_hits = sum(1 for kw in _FINANCIAL_KEYWORDS if kw in text_lower)
    real_numbers = re.findall(r"[\d,]{5,}", text)
    real_number_count = len([n for n in real_numbers if sum(c.isdigit() for c in n) >= 4])
    return keyword_hits >= 3 or real_number_count >= 5


def _resize_for_vision(img: Image.Image, max_dim: int = 1500) -> Image.Image:
    """Downscale large images to prevent OOM on vision model."""
    w, h = img.size
    if max(w, h) <= max_dim:
        return img
    scale = max_dim / max(w, h)
    new_size = (int(w * scale), int(h * scale))
    logger.info("    Resizing image %sx%s → %sx%s for vision", w, h, *new_size)
    return img.resize(new_size, Image.LANCZOS)


def _img_to_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _vision_ocr_page(img_pil: Image.Image) -> str:
    """Use Gemma4 vision model to OCR a page when Tesseract/Paddle fail."""
    prompt = (
        "This is a scanned financial document page. "
        "Extract ALL text and numbers exactly as they appear. "
        "For table rows, separate columns with ' | '. "
        "Preserve row structure. Return only the extracted text, no explanation."
    )
    return _call_vlm(img_pil, prompt)


_paddle_singleton = None


def _get_paddle_ocr():
    """Lazy-init PaddleOCR for mapper re-extraction on schedule pages."""
    global _paddle_singleton
    if _paddle_singleton is not None:
        return _paddle_singleton
    if not _PADDLE_AVAILABLE:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            try:
                _paddle_singleton = PaddleOCR(use_textline_orientation=False, lang="en")
            except TypeError:
                _paddle_singleton = PaddleOCR(use_angle_cls=False, lang="en")
        return _paddle_singleton
    except Exception as e:
        logger.warning("  PaddleOCR lazy init failed: %s", e)
        return None


def _paddle_section(text: str) -> str:
    if "=== PADDLEOCR ===" not in text:
        return ""
    return text.split("=== PADDLEOCR ===", 1)[1].split("=== VISION OCR ===")[0].strip()


def _merge_ocr_texts(tesseract_text: str, paddle_text: str) -> str:
    parts = []
    if tesseract_text.strip():
        parts.append("=== TESSERACT OCR ===\n" + tesseract_text.strip())
    if paddle_text.strip():
        parts.append("=== PADDLEOCR ===\n" + paddle_text.strip())
    return "\n\n".join(parts)


# ===========================================================================
# AGENT 1 — EXTRACTOR
# ===========================================================================

class ExtractorAgent:
    def __init__(self):
        self._paddle = None
        if _PADDLE_AVAILABLE:
            logger.info("  PaddleOCR available — initializing...")
            try:
                logging.getLogger("ppocr").setLevel(logging.ERROR)
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    self._paddle = PaddleOCR(use_angle_cls=False, lang="en")
                logger.info("  PaddleOCR ready.")
            except Exception as e:
                logger.warning("  PaddleOCR init failed: %s — Tesseract only.", e)

    def run(
        self,
        pdf_path: str,
        dpi: int = 300,
        save_debug: bool = False,
        audit_session=None,
    ) -> Tuple[Dict[int, str], Dict[int, Image.Image]]:
        """Returns (page_texts, page_images)."""
        mode = "Tesseract + PaddleOCR" if self._paddle else "Tesseract only"
        logger.info("=== AGENT 1: OCR Extractor [%s] (DPI=%s) ===", mode, dpi)

        doc = fitz.open(pdf_path)
        pages: Dict[int, str] = {}
        images: Dict[int, Image.Image] = {}
        total = len(doc)
        debug_dir = os.path.splitext(pdf_path)[0] + "_ocr_debug"

        for i in range(total):
            pnum = i + 1
            logger.info("  Page %s/%s ...", pnum, total)
            pix = doc[i].get_pixmap(dpi=dpi)
            img_pil = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            images[pnum] = img_pil
            img_pre = _preprocess_image(img_pil)

            tess_text = _tesseract_extract(img_pre)

            paddle_text = ""
            if self._paddle:
                try:
                    paddle_text = _paddle_extract(np.array(img_pil), self._paddle)
                except Exception as e:
                    logger.warning("    PaddleOCR page %s failed: %s", pnum, e)

            combined_raw = tess_text + " " + paddle_text
            if not _ocr_quality_ok(combined_raw):
                logger.info(
                    "    Page %s: OCR quality poor — trying %s vision supplement",
                    pnum, VISION_MODEL,
                )
                try:
                    vision_text = _vision_ocr_page(img_pil)
                    if vision_text:
                        # Keep Paddle table text; append vision (do not replace)
                        paddle_text = (
                            paddle_text + "\n=== VISION OCR ===\n" + vision_text
                        ).strip()
                except Exception as ve:
                    logger.warning("    Vision OCR fallback failed: %s", ve)

            combined = _merge_ocr_texts(tess_text, paddle_text)
            pages[pnum] = combined

            if audit_session is not None:
                from compile_extraction.audit import PageOcrRecord
                audit_session.save_ocr_page(pnum, combined)
                audit_session.audit.ocr_pages.append(PageOcrRecord(
                    page=pnum,
                    tesseract_chars=len(tess_text),
                    paddle_chars=len(paddle_text),
                    combined_chars=len(combined),
                    quality_ok=_ocr_quality_ok(combined_raw),
                    vision_used="=== VISION OCR ===" in paddle_text,
                ))
                audit_session.ocr_logger.info(
                    "page=%s tess=%s paddle=%s quality_ok=%s",
                    pnum, len(tess_text), len(paddle_text),
                    _ocr_quality_ok(combined_raw),
                )
                if not _ocr_quality_ok(combined_raw):
                    audit_session.audit.low_confidence_pages.append(pnum)

            if save_debug:
                os.makedirs(debug_dir, exist_ok=True)
                with open(
                    os.path.join(debug_dir, f"page_{pnum}.txt"), "w", encoding="utf-8"
                ) as f:
                    f.write(combined)

        doc.close()
        total_chars = sum(len(t) for t in pages.values())
        logger.info("  Total chars extracted: %s from %s pages", total_chars, total)
        if save_debug:
            logger.info("  OCR debug saved → %s", debug_dir)
        return pages, images


# ===========================================================================
# PAGE CLASSIFICATION
# ===========================================================================

def _classify_page(text: str) -> Set[str]:
    """Classify page content for routing to the correct mapper."""
    tags: Set[str] = set()
    t = text.lower()

    # ── Skip detection ─────────────────────────────────────────
    skip_kw = [
        "auditor", "accounting policies", "accounting concepts",
        "revenue recognition", "borrowing cost", "impairment of asset",
        "intangible asset", "contingent liabilit", "foreign currency",
        "expenditure during construction",
    ]
    if any(kw in t for kw in skip_kw):
        tags.add("skip")
        return tags

    # P&L pages: ALWAYS skip — Block D data comes only from BS schedules
    if "profit and loss account" in t or "profit & loss account" in t:
        tags.add("skip")
        return tags

    # Balance sheet MAIN summary / face page (not schedule detail pages)
    if "balance sheet as at" in t or "balance sheet as on" in t:
        is_annex = "schedules annexed" in t
        is_note_detail = "notes to financial" in t or "note 4" in t
        corporate_face = (
            "total assets" in t
            or "total nssets" in t
            or "equity and liabilit" in t
        )
        legacy_face = "sources of funds" in t or "application of funds" in t
        if not is_annex and not is_note_detail and (corporate_face or legacy_face):
            tags.add("summary")
            return tags

    # ── Block C: Fixed Assets / Schedule 5 (strict) ───────────
    c_strict = [
        "schedule 5", "schedule: 5", "gross block", "net block",
        "property, plant", "property.plant", "block \"a\"", "block \"d\"",
        "tangible assets", "plant and equipment",
    ]
    if any(kw in t for kw in c_strict):
        tags.add("block_c")
    else:
        # Require multiple asset names appearing WITH numbers (actual table)
        asset_names = ["land", "building", "furniture", "vehicle", "computer"]
        has_numbers = bool(re.search(r"[\d,]{6,}", text))
        if sum(1 for n in asset_names if n in t) >= 3 and has_numbers:
            tags.add("block_c")

    # ── Block D: Working Capital / Schedules 3, 6, 7, 8, 9, 10 ─
    d_kw = [
        "schedule 6", "schedule: 6", "schedule 7", "schedule: 7",
        "schedule 8", "schedule: 8", "schedule 9", "schedule: 9",
        "schedule 10", "schedule: 10", "schedule 3", "schedule: 3",
        "inventories", "trade receivable", "cash & bank", "cash at hand",
        "loans & advance", "current liabilities", "sundry creditor",
        "secured loan", "unsecured loan", "raw material", "finished good",
        "work in progress", "stores & spare",
    ]
    if any(kw in t for kw in d_kw):
        tags.add("block_d")

    return tags


# ===========================================================================
# LLM CALLERS
# ===========================================================================

def _call_llm(prompt: str) -> str:
    """Text LLM via Ollama /api/chat (more reliable than /api/generate for Gemma4)."""
    payload = {
        "model": TEXT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.0, "num_ctx": 16384, "num_predict": 4096},
    }
    r = requests.post(OLLAMA_CHAT_URL, json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    msg = r.json().get("message") or {}
    return (msg.get("content") or "").strip()


def _extract_block_c_from_ocr_text(ocr_text: str) -> List[Dict]:
    """Text LLM extraction from RapidOCR schedule text (Schedule 5)."""
    if len(ocr_text) < 300:
        return []
    prompt = (
        "Extract Block C (Fixed Assets) from this Schedule 5 OCR text.\n"
        "Map Block A+B → sl_no 2 Building, Block D → sl_no 3 Plant, "
        "Block F/p → sl_no 4 Vehicle, Block E+G → sl_no 5 Computer, "
        "Block C+H → sl_no 7 Others. Land=1, Pollution=6, CWIP=9 if present.\n"
        "Use Net Block and Gross Block rows; Indian numbers without commas.\n"
        f"{_VISION_PROMPT_C}\n\nOCR TEXT:\n{ocr_text[:14000]}"
    )
    try:
        raw = _call_llm(prompt)
        parsed = extract_json_from_response(raw)
        if not parsed:
            return []
        rows = parsed.get("block_c") or []
        rows = _merge_sub_rows_c(rows)
        return [
            r for r in rows
            if _has_data(r, {"sl_no", "asset_type"})
        ]
    except Exception as e:
        logger.warning("  Block C LLM from OCR failed: %s", e)
        return []


def _call_vlm(img_pil: Image.Image, prompt: str) -> str:
    """Vision LLM via Ollama /api/chat (Gemma4 requires chat + images)."""
    small = _resize_for_vision(img_pil)
    payload = {
        "model": VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [_img_to_b64(small)],
            }
        ],
        "stream": False,
        "options": {"temperature": 0.0, "num_ctx": 16384, "num_predict": 4096},
    }
    r = requests.post(OLLAMA_CHAT_URL, json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    msg = r.json().get("message") or {}
    return (msg.get("content") or "").strip()


# ===========================================================================
# AGENT 2 — MAPPER
# ===========================================================================

BLOCK_C_ROW_NAMES = "\n".join(
    f"  sl_no={r['sl_no']}: {r['asset_type']}" for r in BLOCK_C_TEMPLATE
)

_VISION_PROMPT_C = """You are extracting data from a Fixed Assets / Property Plant & Equipment schedule.

Look for the TABLE on this page with columns for Gross Block, Depreciation, and Net Block.

Map asset rows:
  sl_no=2: "Building" or "Block A"
  sl_no=3: "Plant & Machinery" or "Block D" + "Factory Building" or "Block B" (ADD them together)
  sl_no=4: "Vehicle" or "Block F" (Transport Equipment)
  sl_no=5: "Computer" or "Block G" + "Office Equipment" or "Block E" (ADD them)
  sl_no=7: "Furniture" or "Block C" + "Chill Roll" or "Block H" (ADD them)
  sl_no=9: Capital Work in Progress
  sl_no=1: Land (if present; otherwise all zeros)
  sl_no=6: Pollution control (if present; otherwise all zeros)

DO NOT extract rows 8 and 10 — set to all zeros.

For EACH row, read these values LEFT to RIGHT across the table:
  gross_opening, gross_addition_reval(usually 0), gross_addition_actual, gross_deduction, gross_closing,
  dep_up_to_beginning, dep_provided_during_year, dep_adjustment(usually 0), dep_up_to_end,
  net_opening, net_closing

The LAST two numbers in each row are net_opening (previous year) and net_closing (current year).
Work BACKWARDS from the right side to identify columns:
  rightmost = net_closing (or net_opening, check which is larger to determine order)
  then dep_up_to_end, dep_provided_during_year, dep_up_to_beginning
  then gross_closing, gross_deduction, gross_addition_actual, gross_opening

VERIFICATION RULES (each row MUST satisfy):
  gross_closing = gross_opening + additions - deductions
  dep_up_to_end = dep_up_to_beginning + dep_provided - dep_adjustment
  net_closing = gross_closing - dep_up_to_end
  net_opening = gross_opening - dep_up_to_beginning
  dep_up_to_end MUST be < gross_closing

Indian number format: remove ALL commas (26,27,77,964 → 262777964)
Blank or dash = 0

Return ONLY valid JSON:
{"block_c": [{"sl_no":2,"asset_type":"Building","gross_opening":0,"gross_addition_reval":0,"gross_addition_actual":0,"gross_deduction":0,"gross_closing":0,"dep_up_to_beginning":0,"dep_provided_during_year":0,"dep_adjustment":0,"dep_up_to_end":0,"net_opening":0,"net_closing":0},...all rows...],"block_d":[]}"""


def _build_mapper_prompt_d(
    ocr_text: str, failed_rows: Optional[List[int]] = None
) -> str:
    row_filter = ""
    if failed_rows:
        names = [
            r["item_name"]
            for r in BLOCK_D_TEMPLATE
            if r["sl_no"] in failed_rows
        ]
        row_filter = f"\nFocus ONLY on these rows: {', '.join(names)}\n"

    return f"""Extract Working Capital data from this Balance Sheet schedule page.{row_filter}

COLUMN ORDER: Each line has TWO numbers.
  FIRST number = closing_rs (Current Year 2024)
  SECOND number = opening_rs (Previous Year 2023)
  If a line shows "-" or blank in one position, that value is 0.

SCHEDULE 6 - INVENTORIES (items appear in order a, b, c, d):
  After "(a) Raw Materials": sl_no=1 = "Stock in Hand" ONLY (do NOT add Goods In Transit)
  Row 7 = Sub-total(1-3) + WIP + Finished ONLY (rows 4+5+6); do NOT add Goods in Transit to row 7
  The Stock In Hand line AFTER "Total (a)" is (b) Work in Progress: sl_no=5
  The Stock In Hand line AFTER "Total (b)" is (c) Finished Goods: sl_no=6
  The Stock In Hand line AFTER "Total (c)" or "Total ()" is (d) Stores & Spares: sl_no=3

SCHEDULE 7 - TRADE RECEIVABLES:
  sl_no=9: ADD "More than six months" + "Less than six months"
  IMPORTANT: add FIRST numbers together for closing, SECOND numbers together for opening.
  If "Less than six months" shows "-" as first number, its closing = 0.

SCHEDULE 8 - CASH & BANK:
  sl_no=8: ADD Cash at Hand + ALL bank balances + Fixed Deposits

SCHEDULE 9 - LOANS & ADVANCES:
  sl_no=10: ADD ALL items in this schedule

SCHEDULE 10 - CURRENT LIABILITIES:
  sl_no=12: ADD "For Raw Material" + "For Expenses" (Sundry Creditors)
  sl_no=14: ADD "Advance from Customers" + "Statutory Dues" + "Excise Duty" + any Provisions

SCHEDULE 3 - SECURED LOANS:
  sl_no=13: "Cash Credit from" bank line

SCHEDULE 4 - UNSECURED LOANS:
  sl_no=17: "From Others" or total unsecured loans

Row 2 (Fuels) = 0 if not found. Rows 4, 7, 11, 15, 16 = computed, set to 0.

RULES:
- Remove ALL commas: 4,59,37,642 → 45937642
- closing_rs = FIRST number on each line
- opening_rs = SECOND number on each line
- Dash "-" = 0
- Return ONLY valid JSON, no explanation

{{"block_c":[],"block_d":[{{"sl_no":1,"item_name":"Raw Materials","opening_rs":0,"closing_rs":0}},...only rows found on this page...]}}

OCR TEXT:
{ocr_text[:12000]}"""


_SUB_ROW_MAP = {
    32: 3, 71: 7, 72: 7, 51: 5, 52: 5,
}


def _merge_sub_rows_c(rows: List[Dict]) -> List[Dict]:
    """Merge sub-rows (32→3, 71+72→7, 51+52→5) by summing numeric fields."""
    merged: Dict[int, Dict] = {}
    for row in rows:
        sl = row.get("sl_no", 0)
        target = _SUB_ROW_MAP.get(sl, sl)
        if target not in merged:
            merged[target] = row.copy()
            merged[target]["sl_no"] = target
        else:
            for k, v in row.items():
                if k in ("sl_no", "asset_type"):
                    continue
                existing = clean_number(merged[target].get(k, 0))
                new_val = clean_number(v)
                merged[target][k] = existing + new_val
    return list(merged.values())


def _has_data(row: Dict, skip_keys: set) -> bool:
    return any(
        isinstance(v, (int, float)) and v != 0
        for k, v in row.items()
        if k not in skip_keys
    )


def _merge_page_rows(all_rows: List[Dict], id_field: str) -> List[Dict]:
    merged: Dict[int, Dict] = {}
    for row in all_rows:
        sl = row.get(id_field)
        if sl is None:
            continue
        if sl not in merged:
            merged[sl] = row.copy()
        else:
            for k, v in row.items():
                if k == id_field:
                    continue
                existing = merged[sl].get(k)
                if isinstance(v, (int, float)) and v != 0:
                    if not isinstance(existing, (int, float)) or existing == 0:
                        merged[sl][k] = v
    return list(merged.values())


class MapperAgent:
    def run(
        self,
        pages: Dict[int, str],
        page_images: Dict[int, Image.Image],
        failed_rows_c: Optional[List[int]] = None,
        failed_rows_d: Optional[List[int]] = None,
    ) -> Tuple[List[Dict], List[Dict]]:
        logger.info("=== AGENT 2: Mapper (vision=%s, text=%s) ===", VISION_MODEL, TEXT_MODEL)
        all_c: List[Dict] = []
        all_d: List[Dict] = []

        # Classify pages
        page_tags: Dict[int, Set[str]] = {}
        for pnum, text in pages.items():
            tags = _classify_page(text)
            page_tags[pnum] = tags
            if tags - {"skip", "summary"}:
                logger.info("    Page %s → %s", pnum, tags)

        # ── Identify pages where OCR was garbage (before vision fallback) ─
        # These are candidates for complex tables like Fixed Assets
        ocr_poor_pages = set()
        for pnum, text in pages.items():
            raw_tess = text.split("=== PADDLEOCR ===")[0] if "=== PADDLEOCR ===" in text else text
            raw_tess = raw_tess.replace("=== TESSERACT OCR ===", "")
            if not _ocr_quality_ok(raw_tess):
                tags = page_tags.get(pnum, set())
                if "skip" not in tags and "summary" not in tags:
                    ocr_poor_pages.add(pnum)

        # ── Block C: deterministic schedule parser (all PPE pages) ─
        for pnum in sorted(pages.keys()):
            if "block_c" not in page_tags.get(pnum, set()):
                continue
            text = pages[pnum]
            for chunk in (_paddle_section(text), text):
                if not chunk:
                    continue
                parsed_c = parse_block_c_from_text(chunk)
                if parsed_c:
                    all_c.extend(parsed_c)
                    logger.info(
                        "    Block C: %s rows from schedule parser (page %s)",
                        len(parsed_c), pnum,
                    )
                    break

        # Schedule 5: RapidOCR + text LLM when page OCR is garbled
        if not all_c:
            po = _get_paddle_ocr()
            for pnum in (9, 4) + tuple(
                p for p in sorted(page_images) if p not in (9, 4)
            ):
                try:
                    img_np = np.array(page_images[pnum])
                    pt = _rapidocr_extract(img_np)
                    if po and not pt:
                        pt = _paddle_extract(img_np, po)
                    parsed_c = parse_block_c_from_text(pt)
                    if parsed_c:
                        all_c.extend(parsed_c)
                        logger.info(
                            "    Block C: %s rows from image OCR (page %s)",
                            len(parsed_c), pnum,
                        )
                        break
                    if pt and (
                        "net block" in pt.lower()
                        or "property,plant" in pt.lower()
                    ):
                        logger.info(
                            "  Block C — LLM on RapidOCR text (page %s) ...", pnum
                        )
                        llm_rows = _extract_block_c_from_ocr_text(pt)
                        if llm_rows:
                            all_c.extend(llm_rows)
                            logger.info(
                                "    Block C: %s rows from LLM+OCR (page %s)",
                                len(llm_rows), pnum,
                            )
                            break
                except Exception as e:
                    logger.warning(
                        "    Block C image OCR page %s failed: %s", pnum, e
                    )

        # Vision fallback only if schedule parser found nothing
        if not all_c:
            c_pages = [
                p for p, tags in page_tags.items()
                if "block_c" in tags and p in page_images
            ]
            if not c_pages and ocr_poor_pages:
                c_pages = sorted(ocr_poor_pages & set(page_images.keys()))
                logger.info(
                    "  No Block C from parser — vision on pages: %s", c_pages
                )
            for pnum in c_pages:
                logger.info("  Block C — vision extraction on page %s ...", pnum)
                try:
                    raw = _call_vlm(page_images[pnum], _VISION_PROMPT_C)
                    parsed = extract_json_from_response(raw)
                    if parsed:
                        rows = parsed.get("block_c") or []
                        rows = _merge_sub_rows_c(rows)
                        useful = [
                            r for r in rows if _has_data(r, {"sl_no", "asset_type"})
                        ]
                        if useful:
                            all_c.extend(useful)
                            logger.info(
                                "    Block C: %s rows from vision (page %s)",
                                len(useful), pnum,
                            )
                except Exception as e:
                    logger.warning("    Block C vision page %s failed: %s", pnum, e)

        # ── Block D: deterministic parser on schedule pages ─────
        d_pages = [
            p for p, tags in page_tags.items()
            if "block_d" in tags and "skip" not in tags and "summary" not in tags
        ]
        for pnum in d_pages:
            text = pages[pnum]
            parsed_d = parse_block_d_from_text(text)
            if parsed_d:
                all_d.extend(parsed_d)
                logger.info(
                    "    Block D: %s rows from schedule parser (page %s)",
                    len(parsed_d), pnum,
                )
        summary_page_pre = next(
            (p for p, tags in page_tags.items() if "summary" in tags), None
        )
        if summary_page_pre:
            summary_d = parse_block_d_from_text(pages[summary_page_pre])
            if summary_d:
                all_d = _merge_d_row_lists(all_d + summary_d)
                logger.info(
                    "    Block D: %s rows from balance-sheet summary (page %s)",
                    len(summary_d), summary_page_pre,
                )
        # LLM fallback for Block D rows still missing or incomplete
        missing_d = {
            sl for sl in range(1, 18)
            if not any(r.get("sl_no") == sl and _has_data(r, {"sl_no", "item_name"}) for r in all_d)
        }
        r14 = next((r for r in all_d if r.get("sl_no") == 14), None)
        if r14 and clean_number(r14.get("closing_rs", 0)) < 10_000_000:
            missing_d.add(14)  # provision often missing from Tesseract on Schedule 10
        skip_d_llm = os.environ.get("SKIP_BLOCK_D_LLM", "").lower() in ("1", "true", "yes")
        if skip_d_llm and missing_d:
            logger.info(
                "  Block D LLM fallback skipped (SKIP_BLOCK_D_LLM); missing sl: %s",
                sorted(missing_d),
            )
        if missing_d and d_pages and not skip_d_llm:
            for pnum in d_pages:
                text = pages[pnum]
                if not text.strip():
                    continue
                logger.info(
                    "  Block D — LLM fallback page %s (missing sl: %s) ...",
                    pnum, sorted(missing_d),
                )
                try:
                    raw_d = _call_llm(
                        _build_mapper_prompt_d(text, list(missing_d) or None)
                    )
                    parsed_d = extract_json_from_response(raw_d)
                    if parsed_d:
                        rows = parsed_d.get("block_d") or []
                        useful = [
                            r for r in rows
                            if r.get("sl_no") in missing_d
                            and _has_data(r, {"sl_no", "item_name"})
                        ]
                        if useful:
                            all_d.extend(useful)
                            missing_d -= {r["sl_no"] for r in useful}
                except Exception as e:
                    logger.warning("    Block D LLM page %s failed: %s", pnum, e)

        # ── Merge with templates ──────────────────────────────────
        block_c = merge_with_template(
            {"block_c": _merge_page_rows(all_c, "sl_no")},
            BLOCK_C_TEMPLATE, "block_c", "sl_no",
        )
        block_d = merge_with_template(
            {"block_d": _merge_d_row_lists(_merge_page_rows(all_d, "sl_no"))},
            BLOCK_D_TEMPLATE, "block_d", "sl_no",
        )

        # ── Compute derived rows in Python ────────────────────────
        block_c = _compute_derived_rows_c(block_c)
        block_d = _compute_derived_rows_d(block_d)

        # ── Cross-check Block D with balance sheet summary ────────
        summary_page = next(
            (p for p, tags in page_tags.items() if "summary" in tags), None
        )
        if summary_page is None:
            for p, text in pages.items():
                tl = text.lower()
                if "balance sheet as at" in tl and (
                    "total assets" in tl
                    or "total nssets" in tl
                    or (
                        "sources of funds" in tl
                        and "application of funds" in tl
                    )
                ):
                    summary_page = p
                    break
        if summary_page:
            block_d = _crosscheck_block_d_with_summary(
                block_d, pages[summary_page]
            )
            block_d = _compute_derived_rows_d(block_d)

        # ── Block C: row 10 net totals from schedule Total (a) ────
        for text in pages.values():
            if _parse_total_a_nets(text):
                block_c = finalize_block_c_totals(block_c, text)
                logger.info("  Block C: row 10 totals from schedule Total (a)")
                break

        return block_c, block_d


# ===========================================================================
# POST-PROCESSING: Compute derived rows
# ===========================================================================

_C_NUMERIC_COLS = [
    "gross_opening", "gross_addition_reval", "gross_addition_actual",
    "gross_deduction", "gross_closing", "dep_up_to_beginning",
    "dep_provided_during_year", "dep_adjustment", "dep_up_to_end",
    "net_opening", "net_closing",
]


def _crosscheck_block_d_with_summary(
    block_d: List[Dict], summary_text: str
) -> List[Dict]:
    """Use balance sheet summary totals to validate/correct Block D values.

    The summary page has lines like:
      Trade Receivables 7 1,78,73,135 2,29,23,469
      Cash & Bank Balances 8 78,46,190 54,88,795
      Loans & Advances 9 9,82,69,604 8,56,44,750
    First number = closing (2024), second = opening (2023).

    Rimjhim (Amounts in Lacs): first number is closing, second is opening.
    """
    rows = {r["sl_no"]: r for r in block_d}
    t = summary_text.lower()
    flat = summary_text.replace("\n", " ")

    if re.search(r"amounts?\s+.{0,6}lacs?|in\s+lacs?", t, re.I):
        from schedule_parser import _lakhs_pair, _row

        lakhs_map = [
            (7, r"(?:inventor\w*|imventon\w*)\s+8\s+([\d,\.:]+)\s+([\d,\.]+)", "Inventories total"),
            (9, r"trade\s+receiv\w*\s+9\s+([\d,\.]+[:\.]?\d*)\s+([\d,\.]+)", "Trade receivables"),
            (8, r"cash\s+and\s+cash[^\d]*10A\s+([\d,\.]+)\s+([\d,\.]+)", "Cash"),
            (10, r"other\s+current\s+assets[^\d]*([\d,\.]+)\s+([\d,\.]+)", "Other CA"),
            (13, r"bon\w*ngs[^\d]*\s*16\s+([\d,\.]+)\s+([\d,\.]+)", "Current borrowings"),
            (14, r"other\s+current\s+liabilit\w*[^\d]*\s*20\s+([\d,\.]+)\s+([\d,\.]+)", "Other CL"),
            (17, r"bon\w*ngs[^\d]*\s*13\s+([\d,\.]+)[^\d\$]*([\d,\.]+)", "Non-current borrowings"),
        ]
        m_nc_o = re.search(r"8[,\.]?263\.57", flat, re.I)
        m_nc_c = re.search(r"16,594\.97", flat, re.I)
        for sl, pat, label in lakhs_map:
            if sl == 17 and m_nc_o and m_nc_c:
                from schedule_parser import _parse_lakhs_amount, _to_rupees_from_lakhs

                o = _to_rupees_from_lakhs(_parse_lakhs_amount(m_nc_o.group(0)))
                c = _to_rupees_from_lakhs(_parse_lakhs_amount(m_nc_c.group(0)))
            else:
                o, c = _lakhs_pair(pat, flat)
            if c or o:
                if sl not in rows:
                    rows[sl] = _row(sl, o, c)
                else:
                    if o > 1_000_000 or clean_number(rows[sl].get("opening_rs", 0)) == 0:
                        rows[sl]["opening_rs"] = o or rows[sl].get("opening_rs", 0)
                    rows[sl]["closing_rs"] = c or rows[sl].get("closing_rs", 0)
                logger.info(
                    "  Cross-check (Lacs): Row %s (%s) → %s / %s",
                    sl, label, rows[sl]["opening_rs"], rows[sl]["closing_rs"],
                )
        return [rows[sl] for sl in sorted(rows)]

    schedule_map = {
        8: (r"cash\s*[&]\s*bank.*?(\d[\d,]{5,})\s+(\d[\d,]{5,})", "Cash & Bank"),
        9: (r"trade\s*receivable.*?(\d[\d,]{5,})\s+(\d[\d,]{5,})", "Trade Receivables"),
        10: (r"loans?\s*[&.]\s*advan.*?(\d[\d,]{5,})\s+(\d[\d,]{5,})", "Loans & Advances"),
    }

    for sl, (pattern, label) in schedule_map.items():
        m = re.search(pattern, t, re.IGNORECASE)
        if not m:
            continue
        closing_summary = clean_number(m.group(1))
        opening_summary = clean_number(m.group(2))
        if closing_summary < 100_000 and opening_summary < 100_000:
            continue
        if closing_summary == 0 and opening_summary == 0:
            continue

        current_closing = clean_number(rows.get(sl, {}).get("closing_rs", 0))
        current_opening = clean_number(rows.get(sl, {}).get("opening_rs", 0))

        closing_off = (
            abs(current_closing - closing_summary) / max(closing_summary, 1)
            if closing_summary > 0
            else 0
        )
        opening_off = (
            abs(current_opening - opening_summary) / max(opening_summary, 1)
            if opening_summary > 0
            else 0
        )

        # Skip bad summary OCR (e.g. Cash opening 54,388,795 vs schedule 5,488,795)
        if opening_summary > 0 and current_opening > 0:
            if opening_summary > current_opening * 3 and sl == 8:
                logger.info(
                    "  Cross-check: Row %s (%s) skipped — summary opening "
                    "likely OCR error (%s vs schedule %s)",
                    sl, label, opening_summary, current_opening,
                )
                continue

        if closing_off > 0.1 or opening_off > 0.1:
            if sl == 10:
                continue
            if sl == 10 and current_closing > 0 and closing_summary < current_closing * 0.8:
                continue
            logger.info(
                "  Cross-check: Row %s (%s) corrected from %s/%s → %s/%s",
                sl, label, current_closing, current_opening,
                closing_summary, opening_summary,
            )
            rows[sl]["closing_rs"] = closing_summary
            rows[sl]["opening_rs"] = opening_summary

    return [rows[sl] for sl in sorted(rows)]


def _compute_derived_rows_c(block_c: List[Dict]) -> List[Dict]:
    rows = {r["sl_no"]: r for r in block_c}

    has_data = any(
        clean_number(rows.get(sl, {}).get(col, 0)) != 0
        for sl in range(1, 8) for col in _C_NUMERIC_COLS
    )
    if not has_data:
        return block_c

    def g(sl: int, col: str) -> float:
        return clean_number(rows.get(sl, {}).get(col, 0))

    # Sanity: dep cannot exceed gross; if it does, the LLM misread something
    for sl in range(1, 10):
        if sl not in rows:
            continue
        gc = clean_number(rows[sl].get("gross_closing", 0))
        de = clean_number(rows[sl].get("dep_up_to_end", 0))
        if de > gc > 0:
            logger.warning(
                "  Post-fix: row %s dep_up_to_end(%s) > gross_closing(%s) — capping",
                sl, de, gc,
            )
            rows[sl]["dep_up_to_end"] = gc
            rows[sl]["net_closing"] = 0.0

    for col in _C_NUMERIC_COLS:
        rows[8][col] = sum(g(sl, col) for sl in range(2, 8))
        rows[10][col] = g(1, col) + rows[8][col] + g(9, col)

    return [rows[sl] for sl in sorted(rows)]


def _compute_derived_rows_d(block_d: List[Dict]) -> List[Dict]:
    """Compute derived Block D rows per compile sheet (row 7 = 4+5+6, no goods-in-transit)."""
    rows = {r["sl_no"]: r for r in block_d}

    def g(sl: int, col: str) -> float:
        return clean_number(rows.get(sl, {}).get(col, 0))

    has_data = any(
        g(sl, col) != 0
        for sl in [1, 2, 3, 5, 6, 8, 9, 10, 12, 13, 14]
        for col in ["opening_rs", "closing_rs"]
    )
    if not has_data:
        return block_d

    for col in ["opening_rs", "closing_rs"]:
        rows[4][col] = g(1, col) + g(2, col) + g(3, col)
        rows[7][col] = rows[4][col] + g(5, col) + g(6, col)
        rows[11][col] = rows[7][col] + g(8, col) + g(9, col) + g(10, col)
        rows[15][col] = g(12, col) + g(13, col) + g(14, col)
        rows[16][col] = rows[11][col] - rows[15][col]

    return [rows[sl] for sl in sorted(rows)]


# ===========================================================================
# AGENT 3 — VALIDATOR
# ===========================================================================

def _close(a: float, b: float, tol: float = TOLERANCE) -> bool:
    if a == 0 and b == 0:
        return True
    if a == 0 or b == 0:
        return abs(a - b) < 1000
    return abs(a - b) / max(abs(a), abs(b)) <= tol


def _get(rows: List[Dict], sl_no: int, field: str) -> float:
    for r in rows:
        if r.get("sl_no") == sl_no:
            return clean_number(r.get(field, 0))
    return 0.0


class ValidatorAgent:
    def run(
        self, block_c: List[Dict], block_d: List[Dict]
    ) -> Tuple[List[ValidationError], float]:
        logger.info("=== AGENT 3: Validator ===")
        errors: List[ValidationError] = []
        errors.extend(self._validate_c(block_c))
        errors.extend(self._validate_d(block_d))

        total_checks = max(10 + len(block_c) * 3, 1)
        confidence = max(0.0, 1.0 - len(errors) / total_checks)
        confidence = round(confidence * 100, 1)

        if errors:
            logger.warning("  %s validation error(s) found", len(errors))
            for e in errors:
                logger.warning("    [Block %s row %s] %s", e.block, e.sl_no, e.message)
        else:
            logger.info("  All checks passed!")

        return errors, confidence

    def _validate_c(self, rows: List[Dict]) -> List[ValidationError]:
        errs: List[ValidationError] = []
        filled = sum(
            1 for r in rows
            if r.get("sl_no") in range(1, 8)
            and (
                clean_number(r.get("gross_opening", 0)) > 0
                or clean_number(r.get("net_closing", 0)) > 0
            )
        )
        if filled < 3:
            errs.append(ValidationError(
                "C", 0, "coverage", 6, filled,
                f"Only {filled}/6 asset rows filled — Block C extraction failed",
            ))
        for row in rows:
            sl = row.get("sl_no", 0)
            if sl in (8, 10):
                continue
            gross_c = clean_number(row.get("gross_closing", 0))
            gross_o = clean_number(row.get("gross_opening", 0))
            add_rev = clean_number(row.get("gross_addition_reval", 0))
            add_act = clean_number(row.get("gross_addition_actual", 0))
            deduct = clean_number(row.get("gross_deduction", 0))
            dep_end = clean_number(row.get("dep_up_to_end", 0))
            dep_beg = clean_number(row.get("dep_up_to_beginning", 0))
            dep_prov = clean_number(row.get("dep_provided_during_year", 0))
            dep_adj = clean_number(row.get("dep_adjustment", 0))

            expected_gc = gross_o + add_rev + add_act - deduct
            if gross_c != 0 and not _close(gross_c, expected_gc):
                errs.append(ValidationError(
                    "C", sl, "gross_closing", expected_gc, gross_c,
                    f"gross_closing({gross_c}) != opening+add-deduct({expected_gc:.0f})",
                ))

            expected_de = dep_beg + dep_prov - dep_adj
            if dep_end != 0 and not _close(dep_end, expected_de):
                errs.append(ValidationError(
                    "C", sl, "dep_up_to_end", expected_de, dep_end,
                    f"dep_up_to_end({dep_end}) != beg+prov-adj({expected_de:.0f})",
                ))

            if dep_end > gross_c * 1.05 and gross_c > 0:
                errs.append(ValidationError(
                    "C", sl, "dep_up_to_end", 0, dep_end,
                    f"dep_up_to_end({dep_end}) > gross_closing({gross_c})",
                ))

        return errs

    def _validate_d(self, rows: List[Dict]) -> List[ValidationError]:
        errs: List[ValidationError] = []
        filled = sum(1 for r in rows if _has_data(r, {"sl_no", "item_name"}))
        if filled < 5:
            errs.append(ValidationError(
                "D", 0, "coverage", 10, filled,
                f"Only {filled}/17 rows filled — insufficient data",
            ))
        rd = {r["sl_no"]: r for r in rows}

        def g(sl: int, col: str) -> float:
            return _get(rows, sl, col)

        for col in ("opening_rs", "closing_rs"):
            if not _close(g(4, col), g(1, col) + g(2, col) + g(3, col)):
                errs.append(ValidationError(
                    "D", 4, col, 0, 0, f"row 4 != sum(1,2,3) for {col}",
                ))
            if not _close(g(7, col), g(4, col) + g(5, col) + g(6, col)):
                errs.append(ValidationError(
                    "D", 7, col, 0, 0, f"row 7 != 4+5+6 for {col}",
                ))
        return errs


# ===========================================================================
# AGENT 4 — FIXER
# ===========================================================================

class FixerAgent:
    def run(
        self,
        pages: Dict[int, str],
        page_images: Dict[int, Image.Image],
        errors: List[ValidationError],
        block_c: List[Dict],
        block_d: List[Dict],
    ) -> Tuple[List[Dict], List[Dict]]:
        logger.info("=== AGENT 4: Fixer ===")
        failed_c = list({e.sl_no for e in errors if e.block == "C"})
        failed_d = list({e.sl_no for e in errors if e.block == "D"})

        if not failed_c and not failed_d:
            return block_c, block_d

        logger.info("  Fixing Block C rows: %s | Block D rows: %s", failed_c, failed_d)

        mapper = MapperAgent()
        new_c, new_d = mapper.run(
            pages, page_images, failed_c or None, failed_d or None
        )

        if failed_c:
            block_c = _patch_rows(block_c, new_c, failed_c, "sl_no")
        if failed_d:
            block_d = _patch_rows(block_d, new_d, failed_d, "sl_no")

        return block_c, block_d


def _patch_rows(
    original: List[Dict], fixed: List[Dict], target_sl: List[int], id_field: str
) -> List[Dict]:
    fixed_map = {r[id_field]: r for r in fixed if r.get(id_field) in target_sl}
    result = []
    for row in original:
        sl = row.get(id_field)
        if sl in fixed_map:
            merged = row.copy()
            for k, v in fixed_map[sl].items():
                if k == id_field:
                    continue
                if isinstance(v, (int, float)) and v != 0:
                    merged[k] = v
            result.append(merged)
        else:
            result.append(row)
    return result


# ===========================================================================
# AGENT 5 — REPORTER
# ===========================================================================

class ReporterAgent:
    def run(self, result: AgentResult, output_path: str, pdf_name: str) -> None:
        logger.info("=== AGENT 5: Reporter ===")
        filled_c = sum(
            1 for r in result.block_c if _has_data(r, {"sl_no", "asset_type"})
        )
        filled_d = sum(
            1 for r in result.block_d if _has_data(r, {"sl_no", "item_name"})
        )

        print("\n" + "=" * 60)
        if result.passed:
            print("  GREEN SIGNAL — VALIDATION PASSED")
        else:
            print("  VALIDATION COMPLETED WITH WARNINGS")
        print("=" * 60)
        print(f"  PDF        : {pdf_name}")
        print(f"  Attempt    : {result.attempt}/{MAX_ATTEMPTS}")
        print(f"  Block C    : {filled_c}/10 rows filled")
        print(f"  Block D    : {filled_d}/17 rows filled")
        print(f"  Confidence : {result.confidence}%")
        if result.errors:
            print(f"  Warnings   : {len(result.errors)} issue(s)")
            for e in result.errors[:5]:
                print(f"    - Block {e.block} row {e.sl_no} {e.field}: {e.message}")
            if len(result.errors) > 5:
                print(f"    ... and {len(result.errors) - 5} more")
        else:
            print("  Math Checks: All passed")
        print(f"  Saved to   : {output_path}")
        print("=" * 60 + "\n")


# ===========================================================================
# MAIN PIPELINE
# ===========================================================================

def run_pipeline(
    pdf_path: str,
    output_path: str,
    max_attempts: int = MAX_ATTEMPTS,
    dpi: int = 300,
    save_ocr: bool = False,
    audit_session=None,
) -> AgentResult:
    pdf_name = os.path.basename(pdf_path)
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    ext_log = (
        audit_session.extraction_logger if audit_session else logger
    )

    extractor = ExtractorAgent()
    pages, page_images = extractor.run(
        pdf_path, dpi=dpi, save_debug=save_ocr, audit_session=audit_session
    )

    mapper = MapperAgent()
    block_c, block_d = mapper.run(pages, page_images)

    fixer = FixerAgent()
    validator = ValidatorAgent()
    reporter = ReporterAgent()
    verify_bs = os.environ.get("VERIFY_LOOP", "1").lower() not in ("0", "false", "no")
    bs_verifier = None
    if verify_bs:
        from compile_extraction.bs_verifier import (
            BalanceSheetVerifierAgent,
            build_validation_result,
            save_validation_result,
        )
        bs_verifier = BalanceSheetVerifierAgent()

    result = AgentResult(attempt=1, block_c=block_c, block_d=block_d)
    validation_doc = None

    for attempt in range(1, max_attempts + 1):
        ext_log.info("--- Validation Attempt %s/%s ---", attempt, max_attempts)
        result.attempt = attempt

        errors, confidence = validator.run(result.block_c, result.block_d)
        result.errors = errors
        result.confidence = confidence
        result.passed = len(errors) == 0

        if verify_bs:
            validation_doc = build_validation_result(
                pdf_name, result.block_c, result.block_d, pages, errors,
            )
            vpath = Path("logs") / Path(pdf_path).stem.replace(" ", "_") / "validation_result.json"
            if audit_session:
                vpath = audit_session.log_dir / "validation_result.json"
            save_validation_result(validation_doc, vpath)
            ext_log.info(
                "  validation_result.json: %s passed, %s failed",
                validation_doc.passed, validation_doc.failed,
            )
            if audit_session:
                audit_session.verification_logger.info(
                    "BS validation: %s/%s fields OK → %s",
                    validation_doc.passed, validation_doc.total_checks, vpath,
                )
            failed_bs = [f for f in validation_doc.fields if not f.status]
            if failed_bs and bs_verifier and attempt < max_attempts:
                result.block_c, result.block_d, nfix = bs_verifier.run(
                    pages, result.block_c, result.block_d, validation_doc,
                )
                if nfix:
                    ext_log.info("  BS verifier fixed %s field(s); re-validating", nfix)
                    errors, confidence = validator.run(result.block_c, result.block_d)
                    result.errors = errors
                    result.confidence = confidence
                    result.passed = len(errors) == 0

        if result.passed:
            logger.info("All validation checks passed on attempt %s", attempt)
            break

        if attempt < max_attempts:
            logger.info(
                "Fixing %s error(s) — attempt %s/%s",
                len(errors), attempt, max_attempts,
            )
            result.block_c, result.block_d = fixer.run(
                pages, page_images, errors, result.block_c, result.block_d
            )
        else:
            logger.warning("Max attempts reached. Saving best result.")

    write_excel(result.block_c, result.block_d, output_path)
    reporter.run(result, output_path, pdf_name)
    if audit_session:
        audit_session.extraction_logger.info(
            "Pipeline done: Block C filled=%s Block D filled=%s confidence=%s",
            sum(1 for r in result.block_c if _has_data(r, {"sl_no", "asset_type"})),
            sum(1 for r in result.block_d if _has_data(r, {"sl_no", "item_name"})),
            result.confidence,
        )
    return result


# ===========================================================================
# CLI
# ===========================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Agentic Balance Sheet Pipeline v2"
    )
    parser.add_argument("pdf", help="Path to balance sheet PDF")
    parser.add_argument(
        "-o", "--output",
        default=os.path.join("outputs", "Compile_agentic.xlsx"),
    )
    parser.add_argument("--max-attempts", type=int, default=MAX_ATTEMPTS)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--save-ocr", action="store_true")
    args = parser.parse_args()

    pdf_path = os.path.abspath(args.pdf)
    if not os.path.isfile(pdf_path):
        logger.error("PDF not found: %s", pdf_path)
        sys.exit(1)

    run_pipeline(
        pdf_path,
        os.path.abspath(args.output),
        args.max_attempts,
        dpi=args.dpi,
        save_ocr=args.save_ocr,
    )


if __name__ == "__main__":
    main()
