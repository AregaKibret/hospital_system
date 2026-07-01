param(
    [int]$MaxHours = 2,
    [int]$RestartDelaySeconds = 10
)

$maxRuntimeSeconds = $MaxHours * 60 * 60
$startTime = Get-Date
$iteration = 0

$claudeCommand = Get-Command claude -ErrorAction SilentlyContinue
$wrapperScript = Join-Path $PSScriptRoot 'claude_with_autoyes.ps1'
if (-not $claudeCommand) {
    Write-Host "Claude executable was not found on PATH. Please install or add it to PATH before scheduling this task."
    exit 1
}
if (-not (Test-Path $wrapperScript)) {
    Write-Host "Auto-yes wrapper script was not found: $wrapperScript"
    exit 1
}

while ($true) {
    $iteration++
    $elapsed = (Get-Date) - $startTime

    if ($elapsed.TotalSeconds -ge $maxRuntimeSeconds) {
        Write-Host "Reached the $MaxHours-hour window. Sending 'continue now' to Claude..."
        & powershell -NoProfile -ExecutionPolicy Bypass -File $wrapperScript "continue now"
        $exitCode = $LASTEXITCODE
        $startTime = Get-Date
    }
    else {
        Write-Host "Starting Claude session #$iteration..."
        & powershell -NoProfile -ExecutionPolicy Bypass -File $wrapperScript --continue
        $exitCode = $LASTEXITCODE
    }

    if ($exitCode -eq 0) {
        Write-Host "Claude exited normally. Restarting in $RestartDelaySeconds seconds..."
    }
    else {
        Write-Host "Claude exited with code $exitCode. Restarting in $RestartDelaySeconds seconds..."
    }

    Start-Sleep -Seconds $RestartDelaySeconds
}
