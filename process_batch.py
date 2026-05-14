import os
import glob
import logging
from fill_compile_sheet import extract_and_map_data, create_excel_compile_sheet

# ==========================================
# ENTERPRISE BATCH PROCESSING WRAPPER (GEMINI)
# Fully Automated Pipeline for Multiple Balance Sheets
# ==========================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BatchCompileProcessor:
    def __init__(self, data_dir="data", output_dir="outputs"):
        self.data_dir = data_dir
        self.output_dir = output_dir
        
        # Ensure output directory exists
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

    def process_single_file(self, pdf_path):
        filename = os.path.basename(pdf_path)
        base_name = os.path.splitext(filename)[0]
        
        # Skip the blank compilation schedule template if it's in the folder
        if "Compile schedule" in filename:
            logger.info(f"Skipping template file: {filename}")
            return

        logger.info(f"========== STARTING PROCESSING: {filename} ==========")
        
        # Output Excel file name will be unique for each PDF
        output_excel_path = os.path.join(self.output_dir, f"Compile_Filled_{base_name}.xlsx")
        
        try:
            logger.info(f"Extracting and mapping data via Gemini API for {filename}...")
            mapped_data = extract_and_map_data(pdf_path)
            
            logger.info(f"Generating Excel Compilation Sheet at {output_excel_path}...")
            create_excel_compile_sheet(mapped_data, output_excel_path)
            
            logger.info(f"SUCCESS: Finished processing {filename}")
        except Exception as e:
            logger.error(f"FAILED to process {filename}. Error: {e}")

    def run_batch(self):
        # Dynamically find ALL pdf files in the data directory
        pdf_files = glob.glob(os.path.join(self.data_dir, "*.pdf"))
        if not pdf_files:
            logger.error(f"No PDF files found in '{self.data_dir}' folder.")
            return
            
        logger.info(f"Found {len(pdf_files)} PDF files to process in batch.")
        
        # Process them one by one automatically
        for idx, pdf_path in enumerate(pdf_files):
            logger.info(f"\n--- Processing File {idx+1} of {len(pdf_files)} ---")
            self.process_single_file(pdf_path)
            
        logger.info("========== BATCH PROCESSING COMPLETE ==========")

if __name__ == "__main__":
    # Point data_dir to where the PDFs live. Output will go to 'outputs' folder.
    processor = BatchCompileProcessor(
        data_dir=r"C:\Users\anshu\Desktop\extract\data",
        output_dir=r"C:\Users\anshu\Desktop\extract\outputs"
    )
    processor.run_batch()
