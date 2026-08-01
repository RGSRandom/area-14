$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$pidFile = Join-Path $repoRoot "bot_local.pid"
$stopFlag = Join-Path $repoRoot "stop_cloud.txt"
$localReadyFlag = Join-Path $repoRoot "local_ready.txt"

function Test-InternetConnection {
    try {
        $request = [System.Net.WebRequest]::Create("https://1.1.1.1")
        $request.Timeout = 5000
        $response = $request.GetResponse()
        $response.Close()
        return $true
    }
    catch {
        return $false
    }
}

function Test-LocalBotRunning {
    if (-not (Test-Path $pidFile)) {
        return $false
    }

    try {
        $pid = (Get-Content $pidFile -ErrorAction Stop).Trim()
        if ($pid -and (Get-Process -Id ([int]$pid) -ErrorAction SilentlyContinue)) {
            return $true
        }
    }
    catch {}

    return $false
}

function Get-RepoInfo {
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

    $token = $env:GITHUB_TOKEN
    if (-not $token) { $token = $env:GH_TOKEN }

    return @{ RepoName = $repoName; Token = $token }
}

function Get-ActiveWorkflowRuns {
    param([string]$RepoName, [string]$Token)

    if (-not $RepoName -or -not $Token) {
        return @()
    }

    $headers = @{ Authorization = "Bearer $Token"; Accept = "application/vnd.github+json" }
    $uri = "https://api.github.com/repos/$RepoName/actions/runs?per_page=100"

    try {
        $runs = Invoke-RestMethod -Method Get -Uri $uri -Headers $headers
        return @($runs.workflow_runs | Where-Object { $_.status -in @('queued','in_progress') })
    }
    catch {
        return @()
    }
}

function Cancel-ActiveWorkflowRuns {
    param([string]$RepoName, [string]$Token)

    $runs = Get-ActiveWorkflowRuns -RepoName $RepoName -Token $Token
    if (-not $runs) {
        return
    }

    $headers = @{ Authorization = "Bearer $Token"; Accept = "application/vnd.github+json" }
    foreach ($run in $runs) {
        try {
            Invoke-RestMethod -Method Post -Uri "https://api.github.com/repos/$RepoName/actions/runs/$($run.id)/cancel" -Headers $headers -ContentType "application/json" | Out-Null
        }
        catch {}
    }
}

function Dispatch-CloudWorkflow {
    param([string]$RepoName, [string]$Token)

    if (-not $RepoName -or -not $Token) {
        Write-Host "No GitHub token or repository found; cloud fallback skipped."
        return
    }

    $headers = @{ Authorization = "Bearer $Token"; Accept = "application/vnd.github+json" }
    $body = @{ ref = "main" } | ConvertTo-Json

    try {
        Invoke-RestMethod -Method Post -Uri "https://api.github.com/repos/$RepoName/actions/workflows/bot.yml/dispatches" -Headers $headers -ContentType "application/json" -Body $body | Out-Null
        Write-Host "Dispatched cloud workflow."
    }
    catch {
        Write-Host "Cloud workflow dispatch failed: $($_.Exception.Message)"
    }
}

$repoInfo = Get-RepoInfo
$repoName = $repoInfo.RepoName
$token = $repoInfo.Token

if (Test-InternetConnection) {
    if (Test-LocalBotRunning) {
        if (Test-Path $stopFlag) {
            Remove-Item $stopFlag -Force -ErrorAction SilentlyContinue
        }
        if (Test-Path $localReadyFlag) {
            Remove-Item $localReadyFlag -Force -ErrorAction SilentlyContinue
        }

        if ($repoName -and $token) {
            Cancel-ActiveWorkflowRuns -RepoName $repoName -Token $token
        }

        Write-Host "Internet is back and local bot is available; cloud run cancelled."
        exit 0
    }

    if (-not (Test-Path $localReadyFlag)) {
        & (Join-Path $repoRoot "scripts\run_local.ps1")
    }

    Write-Host "Internet is available; local startup was ensured."
    exit 0
}

if (Test-LocalBotRunning) {
    if (Test-Path $localReadyFlag) {
        Remove-Item $localReadyFlag -Force -ErrorAction SilentlyContinue
    }
    Dispatch-CloudWorkflow -RepoName $repoName -Token $token
    Write-Host "Internet is down; cloud fallback started."
    exit 0
}

Write-Host "No internet and local bot is not running."
