"""
Supervisor Orchestrator — the agentic loop controller.

Flow per attempt:
  PDF → [pdf_processor] → [table_detector] → [row_mapper]
       → [Agent 1: extractor] → [Agent 2: verifier]
       → if REJECTED → retry (up to MAX_RETRIES)
       → [Agent 3: auditor]
       → if REJECTED → retry
       → FINAL RESULT

The supervisor:
  - Decides whether to retry or accept
  - Escalates DPI on retry (better scanned image quality)
  - Logs a full audit trail for every attempt
"""
from __future__ import annotations

import copy
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import config
import schemas
from agents import agent_1_extractor, agent_2_verifier, agent_3_auditor
from pipeline import pdf_processor, table_detector, row_mapper
from utils.logger import get_logger

logger = get_logger("supervisor")


# ─────────────────────────────────────────────────────────────────────────────
# Attempt result
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AttemptResult:
    attempt_no:      int
    verifier_status: str
    auditor_status:  str
    audit_failures:  List[str]
    verify_summary:  Dict[str, Any]
    elapsed_sec:     float
    block_c:         List[Dict[str, Any]] = field(default_factory=list)
    block_d:         List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class PipelineResult:
    pdf_path:         str
    final_status:     str               # "SUCCESS" | "PARTIAL" | "FAILED"
    block_c:          List[Dict[str, Any]]
    block_d:          List[Dict[str, Any]]
    attempts:         List[AttemptResult]
    total_elapsed:    float


# ─────────────────────────────────────────────────────────────────────────────
# DPI escalation on retry
# ─────────────────────────────────────────────────────────────────────────────

_DPI_LADDER = [config.PDF_DPI, 350, 400]


# ─────────────────────────────────────────────────────────────────────────────
# Core pipeline (one attempt)
# ─────────────────────────────────────────────────────────────────────────────

