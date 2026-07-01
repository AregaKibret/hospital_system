$scriptPath = Join-Path $PSScriptRoot '..\..\scripts\claude_auto_resume.ps1'
$workingDirectory = 'C:\Users\agi1w\hospital_system'
$taskName = 'ClaudeAutoResume'

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`"" -WorkingDirectory $workingDirectory
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RunOnlyIfNetworkAvailable

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Automatically resume Claude Code after session limits' -Force

Write-Host "Scheduled task '$taskName' created."
Write-Host "It will start at logon and keep running from $workingDirectory."
