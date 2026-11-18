# VibeCore Developer Automation for Windows PowerShell
# Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)

param (
    [string]$Action = "dev"
)

function Setup-VibeCore {
    Write-Host "Setting up VibeCore development environment..." -ForegroundColor Cyan
    python -m pip install -e ".[dev]"
    Write-Host "Installing Chrome Extension dependencies..." -ForegroundColor Cyan
    Push-Location chrome-extension
    npm install
    Pop-Location
    Write-Host "Installing Dashboard UI dependencies..." -ForegroundColor Cyan
    Push-Location dashboard-ui
    npm install
    Pop-Location
    Write-Host "Setup complete!" -ForegroundColor Green
}

function Build-Extension {
    Write-Host "Building Chrome Extension (Manifest V3)..." -ForegroundColor Cyan
    Push-Location chrome-extension
    npm run build
    Pop-Location
    Write-Host "Chrome Extension compiled into chrome-extension/dist/" -ForegroundColor Green
}

function Run-Tests {
    Write-Host "Running Python pytest suite..." -ForegroundColor Cyan
    pytest -v
    Write-Host "Verifying Chrome Extension build..." -ForegroundColor Cyan
    Push-Location chrome-extension
    npm run build
    Pop-Location
    Write-Host "Verifying Dashboard UI build..." -ForegroundColor Cyan
    Push-Location dashboard-ui
    npm run build
    Pop-Location
    Write-Host "All tests and builds passed!" -ForegroundColor Green
}

function Run-Dev {
    Write-Host "==========================================================" -ForegroundColor Green
    Write-Host "  Starting VibeCore Swarm Runtime & Live Dashboard" -ForegroundColor Green
    Write-Host "  Author: Sandeep Hipparagi" -ForegroundColor DarkGray
    Write-Host "==========================================================" -ForegroundColor Green

    python -c "import sys; from pathlib import Path; sys.path.insert(0, str(Path.cwd() / 'core-orchestrator')); from app.config import get_or_create_bridge_token; print('Active Bridge Token: ' + get_or_create_bridge_token())"

    Write-Host "Starting Orchestrator (:8000)..." -ForegroundColor Cyan
    $orchestrator = Start-Process python -ArgumentList "-m", "uvicorn", "app.main:app", "--app-dir", "core-orchestrator", "--reload", "--port", "8000" -PassThru

    Write-Host "Starting Dashboard UI (:5173)..." -ForegroundColor Cyan
    Push-Location dashboard-ui
    try {
        npm run dev
    } finally {
        Pop-Location
        Stop-Process -Id $orchestrator.Id -ErrorAction SilentlyContinue
    }
}

switch ($Action.ToLower()) {
    "setup" { Setup-VibeCore }
    "build-ext" { Build-Extension }
    "test" { Run-Tests }
    "dev" { Run-Dev }
    default {
        Write-Host "Usage: .\scripts\dev.ps1 [setup | dev | test | build-ext]" -ForegroundColor Yellow
    }
}
