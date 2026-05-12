import os
import time
import json
import pandas as pd
import google.generativeai as genai
from pydantic import BaseModel, Field
from typing import List, Optional

# ==========================================
# ENTERPRISE ONE-SHOT COMPILE SHEET FILLER
# AI-Powered Mapping for Block C and Block D
# ==========================================

# ⚠️ IMPORTANT: Please add your Gemini API Key below
API_KEY = "AIzaSyBaGeVyVGa8lH3lRpnxN9RFR4QbFPlJd7w" # Get it from https://aistudio.google.com/
genai.configure(api_key=API_KEY)

# ==========================================
# 1. PYDANTIC SCHEMAS (Defining the exact structure of Block C and D)
# ==========================================

class BlockCRow(BaseModel):
    sl_no: int = Field(description="Serial Number")
    asset_type: str = Field(description="Types of Asset")
    gross_opening: Optional[float] = Field(default=0.0, description="Gross Value Opening As On 01/04/2023")
    gross_addition_reval: Optional[float] = Field(default=0.0, description="Gross Value Addition Due to revaluation")
    gross_addition_actual: Optional[float] = Field(default=0.0, description="Gross Value Actual addition")
    gross_deduction: Optional[float] = Field(default=0.0, description="Gross Value Deduction & adjustment during the year")
    gross_closing: Optional[float] = Field(default=0.0, description="Gross Value Closing as on 31/03/2024")
    dep_up_to_beginning: Optional[float] = Field(default=0.0, description="Depreciation Up to year beginning")
    dep_provided_during_year: Optional[float] = Field(default=0.0, description="Depreciation Provided during the year")
    dep_adjustment: Optional[float] = Field(default=0.0, description="Depreciation Adjustment for sold/discarded")
    dep_up_to_end: Optional[float] = Field(default=0.0, description="Depreciation Up to year end")
    net_opening: Optional[float] = Field(default=0.0, description="Net Value Opening as on 01/04/2023")
    net_closing: Optional[float] = Field(default=0.0, description="Net Value Closing as on 31/03/2024")

class BlockDRow(BaseModel):
    sl_no: int = Field(description="Serial Number")
    item_name: str = Field(description="Name of the Item")
    opening_rs: Optional[float] = Field(default=0.0, description="Opening Balance (Rs.)")
    closing_rs: Optional[float] = Field(default=0.0, description="Closing Balance (Rs.)")

class CompileSheetData(BaseModel):
    block_c: List[BlockCRow] = Field(description="Must contain exactly 10 rows representing Land, Building, etc. up to Total.")
    block_d: List[BlockDRow] = Field(description="Must contain exactly 17 rows representing Raw Materials, etc. up to Outstanding loans.")

# ==========================================
# 2. LLM PROCESSING FUNCTION
# ==========================================
def extract_and_map_data(pdf_path: str) -> CompileSheetData:
    print(f"Uploading Balance Sheet PDF to Gemini for processing: {pdf_path}")
    
    # Upload the file to Gemini
    pdf_file = genai.upload_file(path=pdf_path)
    
    # Wait for the file to be processed
    while pdf_file.state.name == "PROCESSING":
        print(".", end="", flush=True)
        time.sleep(2)
        pdf_file = genai.get_file(pdf_file.name)
    
    if pdf_file.state.name == "FAILED":
        raise ValueError("PDF processing failed in Gemini.")
        
    print("\nPDF Uploaded and Processed. Initiating One-Shot Data Mapping...")

    # Define the System Instruction
    instruction = """
    You are an expert Financial Analyst and Data Entry Specialist.
    Your task is to map financial data from the provided Balance Sheet PDF into the EXACT JSON template below.
    
    CRITICAL INSTRUCTIONS:
    1. DO NOT change the "asset_type" or "item_name" values in the template. They must remain exactly as they are.
    2. Fill in the 0.0 values with the correct numbers from the PDF.
    3. If a value is missing or not applicable, leave it as 0.0.
    4. DO NOT scale the numbers to Lakhs or Crores. Write the exact full numbers (e.g., 313557000).
    5. Remove commas from numbers before parsing them as float.
    
    FILL IN AND RETURN THIS EXACT JSON FORMAT:
    {
      "block_c": [
        {"sl_no": 1, "asset_type": "Land", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0},
        {"sl_no": 2, "asset_type": "Building", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0},
        {"sl_no": 3, "asset_type": "Plant and Machinery", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0},
        {"sl_no": 4, "asset_type": "Transport Equipment", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0},
        {"sl_no": 5, "asset_type": "Computer Equipment & software", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0},
        {"sl_no": 6, "asset_type": "Pollution control equipment", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0},
        {"sl_no": 7, "asset_type": "Others", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0},
        {"sl_no": 8, "asset_type": "Sub-total(2 to 7)", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0},
        {"sl_no": 9, "asset_type": "Capital Work in Progress", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0},
        {"sl_no": 10, "asset_type": "Total(1+8+9)", "gross_opening": 0.0, "gross_addition_reval": 0.0, "gross_addition_actual": 0.0, "gross_deduction": 0.0, "gross_closing": 0.0, "dep_up_to_beginning": 0.0, "dep_provided_during_year": 0.0, "dep_adjustment": 0.0, "dep_up_to_end": 0.0, "net_opening": 0.0, "net_closing": 0.0}
      ],
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
        {"sl_no": 10, "item_name": "Other current assests", "opening_rs": 0.0, "closing_rs": 0.0},
        {"sl_no": 11, "item_name": "Total current assets(7 to 10)", "opening_rs": 0.0, "closing_rs": 0.0},
        {"sl_no": 12, "item_name": "Sundry creditors", "opening_rs": 0.0, "closing_rs": 0.0},
        {"sl_no": 13, "item_name": "Over draft,cash credit, other short term loan from banks & other financial institutions", "opening_rs": 0.0, "closing_rs": 0.0},
        {"sl_no": 14, "item_name": "Other current liabilities", "opening_rs": 0.0, "closing_rs": 0.0},
        {"sl_no": 15, "item_name": "Total Current liabilities(12 to 14)", "opening_rs": 0.0, "closing_rs": 0.0},
        {"sl_no": 16, "item_name": "Working Capital(11-15)", "opening_rs": 0.0, "closing_rs": 0.0},
        {"sl_no": 17, "item_name": "Outstanding loans(excluding interest but including deposits)", "opening_rs": 0.0, "closing_rs": 0.0}
      ]
    }
    """

    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=instruction,
        generation_config={
            "response_mime_type": "application/json",
            "temperature": 0.0 # Strict extraction
        }
    )

    response = model.generate_content(
        [pdf_file, "Extract the data into the JSON schema for Block C and Block D."],
    )
    
    print("Data Mapping Complete. Generating Excel...")
    
    import json
    # Clean up the file from Google servers
    genai.delete_file(pdf_file.name)
    
    return json.loads(response.text)

