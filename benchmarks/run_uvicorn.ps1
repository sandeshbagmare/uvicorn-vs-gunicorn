# Run the demo app under Uvicorn on Windows (PowerShell).
#   .\benchmarks\run_uvicorn.ps1            # 1 worker
#   .\benchmarks\run_uvicorn.ps1 -Workers 4 # 4 workers
#
# Note: on Windows Uvicorn uses the asyncio event loop (uvloop is Unix-only) and
# httptools if a wheel is available. Multiple --workers works on Windows; Gunicorn does not.
param(
    [int]$Workers = 1,
    [string]$AppHost = "127.0.0.1",
    [int]$Port = 8000
)
$ErrorActionPreference = "Stop"
# Run from the project root so `app.main:app` imports correctly.
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    Write-Host "Starting Uvicorn with $Workers worker(s) on http://${AppHost}:${Port}" -ForegroundColor Cyan
    python -m uvicorn app.main:app --host $AppHost --port $Port --workers $Workers
}
finally {
    Pop-Location
}
