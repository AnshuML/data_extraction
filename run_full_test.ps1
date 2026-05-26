# Full pipeline test — both DSL balance sheets
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

$runs = @(
    @{
        Pdf    = "data\Balance Sheet of DSL 118184 (1).pdf"
        Out    = "outputs\Compile_DSL_118184.xlsx"
        Golden = "config\golden\dsl_118184.json"
    },
    @{
        Pdf    = "data\Balance Sheet of DSL 114045 (1).pdf"
        Out    = "outputs\Compile_DSL_114045.xlsx"
        Golden = "config\golden\dsl_114045.json"
    }
)

foreach ($r in $runs) {
    Write-Host "`n========== $($r.Pdf) ==========" -ForegroundColor Cyan
    & $py main.py $r.Pdf -o $r.Out --golden $r.Golden --save-ocr
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $py verify.py $r.Out --golden $r.Golden
    if ($LASTEXITCODE -ne 0) { Write-Host "Verify below threshold (see report above)" -ForegroundColor Yellow }
}

Write-Host "`nDone. Outputs in outputs\ and logs in logs\" -ForegroundColor Green
