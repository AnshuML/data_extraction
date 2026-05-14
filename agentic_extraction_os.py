import os
import json
import logging
import requests
import fitz
import io
import base64
from PIL import Image

# Import the Excel generation logic from our successful script
from fill_compile_sheet import create_excel_compile_sheet

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==========================================
# 1. CONFIGURATION & MODELS
# ==========================================
OLLAMA_URL = "http://localhost:11434/api/generate"
VISION_MODEL = "llava"        # Agent 1: The Eyes (Sees the image)
REASONING_MODEL = "gemma4"    # Agent 2: The Brain (Formats JSON)

# ==========================================
# 2. SCHEMA DEFINITIONS (User's Brilliant Idea)
# ==========================================

def get_block_c_schema() -> str:
    """Returns the exact 13-column / 10-row structure for Block C."""
    schema = {
      "block_c": [
        {"sl_no": 1, "asset_type": "Land", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0},
        {"sl_no": 2, "asset_type": "Building", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0},
        {"sl_no": 3, "asset_type": "Plant and Machinary", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0},
        {"sl_no": 4, "asset_type": "Transport Equipment", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0},
        {"sl_no": 5, "asset_type": "Computer Equipment & software", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0},
        {"sl_no": 6, "asset_type": "Pollution control equipment", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0},
        {"sl_no": 7, "asset_type": "Others", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0},
        {"sl_no": 8, "asset_type": "Sub-total(2 to 7)", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0},
        {"sl_no": 9, "asset_type": "Capital Work in Progress", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0},
        {"sl_no": 10, "asset_type": "Total(1+8+9)", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0}
      ]
    }
    return json.dumps(schema, indent=2)

def get_block_d_schema() -> str:
    """Returns the exact 17-row structure for Block D."""
    schema = {
      "block_d": [
        {"sl_no": 1, "item_name": "Raw Materials & Components and Packing materials", "opening_rs": 0.0, "closing_rs": 0.0},
        {"sl_no": 2, "item_name": "Fuels & Lubricants", "opening_rs": 0.0, "closing_rs": 0.0},
        {"sl_no": 3, "item_name": "Spares, Stores & others", "opening_rs": 0.0, "closing_rs": 0.0},
        {"sl_no": 4, "item_name": "Sub-Total(1 to 3)", "opening_rs": 0.0, "closing_rs": 0.0},
        {"sl_no": 5, "item_name": "Semi-finished goods/work in progress", "opening_rs": 0.0, "closing_rs": 0.0},
        {"sl_no": 6, "item_name": "Finished goods", "opening_rs": 0.0, "closing_rs": 0.0},
        {"sl_no": 7, "item_name": "Total inventory(4 to 6)", "opening_rs": 0.0, "closing_rs": 0.0},
        {"sl_no": 8, "item_name": "Cash in Hand & at Bank", "opening_rs": 0.0, "closing_rs": 0.0},
        {"sl_no": 9, "item_name": "Sundry Debtors", "opening_rs": 0.0, "closing_rs": 0.0},
        {"sl_no": 10, "item_name": "Other current assets", "opening_rs": 0.0, "closing_rs": 0.0},
        {"sl_no": 11, "item_name": "Total current assets(7 to 10)", "opening_rs": 0.0, "closing_rs": 0.0},
        {"sl_no": 12, "item_name": "Sundry creditors", "opening_rs": 0.0, "closing_rs": 0.0},
        {"sl_no": 13, "item_name": "Over draft,cash credit, other short term loan from banks & other financial institutions", "opening_rs": 0.0, "closing_rs": 0.0},
        {"sl_no": 14, "item_name": "Other current liabilities", "opening_rs": 0.0, "closing_rs": 0.0},
        {"sl_no": 15, "item_name": "Total Current liabilities(12 to 14)", "opening_rs": 0.0, "closing_rs": 0.0},
        {"sl_no": 16, "item_name": "Working Capital(11-15)", "opening_rs": 0.0, "closing_rs": 0.0},
        {"sl_no": 17, "item_name": "Outstanding loans(excluding interest but including deposits)", "opening_rs": 0.0, "closing_rs": 0.0}
      ]
    }
    return json.dumps(schema, indent=2)

