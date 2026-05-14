# data_extraction

Enterprise level generic data extraction pipeline using Open Source Tools (PyMuPDF, pdfplumber, Tesseract OCR, Ollama Vision LLM).

## Features

- Extracts raw text, tables, and images from scanned Financial PDFs.
- Automatic OCR for scanned images using Tesseract.
- **Agentic Workflow Architecture:** Highly secure, local, multi-agent validation loop for 100% accuracy.

## Multi-Agent Verification Workflow (Architecture)

This project utilizes a highly secure, completely offline Open-Source AI system to extract and validate sensitive government financial data.

```mermaid
graph TD
    A([Start: Scanned Balance Sheet PDF]) --> B[Tesseract OCR Engine]
    B --> C(Raw OCR Text)
  
    C --> D{Agent 1: Gemma 4 Extractor}
  
    %% Extraction Phase
    D --> E(Draft JSON Data)
  
    %% Verification Phase
    E --> F{Agent 2: Llama 3 Verifier}
    C --> F
  
    F -->|REJECTED - Number hallucinated / missing| D
    F -->|APPROVED - Numbers strictly match text| G{Agent 3: Python Math Auditor}
  
    %% Mathematical Audit Phase
    G -->|REJECTED - Totals do not match| D
    G -->|APPROVED - Math is 100% Correct| H([Final Verified JSON Data])
  
    H --> I([Output: Excel Compilation Sheet])
  
    %% Node Styling
    classDef llm fill:#ffe5b4,stroke:#ff8c00,stroke-width:2px;
    classDef logic fill:#d4edda,stroke:#28a745,stroke-width:2px;
    classDef startend fill:#e2e3e5,stroke:#6c757d,stroke-width:2px;
    classDef process fill:#cce5ff,stroke:#007bff,stroke-width:2px;
  
    class D,F llm;
    class G logic;
    class A,H,I startend;
    class B,C,E process;
```

### 🔑 Key Components:

- **Agent 1 (Extractor):** Gemma 4 running locally via Ollama.
- **Agent 2 (Verifier):** Llama 3 running locally via Ollama.
- **Agent 3 (Math Auditor):** The Deterministic Python Checker ensuring mathematical perfection.
- **Supervisor Loop Logic:** If any Verifier (Agent 2 or Agent 3) rejects the data, it triggers an automatic loop back to Agent 1 to try again.

.\venv\Scripts\python agentic_extraction_os.py
