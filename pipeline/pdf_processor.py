"""
PDF Processor — Layer 1 of the pipeline.

Strategy (in priority order):
  1. pdfplumber  — structured table extraction for text-based PDFs
  2. PyMuPDF     — raw text fallback for text-based PDFs
  3. OpenCV + Tesseract — cell-level OCR for scanned/image PDFs

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
    source:      str   # "pdfplumber" | "opencv_tesseract" | "text_fallback"
    bbox:        Optional[Tuple[float, float, float, float]] = None


@dataclass
class PageResult:
    page_num:    int
    raw_text:    str
    cell_matrices: List[CellMatrix] = field(default_factory=list)
    is_scanned:  bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Page-level helpers
# ─────────────────────────────────────────────────────────────────────────────

def _page_to_image(page: fitz.Page, dpi: int = config.PDF_DPI) -> np.ndarray:
    pix  = page.get_pixmap(dpi=dpi)
    img  = Image.open(io.BytesIO(pix.tobytes("png")))
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def _is_scanned_page(page: fitz.Page) -> bool:
    """Heuristic: if fewer than 20 text chars, treat as scanned."""
    return len(page.get_text("text").strip()) < 20


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 1: pdfplumber table extraction
# ─────────────────────────────────────────────────────────────────────────────

def _extract_tables_pdfplumber(
    plumber_page: pdfplumber.page.Page,
    page_num: int,
) -> List[CellMatrix]:
    matrices = []

    # Try strict line-based strategy first
    tables = plumber_page.extract_tables(config.PDFPLUMBER_TABLE_SETTINGS)

    # Fallback to text-based strategy if no tables found
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
# Strategy 2: OpenCV grid detection → per-cell Tesseract OCR
# ─────────────────────────────────────────────────────────────────────────────

def _detect_grid_opencv(img_bgr: np.ndarray) -> Tuple[List[int], List[int]]:
    """
    Detect horizontal and vertical grid lines using morphological operations.
    Returns sorted lists of y-coords (rows) and x-coords (cols).
    """
    gray  = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    thresh = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, 15, 2
    )

    k = config.OPENCV_KERNEL_SIZE

    # Horizontal lines
    h_kernel  = cv2.getStructuringElement(cv2.MORPH_RECT, (k, 1))
    h_lines   = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, h_kernel, iterations=2)

    # Vertical lines
    v_kernel  = cv2.getStructuringElement(cv2.MORPH_RECT, (1, k))
    v_lines   = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, v_kernel, iterations=2)

    def line_positions(mask: np.ndarray, axis: int) -> List[int]:
        projection = np.sum(mask, axis=axis)
        positions  = []
        in_line    = False
        start      = 0
        for i, val in enumerate(projection):
            if val > mask.shape[axis] * 0.3 and not in_line:
                in_line, start = True, i
            elif val <= mask.shape[axis] * 0.3 and in_line:
                positions.append((start + i) // 2)
                in_line = False
        return sorted(positions)

    row_ys = line_positions(h_lines, axis=1)   # sum over cols
    col_xs = line_positions(v_lines, axis=0)   # sum over rows
    return row_ys, col_xs


def _ocr_cell(img_bgr: np.ndarray, y1: int, y2: int, x1: int, x2: int) -> str:
    pad = 4
    y1, y2 = max(0, y1 - pad), min(img_bgr.shape[0], y2 + pad)
    x1, x2 = max(0, x1 - pad), min(img_bgr.shape[1], x2 + pad)
    cell_img  = img_bgr[y1:y2, x1:x2]
    if cell_img.size == 0:
        return ""
    # Upscale small cells for better OCR accuracy
    h, w = cell_img.shape[:2]
    if h < 30 or w < 30:
        cell_img = cv2.resize(cell_img, (max(w * 3, 90), max(h * 3, 90)),
                              interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(cell_img, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    text = pytesseract.image_to_string(
        binary, config=config.TESSERACT_CONFIG
    ).strip()
    return text


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
            text = _ocr_cell(img_bgr, row_ys[r], row_ys[r + 1], col_xs[c], col_xs[c + 1])
            row_cells.append(text)
        matrix.append(row_cells)

    if matrix:
        return [CellMatrix(rows=matrix, page_num=page_num, source="opencv_tesseract")]
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 3: Full-page Tesseract (last resort — no grid structure)
# ─────────────────────────────────────────────────────────────────────────────

def _ocr_full_page(img_bgr: np.ndarray) -> str:
    gray   = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    _, bin_  = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return pytesseract.image_to_string(bin_, config="--oem 3 --psm 6").strip()


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def process_pdf(pdf_path: str) -> List[PageResult]:
    """
    Main entry point. Processes every page of the PDF and returns
    a list of PageResult objects with cell matrices + raw text.
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    logger.info("Opening PDF: %s", os.path.basename(pdf_path))
    results: List[PageResult] = []

    fitz_doc     = fitz.open(pdf_path)
    plumber_doc  = pdfplumber.open(pdf_path)

    try:
        for page_idx in range(len(fitz_doc)):
            page_num    = page_idx + 1
            fitz_page   = fitz_doc[page_idx]
            plumb_page  = plumber_doc.pages[page_idx]
            scanned     = _is_scanned_page(fitz_page)

            logger.info("Processing page %d/%d (scanned=%s)",
                        page_num, len(fitz_doc), scanned)

            pr = PageResult(page_num=page_num, raw_text="", is_scanned=scanned)

            if not scanned:
                # Text PDF — pdfplumber for tables, fitz for raw text
                pr.raw_text      = fitz_page.get_text("text")
                pr.cell_matrices = _extract_tables_pdfplumber(plumb_page, page_num)

                # If pdfplumber found nothing, try OpenCV anyway (borderless tables)
                if not pr.cell_matrices:
                    logger.debug("pdfplumber found no table on page %d — trying OpenCV", page_num)
                    img = _page_to_image(fitz_page)
                    pr.cell_matrices = _extract_tables_opencv(img, page_num)

            else:
                # Scanned page — render to image
                img = _page_to_image(fitz_page)

                # Try OpenCV grid first
                pr.cell_matrices = _extract_tables_opencv(img, page_num)

                # Always also get full-page OCR text (used by verifier)
                pr.raw_text = _ocr_full_page(img)

                if not pr.cell_matrices:
                    logger.warning("Page %d: no grid detected — raw OCR only", page_num)

            results.append(pr)
    finally:
        fitz_doc.close()
        plumber_doc.close()

    logger.info("PDF processing complete — %d pages", len(results))
    return results
