"""
Enterprise Balance Sheet Extraction — Test Runner
==================================================
Dusre system pe run karo jahan Ollama install hai.

Run:
    python run_test.py

Adjust karo:
    PDF_PATH   — apna balance sheet PDF path
    OUT_PATH   — output Excel path
    SCALE      — 1 agar PDF numbers Rupees mein, 100000 agar Lakhs mein
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ═══════════════════════════════════════════════════════
#  SETTINGS — sirf yahan change karo
# ═══════════════════════════════════════════════════════
PDF_PATH = r"C:\Users\DELL\Desktop\extract\data_extraction\data\Balance Sheet of DSL 118184.pdf"
OUT_PATH = r"C:\Users\DELL\Desktop\extract\data_extraction\outputs\DSL_118184_compile.xlsx"
SCALE    = 1        # 1 = Rupees already | 100000 = PDF numbers in Lakhs

# Ollama models (change agar alag model install hai)
EXTRACTOR_MODEL  = "gemma3:4b"    # ya "qwen2.5:3b" ya "gemma2:2b"
VERIFIER_MODEL   = "llama3.2:3b"  # ya "llama3:8b" ya "mistral:7b"

# Context window — model ke hisaab se set karo:
# gemma4:31b / gemma4:26b  → 256000
# gemma4:e4b / gemma4:e2b  → 128000
# gemma3:4b / qwen2.5:7b   → 32000
# llama3.1:8b / llama3.2:3b → 8000
CONTEXT_WINDOW = 32000
# ═══════════════════════════════════════════════════════

import config
config.PDF_UNIT_MULTIPLIER  = SCALE
config.EXTRACTOR_MODEL      = EXTRACTOR_MODEL
config.VERIFIER_MODEL       = VERIFIER_MODEL
config.OLLAMA_CONTEXT_WINDOW = CONTEXT_WINDOW
config.LLM_SNIPPET_MAX_CHARS = CONTEXT_WINDOW // 4

os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)

print("\n" + "═" * 65)
print("  ENTERPRISE BALANCE SHEET EXTRACTION PIPELINE")
print("═" * 65)

# ── Pre-flight checks ────────────────────────────────────────────
print("\n[1/4] Pre-flight checks...")

# PDF check
if not os.path.exists(PDF_PATH):
    print(f"\n  ✗ PDF not found: {PDF_PATH}")
    print("  Please update PDF_PATH in run_test.py")
    sys.exit(1)
print(f"  ✓ PDF found: {os.path.basename(PDF_PATH)}")

# Tesseract check
tesseract_ok = os.path.exists(config.TESSERACT_CMD)
if tesseract_ok:
    print(f"  ✓ Tesseract found: {config.TESSERACT_CMD}")
else:
    print(f"  ⚠ Tesseract not found at: {config.TESSERACT_CMD}")
    print("    Scanned PDFs will use pdfplumber only (text PDFs unaffected)")

# Ollama check
from utils.ollama_client import is_ollama_alive
ollama_ok = is_ollama_alive()
if ollama_ok:
    print(f"  ✓ Ollama running at {config.OLLAMA_BASE_URL}")
    print(f"    Extractor model : {EXTRACTOR_MODEL}")
    print(f"    Verifier model  : {VERIFIER_MODEL}")
else:
    print(f"  ⚠ Ollama not found at {config.OLLAMA_BASE_URL}")
    print("    Pipeline will run in DETERMINISTIC MODE (no LLM)")
    print("    To enable full mode: ollama serve")

# ── Run pipeline ─────────────────────────────────────────────────
print(f"\n[2/4] Starting extraction pipeline...")
print(f"  PDF  : {os.path.basename(PDF_PATH)}")
print(f"  Scale: ×{SCALE}")
print()

from supervisor.orchestrator import run as run_pipeline

try:
    result = run_pipeline(PDF_PATH)
except Exception as e:
    print(f"\n  ✗ Pipeline crashed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ── Export Excel ─────────────────────────────────────────────────
print(f"\n[3/4] Exporting Excel...")
from exporters.excel_exporter import export as export_excel

try:
    excel_path = export_excel(result, OUT_PATH)
    print(f"  ✓ Saved: {excel_path}")
except Exception as e:
    print(f"  ✗ Export failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ── Summary ──────────────────────────────────────────────────────
print(f"\n[4/4] Results Summary")
print("─" * 65)
print(f"  STATUS    : {result.final_status}")
print(f"  ATTEMPTS  : {len(result.attempts)}")
print(f"  TOTAL TIME: {result.total_elapsed}s")
print(f"  OUTPUT    : {excel_path}")
print()

# Attempt details
for att in result.attempts:
    print(f"  Attempt {att.attempt_no}: "
          f"Verifier={att.verifier_status} | "
          f"Auditor={att.auditor_status} | "
          f"VerifyRate={att.verify_summary.get('rate', 0)*100:.0f}% | "
          f"{att.elapsed_sec}s")

# ── Block C preview ──────────────────────────────────────────────
print("\n── Block C — Fixed Assets (all 10 rows) " + "─" * 25)
print(f"  {'Sl':>3}  {'Asset Type':<35}  {'Gross Opening':>16}  {'Net Closing':>14}")
print("  " + "─" * 73)
for row in result.block_c:
    sl   = row.get("sl_no", "")
    name = row.get("asset_type", "")
    go   = float(row.get("gross_opening", 0) or 0) * SCALE
    nc   = float(row.get("net_closing",   0) or 0) * SCALE
    marker = "►" if sl in (8, 10) else " "
    print(f"  {marker}{sl:>2}  {name:<35}  {go:>16,.2f}  {nc:>14,.2f}")

# ── Block D preview ──────────────────────────────────────────────
print("\n── Block D — Working Capital (all 17 rows) " + "─" * 22)
print(f"  {'Sl':>3}  {'Item':<50}  {'Opening':>12}  {'Closing':>12}")
print("  " + "─" * 83)
for row in result.block_d:
    sl   = row.get("sl_no", "")
    name = row.get("item_name", "")
    op   = float(row.get("opening_rs", 0) or 0) * SCALE
    cl   = float(row.get("closing_rs", 0) or 0) * SCALE
    marker = "►" if sl in (4, 7, 11, 15, 16) else " "
    print(f"  {marker}{sl:>2}  {name:<50}  {op:>12,.2f}  {cl:>12,.2f}")

# ── Audit failures ───────────────────────────────────────────────
last_attempt = result.attempts[-1]
if last_attempt.audit_failures:
    print(f"\n── Formula Audit Failures ({len(last_attempt.audit_failures)}) " + "─" * 30)
    for f in last_attempt.audit_failures:
        print(f"  ⚠  {f}")
else:
    print("\n  ✓ All formula checks PASSED")

print("\n" + "═" * 65)
print(f"  OUTPUT FILE: {excel_path}")
print("═" * 65 + "\n")
