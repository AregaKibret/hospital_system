param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$claudeCommand = Get-Command claude -ErrorAction SilentlyContinue
if (-not $claudeCommand) {
    Write-Host "Claude executable was not found on PATH."
    exit 1
}

$allArgs = $Args
& $claudeCommand.Source @allArgs
exit $LASTEXITCODE
