import os
import json
import requests
import fitz  # PyMuPDF
import pytesseract
from PIL import Image
import io
import pandas as pd
from typing import Dict, Any

# ==========================================
# 100% OPEN SOURCE COMPILE SHEET FILLER
# (Memory-Optimized for Local LLMs)
# ==========================================

pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
OLLAMA_URL = "http://localhost:11434/api/generate"
LOCAL_MODEL = "llama3" 

def extract_text_from_pdf(pdf_path: str) -> str:
    print(f"\n Extracting text from {os.path.basename(pdf_path)}...")
    pdf_document = fitz.open(pdf_path)
    full_text = ""
    for page_num in range(len(pdf_document)):
        print(f"   -> Scanning Page {page_num+1}...")
        page = pdf_document[page_num]
        pix = page.get_pixmap(dpi=200) # Reduced DPI slightly to save memory
        img_bytes = pix.tobytes("png")
        img = Image.open(io.BytesIO(img_bytes))
        full_text += pytesseract.image_to_string(img) + "\n"
    pdf_document.close()
    return full_text

def get_block_c_schema() -> str:
    return """
    {
      "block_c": [
        {"sl_no": 1, "asset_type": "Land", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0},
        {"sl_no": 2, "asset_type": "Building", "gross_opening": 0.0, "gross_closing": 0.0, "net_closing": 0.0},
        {"sl_no": 3, "asset_type": "Plant and Machinery", "gross_opening": 0.0, "gross_closing": 0.0, "net_closing": 0.0},
        {"sl_no": 4, "asset_type": "Transport Equipment", "gross_opening": 0.0, "gross_closing": 0.0, "net_closing": 0.0},
        {"sl_no": 10, "asset_type": "Total", "gross_opening": 0.0, "gross_closing": 0.0, "net_closing": 0.0}
      ]
    }
    """

def get_block_d_schema() -> str:
    return """
    {
      "block_d": [
        {"sl_no": 1, "item_name": "Raw Materials", "opening_rs": 0.0, "closing_rs": 0.0},
        {"sl_no": 6, "item_name": "Finished goods", "opening_rs": 0.0, "closing_rs": 0.0},
        {"sl_no": 8, "item_name": "Cash in Hand & at Bank", "opening_rs": 0.0, "closing_rs": 0.0},
        {"sl_no": 9, "item_name": "Sundry Debtors", "opening_rs": 0.0, "closing_rs": 0.0},
        {"sl_no": 12, "item_name": "Sundry creditors", "opening_rs": 0.0, "closing_rs": 0.0},
        {"sl_no": 16, "item_name": "Working Capital", "opening_rs": 0.0, "closing_rs": 0.0}
      ]
    }
    """

def process_block(text: str, schema: str, block_name: str) -> Dict[str, Any]:
    print(f"\n Asking Ollama to extract ONLY {block_name}...")
    
    prompt = f"""
    You are a Financial Analyst. Extract data matching this EXACT JSON structure.
    If a value is not found, output 0.0.
    
    Structure:
    {schema}

    Text:
    {text[:8000]} 
    """
    # Notice we limit text to 8000 chars so Llama 3 doesn't crash

    payload = {
        "model": LOCAL_MODEL,
        "prompt": prompt,
        "format": "json",
        "stream": False
    }

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=300)
        if response.status_code == 200:
            result_text = response.json().get("response", "").strip()
            print(f" {block_name} Extraction Successful!")
            return json.loads(result_text)
        else:
            print(f" Ollama Error: {response.status_code}")
            return {}
    except Exception as e:
        print(f" Error communicating with Ollama: {e}")
        return {}

def create_excel(block_c_data: Dict, block_d_data: Dict, output_path: str):
    print("\n Writing structured data to Excel Compilation Sheet...")
    
    df_c = pd.DataFrame(block_c_data.get("block_c", []))
    df_d = pd.DataFrame(block_d_data.get("block_d", []))

    if df_c.empty and df_d.empty:
        print(" Warning: Ollama returned empty data. Excel will be blank.")
        return

    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        df_c.to_excel(writer, sheet_name='Block C - Fixed Assets', index=False)
        df_d.to_excel(writer, sheet_name='Block D - Working Capital', index=False)
        
    print(f" SUCCESS! Open Source Compilation Complete. Saved to: {output_path}")

if __name__ == "__main__":
    balance_sheet_pdf = r"C:\Users\anshu\Desktop\extract\data\Balance Sheet of DSL 114045 (1).pdf"
    output_excel = r"C:\Users\anshu\Desktop\extract\outputs\Compile_Schedule_Filled_Local.xlsx"
    
    extracted_text = extract_text_from_pdf(balance_sheet_pdf)
    
    if len(extracted_text.strip()) > 50:
        # Pass 1: Block C
        c_data = process_block(extracted_text, get_block_c_schema(), "Block C")
        # Pass 2: Block D
        d_data = process_block(extracted_text, get_block_d_schema(), "Block D")
        
        # Combine and Write
        create_excel(c_data, d_data, output_excel)
    else:
        print(" Failed to extract text from PDF.")
