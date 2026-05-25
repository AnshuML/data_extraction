"""Enterprise compile sheet extraction from balance sheet PDFs."""

from compile_extraction.config import SETTINGS, Settings
from compile_extraction.financial_validation import (
    apply_financial_reconciliation,
    validate_financial_integrity,
)
from compile_extraction.quality import score_against_golden, score_extraction

__version__ = "1.0.0"
__all__ = [
    "SETTINGS",
    "Settings",
    "score_extraction",
    "score_against_golden",
    "validate_financial_integrity",
    "apply_financial_reconciliation",
]


def run_pipeline(*args, **kwargs):
    """Lazy import avoids circular dependency with run_agentic_pipeline."""
    from compile_extraction.pipeline import run_pipeline as _run
    return _run(*args, **kwargs)
