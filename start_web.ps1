$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Virtual environment not found. Run: python -m venv .venv ; .\.venv\Scripts\python.exe -m pip install -e ."
}

& $python -m lemon_home.web
