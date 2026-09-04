# Discord Bot - Local Run (PowerShell)

$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$mainFile = Join-Path $repoRoot "main.py"
$pidFile = Join-Path $repoRoot "bot_local.pid"
$taskName = "DiscordBotCloudOnShutdown"
$taskScript = Join-Path $repoRoot "scripts\switch_to_cloud.ps1"
$logPath = Join-Path $repoRoot "discord-bot-local.log"
$localReadyFlag = Join-Path $repoRoot "local_ready.txt"

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "Discord Bot - Local Run" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

$taskExists = $false
try {
    schtasks.exe /Query /TN $taskName 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $taskExists = $true }
}
catch {}

if (-not $taskExists) {
    $taskCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$taskScript`""
    schtasks.exe /Create /TN $taskName /TR $taskCommand /SC ONLOGOFF /RL HIGHEST /F | Out-Null
    Write-Host "Registered logoff handoff task." -ForegroundColor Green
}

if (Test-Path $pidFile) {
    try {
        $existingPid = Get-Content $pidFile -ErrorAction Stop
        if ($existingPid -and (Get-Process -Id ([int]$existingPid) -ErrorAction SilentlyContinue)) {
            Write-Host "Bot is already running with PID $existingPid" -ForegroundColor Yellow
            exit 0
        }
    }
    catch {}
}

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
if (-not (Test-Path (Join-Path $repoRoot "venv"))) {
    Write-Host "Creating virtual environment..." -ForegroundColor Yellow
    python -m venv (Join-Path $repoRoot "venv")
}

# Activate venv
& (Join-Path $repoRoot "venv\Scripts\Activate.ps1")

# Install requirements
Write-Host "Installing dependencies..." -ForegroundColor Yellow
pip install -q -r (Join-Path $repoRoot "requirements.txt")

# Check for .env file
if (-not (Test-Path (Join-Path $repoRoot ".env"))) {
    Write-Host "`nERROR: .env file not found!" -ForegroundColor Red
    Write-Host "Please create a .env file with your bot token:" -ForegroundColor Yellow
    Write-Host "  BOT_TOKEN=your_discord_bot_token_here`n" -ForegroundColor White
    Read-Host "Press Enter to exit"
    exit 1
}

# Run bot in the background
Write-Host "`nStarting bot...`n" -ForegroundColor Green
$errLogPath = Join-Path $repoRoot "discord-bot-local_err.log"
$process = Start-Process -FilePath (Join-Path $repoRoot "venv\Scripts\python.exe") -ArgumentList @("-u", $mainFile) -WorkingDirectory $repoRoot -WindowStyle Hidden -RedirectStandardOutput $logPath -RedirectStandardError $errLogPath -PassThru
$process.Id | Set-Content $pidFile
Set-Content -Path $localReadyFlag -Value "ready"
Write-Host "Bot started with PID $($process.Id)" -ForegroundColor Green