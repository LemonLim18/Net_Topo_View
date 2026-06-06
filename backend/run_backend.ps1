# Start the NetTopoView backend using the project venv (required for Ansible Studio Check/Deploy).
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ScriptDir "venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "Venv not found at $VenvPython. Create it with: python -m venv venv" -ForegroundColor Red
    exit 1
}
Write-Host "Starting backend with venv Python: $VenvPython" -ForegroundColor Green
Set-Location $ScriptDir
& $VenvPython -m uvicorn backend:app --host 0.0.0.0 --port 8000 --reload
