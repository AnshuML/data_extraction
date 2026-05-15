# Enterprise Balance Sheet Extraction — Setup Guide

## Step 1: Python packages install karo

```powershell
cd "C:\path\to\data_extraction"

pip install pymupdf pdfplumber pillow opencv-python-headless pytesseract rapidfuzz requests openpyxl pandas
```

---

## Step 2: Ollama models pull karo

```powershell
# Pehle Ollama install karo: https://ollama.com/download

ollama pull gemma3:4b       # Agent 1 — Extractor
ollama pull llama3.2:3b     # Agent 2 — Verifier
```

Agar yeh models nahi hain to alternatives:
```powershell
ollama pull qwen2.5:3b      # gemma3 ka alternative
ollama pull llama3:8b       # llama3.2 ka alternative (zyada accurate)
```

---

## Step 3: Ollama server start karo (alag terminal mein)

```powershell
ollama serve
```

---

## Step 4: `run_test.py` mein settings set karo

```python
PDF_PATH = r"C:\...\Balance Sheet of DSL 118184.pdf"
OUT_PATH = r"C:\...\outputs\DSL_118184_compile.xlsx"
SCALE    = 1          # 1=Rupees, 100000=Lakhs
EXTRACTOR_MODEL = "gemma3:4b"
VERIFIER_MODEL  = "llama3.2:3b"
```

---

## Step 5: Run karo

```powershell
python run_test.py
```

---

## Expected Output

```
═════════════════════════════════════════════════════════════════
  ENTERPRISE BALANCE SHEET EXTRACTION PIPELINE
═════════════════════════════════════════════════════════════════

[1/4] Pre-flight checks...
  ✓ PDF found: Balance Sheet of DSL 118184.pdf
  ✓ Tesseract found
  ✓ Ollama running
    Extractor: gemma3:4b
    Verifier : llama3.2:3b

[2/4] Starting extraction pipeline...
[3/4] Exporting Excel...
  ✓ Saved: ...DSL_118184_compile.xlsx

[4/4] Results Summary
  STATUS    : SUCCESS
  ATTEMPTS  : 1
  TOTAL TIME: 52.3s

── Block C — Fixed Assets ────────────────────────────────────
   Sl  Asset Type                           Gross Opening    Net Closing
   ──────────────────────────────────────────────────────────────────────
    1  Land                                     5,000,000      5,000,000
    2  Building                                12,500,000      8,200,000
   ...
► 10  Total (1+8+9)                            45,000,000     28,000,000

── Block D — Working Capital ────────────────────────────────
   ...
►16  Working Capital (11-15)                  12,000,000     14,500,000
```

---

## Excel Output — 4 Sheets

| Sheet | Content |
|-------|---------|
| Block C - Fixed Assets | 10 rows × 13 columns, colour coded |
| Block D - Working Capital | 17 rows × 3 columns, colour coded |
| Audit Log | Attempt history, verify rates |
| Legend | Colour meaning |

### Cell Colours
- 🟢 **Green** — verified, high confidence
- 🟡 **Yellow** — extracted, low confidence (manual review)
- 🔴 **Red** — could not verify against source text
- ⬜ **White** — zero / not found
- 🔵 **Blue** — computed total row

---

## Troubleshooting

| Error | Fix |
|-------|-----|
| `No tables detected` | PDF scanned hai — check Tesseract installation |
| `Ollama not reachable` | Run `ollama serve` in separate terminal |
| `model not found` | Run `ollama pull gemma3:4b` |
| `tesseract not found` | Set correct path in `config.py` → `TESSERACT_CMD` |
| All zeros in Block C/D | PDF format alag hai — `run_test.py` mein `SCALE=100000` try karo |
