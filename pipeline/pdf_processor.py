"""
PDF Processor — Layer 1 of the pipeline.

Strategy (in priority order):
  Text PDFs:
    1. pdfplumber  — structured table extraction (exact cell bboxes)
    2. OpenCV + Tesseract — fallback for borderless text tables

  Scanned / Image PDFs:
    If USE_SURYA=True (recommended):
      3a. Surya Table Recognition — AI-based row/col/cell detection
          + Surya OCR — per-cell text recognition (90+ languages)
    Else:
      3b. OpenCV morphological grid detection + Tesseract per-cell OCR

Returns a list of PageResult objects, one per page.
"""
from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2
import fitz          # PyMuPDF
import numpy as np
import pdfplumber
import pytesseract
from PIL import Image

import config
from utils.logger import get_logger

logger = get_logger("pdf_processor")

pytesseract.pytesseract.tesseract_cmd = config.TESSERACT_CMD


# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CellMatrix:
    """
    Represents a detected table as a 2-D grid of strings.
    rows[i][j] = cell text at row i, column j.
    """
    rows:        List[List[str]]
    page_num:    int
    source:      str   # "pdfplumber" | "surya" | "opencv_tesseract"
    bbox:        Optional[Tuple[float, float, float, float]] = None


@dataclass
class PageResult:
    page_num:     int
    raw_text:     str
    cell_matrices: List[CellMatrix] = field(default_factory=list)
    is_scanned:   bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Page-level helpers
# ─────────────────────────────────────────────────────────────────────────────

def _page_to_image(page: fitz.Page, dpi: int = config.PDF_DPI) -> np.ndarray:
    pix = page.get_pixmap(dpi=dpi)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def _bgr_to_pil(img_bgr: np.ndarray) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))


def _is_scanned_page(page: fitz.Page) -> bool:
    """Heuristic: fewer than 20 text chars → treat as scanned."""
    return len(page.get_text("text").strip()) < 20


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 1: pdfplumber (text PDFs)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_tables_pdfplumber(
    plumber_page: pdfplumber.page.Page,
    page_num: int,
) -> List[CellMatrix]:
    matrices = []

    tables = plumber_page.extract_tables(config.PDFPLUMBER_TABLE_SETTINGS)
    if not tables:
        tables = plumber_page.extract_tables(config.PDFPLUMBER_TEXT_TABLE_SETTINGS)
        source = "pdfplumber_text"
    else:
        source = "pdfplumber"

    for table in tables:
        if not table or len(table) < 2:
            continue
        cleaned = []
        for row in table:
            cleaned.append([
                (cell.strip().replace("\n", " ") if cell else "")
                for cell in row
            ])
        matrices.append(CellMatrix(rows=cleaned, page_num=page_num, source=source))
        logger.debug("pdfplumber: page %d → table %dx%d", page_num,
                     len(cleaned), len(cleaned[0]) if cleaned else 0)
    return matrices


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 2: OpenCV + Tesseract (scanned PDFs — no GPU)
# ─────────────────────────────────────────────────────────────────────────────