def _run_single_attempt(
    pdf_path: str,
    attempt_no: int,
    dpi: int,
) -> Dict[str, Any]:
    """
    Run the full extraction pipeline once.
    Returns raw extracted data + raw text for each page.
    """
    logger.info("=" * 60)
    logger.info("ATTEMPT %d/%d | DPI=%d | %s",
                attempt_no, config.MAX_RETRIES,
                dpi, os.path.basename(pdf_path))
    logger.info("=" * 60)

    # ── Step 1: PDF → pages ──────────────────────────────────────────────────
    # Temporarily override DPI for this attempt
    original_dpi   = config.PDF_DPI
    config.PDF_DPI = dpi
    pages = pdf_processor.process_pdf(pdf_path)
    config.PDF_DPI = original_dpi

    raw_text_by_page: Dict[int, str] = {
        p.page_num: p.raw_text for p in pages
    }

    # ── Step 2: Detect tables ────────────────────────────────────────────────
    all_matrices   = [cm for page in pages for cm in page.cell_matrices]
    detected_tables = table_detector.detect_tables(all_matrices)

    if not detected_tables:
        logger.warning("No tables detected in PDF")
        return {
            "block_c": schemas.make_block_c_template(),
            "block_d": schemas.make_block_d_template(),
            "raw_text_by_page": raw_text_by_page,
        }

    # ── Step 3: Row mapping (RapidFuzz) ─────────────────────────────────────
    c_template = schemas.make_block_c_template()
    d_template = schemas.make_block_d_template()

    all_c_mapped: List[Dict] = []
    all_d_mapped: List[Dict] = []

    for tbl in detected_tables:
        mapped = row_mapper.map_table_to_schema(tbl)
        if tbl.block_type == "block_c":
            all_c_mapped.extend(mapped)
        elif tbl.block_type == "block_d":
            all_d_mapped.extend(mapped)

    # Merge into canonical templates
    block_c = row_mapper.merge_into_template(c_template, all_c_mapped, "asset_type")
    block_d = row_mapper.merge_into_template(d_template, all_d_mapped, "item_name")

    return {
        "block_c":          block_c,
        "block_d":          block_d,
        "raw_text_by_page": raw_text_by_page,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main orchestration loop
# ─────────────────────────────────────────────────────────────────────────────

def run(pdf_path: str) -> PipelineResult:
    """
    Full agentic pipeline with retry loop.

    Returns a PipelineResult with final verified data and attempt history.
    """
    pipeline_start  = time.time()
    attempt_results: List[AttemptResult] = []
    best_result: Optional[Dict[str, Any]] = None

    for attempt_no in range(1, config.MAX_RETRIES + 1):
        dpi = _DPI_LADDER[min(attempt_no - 1, len(_DPI_LADDER) - 1)]
        t0  = time.time()

        # ── Extraction ───────────────────────────────────────────────────────
        extracted = _run_single_attempt(pdf_path, attempt_no, dpi)

        # ── Agent 1: Extractor (LLM for ambiguous rows) ──────────────────────
        a1_result = agent_1_extractor.run(
            block_c_rows     = extracted["block_c"],
            block_d_rows     = extracted["block_d"],
            raw_text_by_page = extracted["raw_text_by_page"],
        )

        # ── Agent 2: Verifier ────────────────────────────────────────────────
        a2_result = agent_2_verifier.run(
            block_c_rows     = a1_result["block_c"],
            block_d_rows     = a1_result["block_d"],
            raw_text_by_page = extracted["raw_text_by_page"],
        )

        verifier_status = a2_result["status"]
        verify_summary  = a2_result["summary"]

        # ── Agent 3: Math Auditor ────────────────────────────────────────────
        a3_result = agent_3_auditor.run(
            block_c_rows = a2_result["block_c"],
            block_d_rows = a2_result["block_d"],
        )

        auditor_status = a3_result["status"]
        audit_failures = a3_result["failures"]

        elapsed = round(time.time() - t0, 1)

        attempt_rec = AttemptResult(
            attempt_no      = attempt_no,
            verifier_status = verifier_status,
            auditor_status  = auditor_status,
            audit_failures  = audit_failures,
            verify_summary  = verify_summary,
            elapsed_sec     = elapsed,
            block_c         = copy.deepcopy(a3_result["block_c"]),
            block_d         = copy.deepcopy(a3_result["block_d"]),
        )
        attempt_results.append(attempt_rec)

        logger.info(
            "Attempt %d complete in %.1fs — Verifier=%s  Auditor=%s",
            attempt_no, elapsed, verifier_status, auditor_status,
        )

        # ── Accept if both agents pass ───────────────────────────────────────
        if verifier_status == "APPROVED" and auditor_status == "APPROVED":
            logger.info("PIPELINE SUCCESS on attempt %d", attempt_no)
            best_result = a3_result
            break

        # ── Keep best partial result ─────────────────────────────────────────
        if best_result is None:
            best_result = a3_result
        else:
            prev_rate = attempt_results[-2].verify_summary.get("rate", 0)
            curr_rate = verify_summary.get("rate", 0)
            if curr_rate > prev_rate:
                best_result = a3_result

        if attempt_no < config.MAX_RETRIES:
            wait = 2 ** attempt_no
            logger.info("Retrying in %ds (attempt %d/%d)…",
                        wait, attempt_no + 1, config.MAX_RETRIES)
            time.sleep(wait)

    # ── Final status ──────────────────────────────────────────────────────────
    last = attempt_results[-1]
    if last.verifier_status == "APPROVED" and last.auditor_status == "APPROVED":
        final_status = "SUCCESS"
    elif best_result and any(
        r.get("gross_closing", 0) or r.get("net_closing", 0)
        for r in best_result.get("block_c", [])
    ):
        final_status = "PARTIAL"
    else:
        final_status = "FAILED"

    total_elapsed = round(time.time() - pipeline_start, 1)
    logger.info(
        "Pipeline finished — status=%s | total time=%.1fs | attempts=%d",
        final_status, total_elapsed, len(attempt_results),
    )

    return PipelineResult(
        pdf_path      = pdf_path,
        final_status  = final_status,
        block_c       = best_result.get("block_c", schemas.make_block_c_template()),
        block_d       = best_result.get("block_d", schemas.make_block_d_template()),
        attempts      = attempt_results,
        total_elapsed = total_elapsed,
    )
