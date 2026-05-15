"""
Central configuration for the enterprise extraction pipeline.
All tuneable parameters live here — no magic numbers scattered across files.
"""
import os

# ─────────────────────────────────────────────
# OLLAMA
# ─────────────────────────────────────────────
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_API_URL:  str = f"{OLLAMA_BASE_URL}/api/generate"

# Local models — change here once, affects entire pipeline
# 8GB  RAM: gemma4:e4b   + llama3.2:3b   (~13 GB)
# 12GB RAM: gemma4:e4b   + llama3.1:8b   (~16 GB)
# 16GB RAM: gemma4:26b   + llama3.2:3b   (~21 GB)
# 32GB RAM: gemma4:31b   + llama3.1:8b   (~26 GB) ← best accuracy
EXTRACTOR_MODEL: str = os.getenv("EXTRACTOR_MODEL", "gemma3:4b")   # Agent 1
VERIFIER_MODEL:  str = os.getenv("VERIFIER_MODEL",  "llama3.2:3b") # Agent 2

OLLAMA_TIMEOUT_EXTRACTOR: int = 300   # seconds
OLLAMA_TIMEOUT_VERIFIER:  int = 180

# Context window — set according to your model
# gemma4:31b / gemma4:26b → 256000
# gemma4:e4b / gemma4:e2b → 128000
# gemma3:4b / qwen2.5:7b  → 32000
# llama3.1:8b / 3.2:3b    → 8000
OLLAMA_CONTEXT_WINDOW: int = int(os.getenv("OLLAMA_CONTEXT_WINDOW", "32000"))

# Max text snippet sent to LLM per call (stays well within context limit)
LLM_SNIPPET_MAX_CHARS: int = OLLAMA_CONTEXT_WINDOW // 4

# ─────────────────────────────────────────────
# PDF PROCESSING
# ─────────────────────────────────────────────
PDF_DPI: int = 300           # Higher = better OCR, slower
USE_SURYA: bool = os.getenv("USE_SURYA", "0") == "1"   # Set USE_SURYA=1 to use Surya OCR instead of Tesseract (requires GPU for speed)
TESSERACT_CMD: str = os.getenv(
    "TESSERACT_CMD",
    r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)
TESSERACT_CONFIG: str = "--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz.,()/-& "

# ─────────────────────────────────────────────
# TABLE DETECTION
# ─────────────────────────────────────────────
# pdfplumber table settings
PDFPLUMBER_TABLE_SETTINGS: dict = {
    "vertical_strategy":   "lines",
    "horizontal_strategy": "lines",
    "snap_tolerance":      5,
    "join_tolerance":      3,
    "edge_min_length":     10,
    "min_words_vertical":  1,
    "min_words_horizontal": 1,
}

# Fallback: explicit text strategy for borderless tables
PDFPLUMBER_TEXT_TABLE_SETTINGS: dict = {
    "vertical_strategy":   "text",
    "horizontal_strategy": "text",
    "snap_tolerance":      8,
}

# OpenCV grid detection (scanned PDFs)
OPENCV_LINE_THRESHOLD: int  = 100  # minimum line length in pixels
OPENCV_LINE_GAP:       int  = 10
OPENCV_KERNEL_SIZE:    int  = 40   # for morphological grid detection

# ─────────────────────────────────────────────
# ROW MAPPING (RapidFuzz)
# ─────────────────────────────────────────────
FUZZY_ACCEPT_THRESHOLD:  int = 80   # ≥ 80 → accept directly
FUZZY_LLM_THRESHOLD:     int = 50   # 50–79 → send to LLM for help
# < 50 → mark as UNMATCHED

# ─────────────────────────────────────────────
# AGENTIC LOOP
# ─────────────────────────────────────────────
MAX_RETRIES: int = 3           # Max extraction attempts before giving up
VERIFIER_NUMBER_TOLERANCE: float = 0.01   # 1% tolerance for float comparison

# ─────────────────────────────────────────────
# SCALE / UNIT
# ─────────────────────────────────────────────
# Set to 1 if PDF numbers are already in Rupees
# Set to 100_000 if PDF numbers are in Lakhs
PDF_UNIT_MULTIPLIER: int = int(os.getenv("PDF_UNIT_MULTIPLIER", "1"))

# ─────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────
BASE_DIR:    str = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR:  str = os.path.join(BASE_DIR, "..", "outputs")
LOG_DIR:     str = os.path.join(BASE_DIR, "..", "logs")
