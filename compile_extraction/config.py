"""Runtime configuration (environment + optional YAML)."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    ollama_base_url: str = "http://localhost:11435"
    vision_model: str = "gemma4:31b"
    text_model: str = "gemma4:31b"
    ollama_timeout: int = 600
    tesseract_cmd: str = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    max_attempts: int = 3
    dpi: int = 300
    tolerance: float = 0.02
    min_quality_pct: float = 95.0
    financial_validation_required: bool = True
    min_financial_validation_pct: float = 100.0

    @property
    def ollama_generate_url(self) -> str:
        return f"{self.ollama_base_url.rstrip('/')}/api/generate"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11435"),
            vision_model=os.environ.get("OLLAMA_VISION_MODEL", "gemma4:31b"),
            text_model=os.environ.get("OLLAMA_TEXT_MODEL", "gemma4:31b"),
            ollama_timeout=int(os.environ.get("OLLAMA_TIMEOUT", "600")),
            tesseract_cmd=os.environ.get(
                "TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe"
            ),
            max_attempts=int(os.environ.get("MAX_ATTEMPTS", "3")),
            dpi=int(os.environ.get("OCR_DPI", "300")),
            tolerance=float(os.environ.get("VALIDATION_TOLERANCE", "0.02")),
            min_quality_pct=float(os.environ.get("MIN_QUALITY_PCT", "95")),
            financial_validation_required=os.environ.get(
                "FINANCIAL_VALIDATION", "1"
            ).lower() not in ("0", "false", "no"),
            min_financial_validation_pct=float(
                os.environ.get("MIN_FINANCIAL_VALIDATION_PCT", "100")
            ),
        )


SETTINGS = Settings.from_env()
