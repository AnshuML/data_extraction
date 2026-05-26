"""Backward-compatible re-exports — use compile_extraction package."""
from compile_extraction.excel import write_excel
from compile_extraction.schema import (
    BLOCK_C_TEMPLATE,
    BLOCK_D_TEMPLATE,
    clean_number,
    extract_json_from_response,
    merge_with_template,
)

__all__ = [
    "BLOCK_C_TEMPLATE",
    "BLOCK_D_TEMPLATE",
    "clean_number",
    "extract_json_from_response",
    "merge_with_template",
    "write_excel",
]