# ==========================================
# 3. EXCEL WRITING FUNCTION
# ==========================================
def create_excel_compile_sheet(data: dict, output_path: str):
    # Try different key names the LLM might have used
    block_c_raw = data.get("block_c") or data.get("Block C") or []
    block_d_raw = data.get("block_d") or data.get("Block D") or []
    
    # Block C DataFrame
    df_c = pd.DataFrame(block_c_raw)
    
    # Identify numerical columns in Block C and multiply by 100,000 (Lakhs to Rs)
    num_cols_c = ["gross_opening", "gross_addition_reval", "gross_addition_actual", "gross_deduction", 
                  "gross_closing", "dep_up_to_beginning", "dep_provided_during_year", "dep_adjustment", 
                  "dep_up_to_end", "net_opening", "net_closing"]
    for col in num_cols_c:
        if col in df_c.columns:
            df_c[col] = pd.to_numeric(df_c[col], errors='coerce').fillna(0) * 100000
    
    # Rename Block C columns if they exist
    c_cols = {
        "sl_no": "Sl No", "asset_type": "Types of Asset", "gross_opening": "Opening As On 01/04/2023",
        "gross_addition_reval": "Addition - Due to revaluation", "gross_addition_actual": "Addition - Actual addition",
        "gross_deduction": "Deduction & adjustment", "gross_closing": "Closing as on 31/03/2024",
        "dep_up_to_beginning": "Dep Up to year beginning", "dep_provided_during_year": "Dep Provided during the year",
        "dep_adjustment": "Dep Adjustment for sold", "dep_up_to_end": "Dep Up to year end",
        "net_opening": "Net Opening as on 01/04/2023", "net_closing": "Net Closing as on 31/03/2024"
    }
    df_c.rename(columns=c_cols, inplace=True, errors='ignore')

    # Block D DataFrame
    df_d = pd.DataFrame(block_d_raw)
    
    # Identify numerical columns in Block D and multiply by 100,000
    num_cols_d = ["opening_rs", "closing_rs"]
    for col in num_cols_d:
        if col in df_d.columns:
            df_d[col] = pd.to_numeric(df_d[col], errors='coerce').fillna(0) * 100000
            
    # Rename Block D columns if they exist
    d_cols = {
        "sl_no": "SlNo.", "item_name": "Items", "opening_rs": "Opening (Rs.)", "closing_rs": "Closing (Rs.)"
    }
    df_d.rename(columns=d_cols, inplace=True, errors='ignore')

    # Write to Excel
    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        df_c.to_excel(writer, sheet_name='Block C - Fixed Assets', index=False)
        df_d.to_excel(writer, sheet_name='Block D - Working Capital', index=False)
        
    print(f"SUCCESS: Compile Sheet successfully saved to {output_path}")

# ==========================================
# 4. MAIN EXECUTION
# ==========================================
if __name__ == "__main__":
    balance_sheet_pdf = r"C:\Users\anshu\Desktop\extract\data\Balance Sheet of DSL 114045 (1).pdf"
    output_excel = r"C:\Users\anshu\Desktop\extract\outputs\Compile_Schedule_Filled.xlsx"
    
    if API_KEY == "YOUR_GEMINI_API_KEY_HERE":
        print("ERROR: Please add your Gemini API Key at the top of the script first!")
    else:
        try:
            mapped_data = extract_and_map_data(balance_sheet_pdf)
            create_excel_compile_sheet(mapped_data, output_excel)
        except Exception as e:
            print(f"An error occurred: {e}")
