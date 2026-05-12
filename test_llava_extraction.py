import os
import fitz
import base64
import requests
import json
import io
from PIL import Image

OLLAMA_URL = "http://localhost:11434/api/generate"
VISION_MODEL = "llava"

def image_to_base64(pixmap):
    img_bytes = pixmap.tobytes("png")
    return base64.b64encode(img_bytes).decode('utf-8')

pdf_path = r'C:\Users\anshu\Desktop\extract\data\Balance Sheet of DSL 114045 (1).pdf'
doc = fitz.open(pdf_path)

print(f"Total Pages: {len(doc)}")

# Test on first 3 pages just to see the layout and if LLaVA can find the data
for i in range(min(5, len(doc))):
    print(f"Processing Page {i+1}...")
    page = doc[i]
    pix = page.get_pixmap(dpi=150)
    base64_img = image_to_base64(pix)
    
    prompt = """
    Analyze this financial document page. 
    1. If you see a 'Fixed Assets' or 'Depreciation' schedule (like Land, Building, Plant and Machinery), extract the Gross Opening, Additions, and Net Closing values.
    2. If you see 'Current Assets' or 'Current Liabilities' (like Inventory, Cash, Debtors), extract their Opening and Closing values.
    If you don't see these, just reply 'Not Found'.
    """
    
    payload = {
        "model": VISION_MODEL,
        "prompt": prompt,
        "images": [base64_img],
        "stream": False
    }
    
    response = requests.post(OLLAMA_URL, json=payload, timeout=300)
    if response.status_code == 200:
        result = response.json().get("response", "").strip()
        print(f"--- PAGE {i+1} RESULT ---")
        print(result)
    else:
        print(f"Error: {response.status_code}")
