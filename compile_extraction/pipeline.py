"""Pipeline entry — delegates to production extractor."""
from __future__ import annotations

from typing import Any


def run_pipeline(*args: Any, **kwargs: Any):
    from run_agentic_pipeline import run_pipeline as _run
    return _run(*args, **kwargs)


__all__ = ["run_pipeline"]