# ==========================================
# 3. THE VISION-REASONING PIPELINE
# ==========================================

def image_to_base64(image_bytes):
    return base64.b64encode(image_bytes).decode('utf-8')

def call_ollama_vision(model, prompt, image_base64):
    payload = {"model": model, "prompt": prompt, "images": [image_base64], "stream": False}
    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=1200) # Increased to 20 mins
        return response.json().get("response", "").strip()
    except Exception as e:
        logger.error(f"Vision Error (Timeout?): {e}"); return None

def call_ollama_reasoning(model, prompt):
    payload = {"model": model, "prompt": prompt, "stream": False}
    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=600)
        return response.json().get("response", "").strip()
    except Exception as e:
        logger.error(f"Reasoning Error: {e}"); return None

def parse_json(response_text):
    try:
        json_start = response_text.find('{')
        json_end = response_text.rfind('}') + 1
        return json.loads(response_text[json_start:json_end])
    except: return None

def agent_1_vision_extractor(image_base64, page_num):
    logger.info(f"-> Agent 1 (LLaVA) is viewing Page {page_num} image...")
    prompt = "Read the financial table on this page. List every item and its values (Opening, Closing, Addition, etc.). Be very precise. If no table, say NO DATA."
    text = call_ollama_vision(VISION_MODEL, prompt, image_base64)
    if text:
        logger.info(f"--- RAW VISION TEXT (Page {page_num}) ---\n{text[:500]}...") # Log first 500 chars
    return text

def agent_2_json_formatter(raw_extracted_text):
    logger.info(f"-> Agent 2 (Gemma 4) is formatting JSON...")
    prompt = f"""
    You are a Financial Data Entry specialist. 
    Map the following raw text into the JSON schema provided below. 
    If a specific number is not found, leave it as 0.0. Do not guess.
    
    SCHEMA:
    {get_block_c_schema()}
    {get_block_d_schema()}
    
    RAW TEXT:
    {raw_extracted_text}
    """
    res = call_ollama_reasoning(REASONING_MODEL, prompt)
    return parse_json(res) if res else None

# ==========================================
# 4. PROCESSING LOOP
# ==========================================

def process_pdf_agentic(pdf_path):
    logger.info(f"Starting Agentic Vision Pipeline for {os.path.basename(pdf_path)}...")
    pdf_fitz = fitz.open(pdf_path)
    master_json = {"block_c": [], "block_d": []}

    for page_num in range(len(pdf_fitz)):
        pix = pdf_fitz[page_num].get_pixmap(dpi=250) # Higher DPI for better vision
        image_base64 = image_to_base64(pix.tobytes("png"))
        
        vision_text = agent_1_vision_extractor(image_base64, page_num + 1)
        if not vision_text or "NO DATA" in vision_text.upper() or len(vision_text) < 20: 
            continue
            
        extracted_data = agent_2_json_formatter(vision_text)
        if extracted_data:
            # Check if we actually got any numbers
            c_data = extracted_data.get("block_c", [])
            d_data = extracted_data.get("block_d", [])
            
            if c_data or d_data:
                master_json["block_c"].extend(c_data)
                master_json["block_d"].extend(d_data)
                logger.info(f"+++ Captured data from Page {page_num + 1} +++")

    return master_json

if __name__ == "__main__":
    pdf_file = r"C:\Users\anshu\Desktop\extract\data\Balance Sheet of DSL 118184 (1).pdf"
    final_data = process_pdf_agentic(pdf_file)
    
    if final_data and (final_data['block_c'] or final_data['block_d']):
        output_excel = r"C:\Users\anshu\Desktop\extract\outputs\Compile_Schedule_Agentic_118184.xlsx"
        create_excel_compile_sheet(final_data, output_excel)
        logger.info(f"Agentic Pipeline SUCCESS! Result at {output_excel}")
    else:
        logger.error("Agentic Pipeline could not find verified data. Please check local model performance.")
