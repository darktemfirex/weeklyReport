# Auto-start the weekly report web server at Windows logon (no admin rights needed).
#   Install: powershell -ExecutionPolicy Bypass -File install_autostart.ps1
#   Remove:  powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -Remove
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 misreads non-ASCII text in BOM-less UTF-8 files.
param([switch]$Remove)
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$startup = [Environment]::GetFolderPath("Startup")
$target = Join-Path $startup "weekly-report-server.vbs"

if ($Remove) {
    Remove-Item -LiteralPath $target -ErrorAction SilentlyContinue
    Write-Host "Removed auto-start: $target"
    exit 0
}

# A .vbs launcher runs the server with no console window at all.
$command = 'powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""' + "$root\run_server.ps1" + '""'
$vbs = 'CreateObject("WScript.Shell").Run "' + $command + '", 0, False'
Set-Content -LiteralPath $target -Value $vbs -Encoding Unicode
Write-Host "Auto-start installed: $target"
Write-Host "From the next logon the app is at http://localhost:8501"
