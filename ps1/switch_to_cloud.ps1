$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$pidFile = Join-Path $repoRoot "bot_local.pid"
$stopFlag = Join-Path $repoRoot "stop_cloud.txt"
$localReadyFlag = Join-Path $repoRoot "local_ready.txt"

if (Test-Path $stopFlag) {
    Remove-Item $stopFlag -Force -ErrorAction SilentlyContinue
    Write-Host "Cloud handoff cancelled by stop flag."
    exit 0
}

if (Test-Path $localReadyFlag) {
    Set-Content -Path $stopFlag -Value "stop"
    Remove-Item $localReadyFlag -Force -ErrorAction SilentlyContinue
    Write-Host "Local server is ready; cloud handoff cancelled."
    exit 0
}

if (Test-Path $pidFile) {
    try {
        $pid = (Get-Content $pidFile -ErrorAction Stop).Trim()
        if ($pid -and (Get-Process -Id ([int]$pid) -ErrorAction SilentlyContinue)) {
            Stop-Process -Id ([int]$pid) -Force -ErrorAction SilentlyContinue
        }
    }
    catch {}
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

$repoName = $env:GITHUB_REPOSITORY
if (-not $repoName) {
    try {
        $remoteUrl = git -C $repoRoot remote get-url origin 2>$null
        if ($remoteUrl) {
            $repoName = $remoteUrl -replace '.*github\.com[:/]', '' -replace '\.git$', ''
        }
    }
    catch {}
}

if (-not $repoName) {
    Write-Host "No GitHub repository found."
    exit 1
}

$token = $env:GITHUB_TOKEN
if (-not $token) { $token = $env:GH_TOKEN }
if (-not $token) {
    Write-Host "No GitHub token found."
    exit 1
}

$headers = @{ Authorization = "Bearer $token"; Accept = "application/vnd.github+json" }
$body = @{ ref = "main" } | ConvertTo-Json

try {
    Invoke-RestMethod -Method Post -Uri "https://api.github.com/repos/$repoName/actions/workflows/bot.yml/dispatches" -Headers $headers -ContentType "application/json" -Body $body | Out-Null
    Write-Host "Cloud workflow dispatched."
}
catch {
    Write-Host "Cloud dispatch failed: $($_.Exception.Message)"
    exit 1
}
