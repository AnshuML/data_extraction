"""
Enterprise Balance Sheet Extraction Pipeline
─────────────────────────────────────────────
Usage:
    # Single PDF
    python main.py --pdf "path/to/balance_sheet.pdf"

    # Single PDF with custom output path
    python main.py --pdf "path/to/balance_sheet.pdf" --out "path/to/output.xlsx"

    # Batch: process all PDFs in a folder
    python main.py --batch "path/to/pdf_folder" --out "path/to/output_folder"

    # Override scale (if PDF is in Lakhs → multiply × 100000)
    python main.py --pdf "..." --scale 100000

    # Override Ollama models
    python main.py --pdf "..." --extractor gemma3:4b --verifier llama3.2:3b
"""
from __future__ import annotations

import argparse
import os
import sys
import time

# ── Add project root to path so sub-packages resolve correctly ──────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from exporters import excel_exporter
from supervisor import orchestrator
from utils.logger import get_logger
from utils.ollama_client import is_ollama_alive

logger = get_logger("main")


# ─────────────────────────────────────────────────────────────────────────────
# Argument parsing
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Enterprise Balance Sheet → Excel extraction pipeline"
    )
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--pdf",   type=str,
                       help="Path to a single PDF file")
    group.add_argument("--batch", type=str,
                       help="Folder containing PDF files")

    p.add_argument("--out",       type=str, default=None,
                   help="Output .xlsx path (single) or folder (batch)")
    p.add_argument("--scale",     type=int, default=1,
                   help="Multiply extracted numbers by this factor (e.g. 100000 for Lakhs)")
    p.add_argument("--extractor", type=str, default=None,
                   help=f"Ollama extractor model (default: {config.EXTRACTOR_MODEL})")
    p.add_argument("--verifier",  type=str, default=None,
                   help=f"Ollama verifier model (default: {config.VERIFIER_MODEL})")
    p.add_argument("--retries",   type=int, default=config.MAX_RETRIES,
                   help=f"Max retry attempts (default: {config.MAX_RETRIES})")
    return p.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
# Pre-flight checks
# ─────────────────────────────────────────────────────────────────────────────

def _preflight() -> bool:
    ok = True

    # Tesseract
    if not os.path.exists(config.TESSERACT_CMD):
        logger.warning(
            "Tesseract not found at %s — scanned PDFs will fail OCR.\n"
            "  Install from: https://github.com/UB-Mannheim/tesseract/wiki\n"
            "  Or set env var TESSERACT_CMD to the correct path.",
            config.TESSERACT_CMD,
        )

    # Ollama
    if not is_ollama_alive():
        logger.error(
            "Ollama server not reachable at %s\n"
            "  Start it with: ollama serve\n"
            "  Then pull models:\n"
            "    ollama pull %s\n"
            "    ollama pull %s",
            config.OLLAMA_BASE_URL,
            config.EXTRACTOR_MODEL,
            config.VERIFIER_MODEL,
        )
        ok = False

    return ok


# ─────────────────────────────────────────────────────────────────────────────
# Single PDF processing
# ─────────────────────────────────────────────────────────────────────────────

def process_single(pdf_path: str, out_path: str | None) -> int:
    """Returns 0 on SUCCESS/PARTIAL, 1 on FAILED."""
    if not os.path.exists(pdf_path):
        logger.error("PDF not found: %s", pdf_path)
        return 1

    logger.info("Starting pipeline for: %s", os.path.basename(pdf_path))
    result = orchestrator.run(pdf_path)

    if result.final_status == "FAILED":
        logger.error("Pipeline FAILED for %s", os.path.basename(pdf_path))
        return 1

    excel_path = excel_exporter.export(result, out_path)
    logger.info("Output: %s", excel_path)

    # Print summary
    print("\n" + "─" * 60)
    print(f"  Status  : {result.final_status}")
    print(f"  PDF     : {os.path.basename(pdf_path)}")
    print(f"  Excel   : {excel_path}")
    print(f"  Attempts: {len(result.attempts)}")
    print(f"  Time    : {result.total_elapsed}s")
    print("─" * 60 + "\n")

    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Batch processing
# ─────────────────────────────────────────────────────────────────────────────

def process_batch(folder: str, out_folder: str | None) -> int:
    pdf_files = [
        f for f in os.listdir(folder)
        if f.lower().endswith(".pdf")
    ]
    if not pdf_files:
        logger.error("No PDF files found in: %s", folder)
        return 1

    if out_folder:
        os.makedirs(out_folder, exist_ok=True)

    logger.info("Batch processing %d PDFs from: %s", len(pdf_files), folder)

    summary = {"success": 0, "partial": 0, "failed": 0}

    for i, pdf_name in enumerate(pdf_files, start=1):
        pdf_path = os.path.join(folder, pdf_name)
        out_path = None
        if out_folder:
            base = os.path.splitext(pdf_name)[0]
            out_path = os.path.join(out_folder, f"{base}_compile.xlsx")

        logger.info("[%d/%d] Processing: %s", i, len(pdf_files), pdf_name)

        try:
            result = orchestrator.run(pdf_path)
            excel_exporter.export(result, out_path)
            summary[result.final_status.lower()] = summary.get(result.final_status.lower(), 0) + 1
            logger.info("[%d/%d] %s → %s", i, len(pdf_files), pdf_name, result.final_status)
        except Exception as exc:
            logger.error("[%d/%d] EXCEPTION for %s: %s", i, len(pdf_files), pdf_name, exc)
            summary["failed"] += 1

    print("\n" + "=" * 60)
    print("  BATCH COMPLETE")
    print(f"  Success : {summary.get('success', 0)}")
    print(f"  Partial : {summary.get('partial', 0)}")
    print(f"  Failed  : {summary.get('failed', 0)}")
    print("=" * 60 + "\n")

    return 0 if summary.get("failed", 0) == 0 else 1


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    args = _parse_args()

    # Apply CLI overrides to config
    config.PDF_UNIT_MULTIPLIER = args.scale
    if args.extractor:
        config.EXTRACTOR_MODEL = args.extractor
    if args.verifier:
        config.VERIFIER_MODEL = args.verifier
    config.MAX_RETRIES = args.retries

    # Pre-flight
    if not _preflight():
        sys.exit(1)

    # Run
    if args.pdf:
        exit_code = process_single(args.pdf, args.out)
    else:
        exit_code = process_batch(args.batch, args.out)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
