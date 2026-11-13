$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        throw 'Run .\setup.ps1 before starting the Website Hunter.'
    }
    & $python -m uvicorn website_hunter.api:app --app-dir (Join-Path $PSScriptRoot 'src') --host 127.0.0.1 --port 8765
}
finally {
    Pop-Location
}
