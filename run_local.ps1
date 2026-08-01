# Discord Bot - Local Run (PowerShell)

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "Discord Bot - Local Run" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

# Check if Python is installed
try {
    python --version | Out-Null
} catch {
    Write-Host "ERROR: Python is not installed or not in PATH" -ForegroundColor Red
    Write-Host "Please install Python from https://www.python.org/" -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}

# Create venv if it doesn't exist
if (-not (Test-Path "venv")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Yellow
    python -m venv venv
}

# Activate venv
& ".\venv\Scripts\Activate.ps1"

# Install requirements
Write-Host "Installing dependencies..." -ForegroundColor Yellow
pip install -q -r requirements.txt

# Check for .env file
if (-not (Test-Path ".env")) {
    Write-Host "`nERROR: .env file not found!" -ForegroundColor Red
    Write-Host "Please create a .env file with your bot token:" -ForegroundColor Yellow
    Write-Host "  BOT_TOKEN=your_discord_bot_token_here`n" -ForegroundColor White
    Read-Host "Press Enter to exit"
    exit 1
}

# Run bot
Write-Host "`nStarting bot...`n" -ForegroundColor Green
python main.py
