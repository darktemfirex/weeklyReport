# Registers a Task Scheduler job that keeps the weekly report web server running (no admin rights, no password needed).
#   Install: powershell -ExecutionPolicy Bypass -File install_task.ps1
#   Remove:  powershell -ExecutionPolicy Bypass -File install_task.ps1 -Remove
# How it works:
# - Starts at logon and re-runs every 5 minutes. run_server.ps1 does nothing if the port is already in use,
#   so a healthy server is left alone and a dead one is restarted within 5 minutes.
# - Runs only while this user is logged on (the server needs the user's .venv and .env).
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 misreads non-ASCII text in BOM-less UTF-8 files.
param([switch]$Remove)
$name = "WeeklyReportServer"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed scheduled task: $name"
    exit 0
}

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$vbs = Join-Path $root "run_server_hidden.vbs"
$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$vbs`"" -WorkingDirectory $root
# Trigger 1: at logon, then every 5 minutes (the repetition starts from the next logon).
$logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$logon.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)).Repetition
# Trigger 2: every 5 minutes starting now, so the watchdog also works in the current session.
$every = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$trigger = @($logon, $every)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "Keeps the weekly report web server running (restarts within 5 minutes if it stops)." -Force | Out-Null
Write-Host "Registered scheduled task: $name (at logon, then every 5 minutes)"
