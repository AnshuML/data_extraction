"""Structured audit logging for compile sheet extraction pipeline."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class PageOcrRecord:
    page: int
    tesseract_chars: int
    paddle_chars: int
    combined_chars: int
    quality_ok: bool
    vision_used: bool
    avg_confidence: float = 0.0


@dataclass
class ValidationRecord:
    block: str
    sl_no: int
    field: str
    message: str
    expected: float = 0.0
    got: float = 0.0


@dataclass
class FieldAccuracy:
    block: str
    sl_no: int
    field: str
    expected: float
    got: float
    match: bool
    pct_error: float = 0.0


@dataclass
class PipelineAudit:
    pdf_name: str
    output_path: str
    started_at: str = ""
    finished_at: str = ""
    block_c_filled: int = 0
    block_d_filled: int = 0
    confidence_pct: float = 0.0
    validation_passed: bool = False
    rules_score_pct: float = 0.0
    golden_score_pct: float = 0.0
    ocr_pages: List[PageOcrRecord] = field(default_factory=list)
    validation_errors: List[ValidationRecord] = field(default_factory=list)
    field_accuracy: List[FieldAccuracy] = field(default_factory=list)
    mapping_status: Dict[str, Any] = field(default_factory=dict)
    mismatches: List[str] = field(default_factory=list)
    missing_fields: List[str] = field(default_factory=list)
    low_confidence_pages: List[int] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AuditSession:
    """One run = one folder under logs/{pdf_stem}/."""

    def __init__(self, pdf_path: str, base_log_dir: str = "logs") -> None:
        self.pdf_path = pdf_path
        self.stem = Path(pdf_path).stem.replace(" ", "_")
        self.log_dir = Path(base_log_dir) / self.stem
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.audit = PipelineAudit(
            pdf_name=os.path.basename(pdf_path),
            output_path="",
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        self._loggers: Dict[str, logging.Logger] = {}

    def get_logger(self, name: str, filename: str) -> logging.Logger:
        if name in self._loggers:
            return self._loggers[name]
        logger = logging.getLogger(f"compile.{self.stem}.{name}")
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()
        fh = logging.FileHandler(self.log_dir / filename, encoding="utf-8")
        fh.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        ))
        logger.addHandler(fh)
        sh = logging.StreamHandler()
        sh.setLevel(logging.INFO)
        sh.setFormatter(logging.Formatter("%(levelname)s | %(message)s"))
        logger.addHandler(sh)
        logger.propagate = False
        self._loggers[name] = logger
        return logger

    @property
    def extraction_logger(self) -> logging.Logger:
        return self.get_logger("extraction", "extraction.log")

    @property
    def ocr_logger(self) -> logging.Logger:
        return self.get_logger("ocr", "ocr.log")

    @property
    def verification_logger(self) -> logging.Logger:
        return self.get_logger("verification", "verification.log")

    @property
    def mapping_logger(self) -> logging.Logger:
        return self.get_logger("mapping", "mapping.log")

    def save_ocr_page(self, page: int, text: str) -> None:
        path = self.log_dir / "ocr_pages" / f"page_{page:02d}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def finalize(
        self,
        result: Any,
        output_path: str,
        rules_report: Any,
        golden_report: Optional[Any] = None,
        field_details: Optional[List[FieldAccuracy]] = None,
    ) -> Path:
        self.audit.output_path = output_path
        self.audit.finished_at = datetime.now(timezone.utc).isoformat()
        if result:
            self.audit.block_c_filled = sum(
                1 for r in result.block_c
                if any(r.get(k, 0) for k in (
                    "gross_opening", "net_closing", "net_opening"
                ))
            )
            self.audit.block_d_filled = sum(
                1 for r in result.block_d
                if r.get("opening_rs") or r.get("closing_rs")
            )
            self.audit.confidence_pct = getattr(result, "confidence", 0.0)
            self.audit.validation_passed = getattr(result, "passed", False)
            for e in getattr(result, "errors", []) or []:
                self.audit.validation_errors.append(ValidationRecord(
                    block=e.block,
                    sl_no=e.sl_no,
                    field=e.field,
                    message=e.message,
                    expected=getattr(e, "expected", 0),
                    got=getattr(e, "got", 0),
                ))
        if rules_report:
            self.audit.rules_score_pct = rules_report.score_pct
            self.audit.mismatches.extend(rules_report.failures)
        if golden_report:
            self.audit.golden_score_pct = golden_report.score_pct
            self.audit.mismatches.extend(golden_report.failures)
        if field_details:
            self.audit.field_accuracy = field_details
            self.audit.missing_fields = [
                f"{f.block}{f.sl_no} {f.field}"
                for f in field_details
                if f.expected != 0 and f.got == 0
            ]
        audit_path = self.log_dir / "audit_report.json"
        audit_path.write_text(
            json.dumps(self.audit.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        self.verification_logger.info("Audit saved: %s", audit_path)
        vr_path = self.log_dir / "validation_result.json"
        if vr_path.is_file():
            self.verification_logger.info(
                "BS cross-check JSON: %s (status=false → re-verified from balance sheet)",
                vr_path,
            )
        return audit_path
