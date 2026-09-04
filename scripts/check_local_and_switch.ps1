$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$localReadyFlag = Join-Path $repoRoot "local_ready.txt"
$stopFlag = Join-Path $repoRoot "stop_cloud.txt"
$pidFile = Join-Path $repoRoot "bot_local.pid"

$localAvailable = $false
if (Test-Path $pidFile) {
    try {
        $pid = (Get-Content $pidFile -ErrorAction Stop).Trim()
        if ($pid -and (Get-Process -Id ([int]$pid) -ErrorAction SilentlyContinue)) {
            $localAvailable = $true
        }
    }
    catch {}
}

if ($localAvailable) {
    Set-Content -Path $localReadyFlag -Value "ready"
    if (Test-Path $stopFlag) {
        Remove-Item $stopFlag -Force -ErrorAction SilentlyContinue
    }
    Write-Host "Local is available; cloud should stop."
    exit 0
}

if (Test-Path $localReadyFlag) {
    Remove-Item $localReadyFlag -Force -ErrorAction SilentlyContinue
}

Write-Host "Local is not available right now."