def _detect_grid_opencv(img_bgr: np.ndarray) -> Tuple[List[int], List[int]]:
    gray   = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    thresh = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, 15, 2,
    )
    k = config.OPENCV_KERNEL_SIZE

    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, 1))
    h_lines  = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, h_kernel, iterations=2)

    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, k))
    v_lines  = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, v_kernel, iterations=2)

    def line_positions(mask: np.ndarray, axis: int) -> List[int]:
        projection = np.sum(mask, axis=axis)
        positions: List[int] = []
        in_line = False
        start   = 0
        for i, val in enumerate(projection):
            if val > mask.shape[axis] * 0.3 and not in_line:
                in_line, start = True, i
            elif val <= mask.shape[axis] * 0.3 and in_line:
                positions.append((start + i) // 2)
                in_line = False
        return sorted(positions)

    row_ys = line_positions(h_lines, axis=1)
    col_xs = line_positions(v_lines, axis=0)
    return row_ys, col_xs


def _ocr_cell(img_bgr: np.ndarray, y1: int, y2: int, x1: int, x2: int) -> str:
    pad = 4
    y1, y2 = max(0, y1 - pad), min(img_bgr.shape[0], y2 + pad)
    x1, x2 = max(0, x1 - pad), min(img_bgr.shape[1], x2 + pad)
    cell_img = img_bgr[y1:y2, x1:x2]
    if cell_img.size == 0:
        return ""
    h, w = cell_img.shape[:2]
    if h < 30 or w < 30:
        cell_img = cv2.resize(cell_img, (max(w * 3, 90), max(h * 3, 90)),
                              interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(cell_img, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return pytesseract.image_to_string(binary, config=config.TESSERACT_CONFIG).strip()


def _extract_tables_opencv(img_bgr: np.ndarray, page_num: int) -> List[CellMatrix]:
    row_ys, col_xs = _detect_grid_opencv(img_bgr)

    if len(row_ys) < 2 or len(col_xs) < 2:
        logger.debug("OpenCV: page %d — not enough grid lines found", page_num)
        return []

    logger.info("OpenCV: page %d — detected %d rows × %d cols",
                page_num, len(row_ys) - 1, len(col_xs) - 1)

    matrix: List[List[str]] = []
    for r in range(len(row_ys) - 1):
        row_cells = []
        for c in range(len(col_xs) - 1):
            text = _ocr_cell(img_bgr, row_ys[r], row_ys[r + 1],
                             col_xs[c], col_xs[c + 1])
            row_cells.append(text)
        matrix.append(row_cells)

    if matrix:
        return [CellMatrix(rows=matrix, page_num=page_num, source="opencv_tesseract")]
    return []


def _ocr_full_page_tesseract(img_bgr: np.ndarray) -> str:
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return pytesseract.image_to_string(binary, config="--oem 3 --psm 6").strip()


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 3: Surya OCR (scanned PDFs — better accuracy, GPU optional)
# ─────────────────────────────────────────────────────────────────────────────
#
# Surya uses two models:
#   TableRecPredictor  → detects table structure: cell bboxes + row_id / col_id
#   RecognitionPredictor → OCR on full page, gives text_lines with bboxes
#
# We map OCR text lines into table cells by bbox overlap (intersection area).
# Predictors are cached as module-level singletons — loaded once, reused per page.

_surya_table_pred  = None
_surya_rec_pred    = None
_surya_det_pred    = None
_surya_import_ok   = None   # None = not tried yet; True/False after first attempt


def _load_surya() -> bool:
    """Load all Surya predictors once. Returns True if successful."""
    global _surya_table_pred, _surya_rec_pred, _surya_det_pred, _surya_import_ok
    if _surya_import_ok is not None:
        return _surya_import_ok
    try:
        from surya.table_rec import TableRecPredictor
        from surya.recognition import RecognitionPredictor
        from surya.detection import DetectionPredictor
        from surya.foundation import FoundationPredictor

        logger.info("Loading Surya models — first time may take 30-60s "
                    "(~500MB download on first run)…")
        foundation         = FoundationPredictor()
        _surya_table_pred  = TableRecPredictor()
        _surya_rec_pred    = RecognitionPredictor(foundation)
        _surya_det_pred    = DetectionPredictor()
        _surya_import_ok   = True
        logger.info("Surya models loaded successfully")
    except ImportError:
        logger.warning(
            "surya-ocr not installed — falling back to OpenCV+Tesseract.\n"
            "  To install: pip install surya-ocr"
        )
        _surya_import_ok = False
    except Exception as exc:
        logger.error("Surya load failed: %s — falling back to OpenCV+Tesseract", exc)
        _surya_import_ok = False
    return _surya_import_ok


def _bbox_overlap_area(a: List[float], b: List[float]) -> float:
    """Intersection area of two [x1,y1,x2,y2] bboxes."""
    ix1 = max(a[0], b[0])
    iy1 = max(a[1], b[1])
    ix2 = min(a[2], b[2])
    iy2 = min(a[3], b[3])
    return max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)


def _extract_tables_surya(img_pil: Image.Image, page_num: int) -> List[CellMatrix]:
    """
    Surya-based table extraction.

    Steps:
      1. TableRecPredictor  → table structure (row_id, col_id, bbox per cell)
      2. RecognitionPredictor → full-page OCR (text + bbox per line)
      3. Map each OCR line into the cell whose bbox it overlaps most
      4. Build CellMatrix grid
    """
    if not _load_surya():
        return []

    logger.info("Surya: processing page %d …", page_num)

    # ── Step 1: Table structure ───────────────────────────────────────────────
    try:
        table_results = _surya_table_pred([img_pil])
    except Exception as exc:
        logger.error("Surya table recognition failed on page %d: %s", page_num, exc)
        return []

    # table_results is a list (one per image).
    # Depending on Surya version, each element is either:
    #   a) a single TableResult  (older API)
    #   b) a list of TableResult (newer API — one per detected table)
    raw = table_results[0] if table_results else None
    if raw is None:
        logger.debug("Surya: no result for page %d", page_num)
        return []
    # Normalise to always be a list
    if hasattr(raw, "cells"):
        page_tables = [raw]          # single TableResult
    elif isinstance(raw, (list, tuple)):
        page_tables = list(raw)      # list of TableResults
    else:
        page_tables = []
    if not page_tables:
        logger.debug("Surya: no tables found on page %d", page_num)
        return []

    # ── Step 2: Full-page OCR ─────────────────────────────────────────────────
    try:
        ocr_results = _surya_rec_pred([img_pil], det_predictor=_surya_det_pred)
        text_lines  = ocr_results[0].text_lines if ocr_results else []
    except Exception as exc:
        logger.warning("Surya OCR failed on page %d: %s — cells may be empty", page_num, exc)
        text_lines = []

    # ── Step 3 & 4: Map text → cells → CellMatrix ────────────────────────────
    matrices: List[CellMatrix] = []

    for tbl_idx, table in enumerate(page_tables):
        cells = getattr(table, "cells", None)
        if not cells:
            continue

        num_rows = max(c.row_id for c in cells) + 1
        num_cols = max(c.col_id for c in cells) + 1

        # Build grid
        grid: List[List[str]] = [[""] * num_cols for _ in range(num_rows)]

        for cell in cells:
            # Surya cell.bbox → list [x1, y1, x2, y2]
            cx1, cy1, cx2, cy2 = cell.bbox

            # Gather text lines that fall inside this cell
            best_texts: List[Tuple[float, str]] = []
            for line in text_lines:
                overlap = _bbox_overlap_area(
                    [cx1, cy1, cx2, cy2],
                    list(line.bbox),
                )
                if overlap > 0:
                    best_texts.append((overlap, line.text.strip()))

            # Sort by overlap descending, concatenate
            best_texts.sort(key=lambda x: x[0], reverse=True)
            cell_text = " ".join(t for _, t in best_texts).strip()
            grid[cell.row_id][cell.col_id] = cell_text

        logger.info(
            "Surya: page %d table[%d] → %d rows × %d cols",
            page_num, tbl_idx, num_rows, num_cols,
        )
        matrices.append(CellMatrix(
            rows=grid, page_num=page_num, source="surya",
            bbox=getattr(table, "bbox", None),
        ))

    return matrices


def _ocr_full_page_surya(img_pil: Image.Image) -> str:
    """
    Full-page raw text via Surya OCR.
    Used as raw_text for verifier when Tesseract is not available.
    """
    if not _load_surya():
        return ""
    try:
        ocr_results = _surya_rec_pred([img_pil], det_predictor=_surya_det_pred)
        lines = ocr_results[0].text_lines if ocr_results else []
        return "\n".join(line.text for line in lines).strip()
    except Exception as exc:
        logger.warning("Surya full-page OCR failed: %s", exc)
        return ""


def _ocr_full_page(img_bgr: np.ndarray, img_pil: Optional[Image.Image] = None) -> str:
    """
    Get raw page text for verifier cross-checking.
    Tries Tesseract first; falls back to Surya if Tesseract unavailable.
    """
    tesseract_ok = os.path.exists(config.TESSERACT_CMD)
    if tesseract_ok:
        try:
            return _ocr_full_page_tesseract(img_bgr)
        except Exception as exc:
            logger.warning("Tesseract full-page OCR failed: %s", exc)

    if config.USE_SURYA and img_pil is not None:
        logger.debug("Falling back to Surya for full-page raw text")
        return _ocr_full_page_surya(img_pil)

    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def process_pdf(pdf_path: str) -> List[PageResult]:
    """
    Main entry point. Processes every page of the PDF and returns
    a list of PageResult objects with cell matrices + raw text.

    Scanned page strategy:
      USE_SURYA=True  → Surya (AI table rec + OCR) with OpenCV fallback
      USE_SURYA=False → OpenCV morphological grid + Tesseract per-cell OCR
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    logger.info("Opening PDF: %s  [USE_SURYA=%s]",
                os.path.basename(pdf_path), config.USE_SURYA)
    results: List[PageResult] = []

    fitz_doc    = fitz.open(pdf_path)
    plumber_doc = pdfplumber.open(pdf_path)

    try:
        for page_idx in range(len(fitz_doc)):
            page_num   = page_idx + 1
            fitz_page  = fitz_doc[page_idx]
            plumb_page = plumber_doc.pages[page_idx]
            scanned    = _is_scanned_page(fitz_page)

            logger.info("Processing page %d/%d  (scanned=%s)",
                        page_num, len(fitz_doc), scanned)

            pr = PageResult(page_num=page_num, raw_text="", is_scanned=scanned)

            if not scanned:
                # ── Text-based PDF ────────────────────────────────────────────
                pr.raw_text      = fitz_page.get_text("text")
                pr.cell_matrices = _extract_tables_pdfplumber(plumb_page, page_num)

                if not pr.cell_matrices:
                    logger.debug("pdfplumber found no table on page %d — trying OpenCV",
                                 page_num)
                    img = _page_to_image(fitz_page)
                    pr.cell_matrices = _extract_tables_opencv(img, page_num)

            else:
                # ── Scanned / Image PDF ───────────────────────────────────────
                img     = _page_to_image(fitz_page)
                img_pil = _bgr_to_pil(img)

                if config.USE_SURYA:
                    # Primary: Surya AI table recognition
                    pr.cell_matrices = _extract_tables_surya(img_pil, page_num)

                    if not pr.cell_matrices:
                        # Fallback: OpenCV when Surya finds no tables
                        logger.warning(
                            "Page %d: Surya found no tables — trying OpenCV fallback",
                            page_num)
                        pr.cell_matrices = _extract_tables_opencv(img, page_num)
                else:
                    # Primary: OpenCV + Tesseract
                    pr.cell_matrices = _extract_tables_opencv(img, page_num)

                # Raw text for verifier (Tesseract preferred, Surya as fallback)
                pr.raw_text = _ocr_full_page(img, img_pil)

                if not pr.cell_matrices:
                    logger.warning("Page %d: no table detected — raw OCR text only",
                                   page_num)

            results.append(pr)
    finally:
        fitz_doc.close()
        plumber_doc.close()

    logger.info("PDF processing complete — %d pages", len(results))
    return results
