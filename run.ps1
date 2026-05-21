# Production run — Balance Sheet PDF → Compile Sheet Excel
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (Test-Path ".\venv\Scripts\Activate.ps1") { .\venv\Scripts\Activate.ps1 }

$env:OLLAMA_BASE_URL = "http://localhost:11435"
$env:OLLAMA_HOST = "http://localhost:11435"
$env:OLLAMA_VISION_MODEL = "gemma4:31b"
$env:OLLAMA_TEXT_MODEL = "gemma4:31b"

$Pdf = if ($args[0]) { $args[0] } else { "data\Balance Sheet of DSL 118184 (1).pdf" }
$Out = if ($args[1]) { $args[1] } else { "outputs\Compile_output.xlsx" }

python main.py $Pdf -o $Out --save-ocr
exit $LASTEXITCODE
