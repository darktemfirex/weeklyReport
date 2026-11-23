# Starts the weekly report web UI (FastAPI + HTML form). Called at logon by the Startup entry made by install_autostart.ps1.
# - Does nothing if the port is already in use (server already running).
# - Binds to 127.0.0.1 only (this PC). Use -Address 0.0.0.0 to share on the network.
# - Log file is per port (logs\server-<port>.log) so an old server holding another log file cannot block startup.
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 misreads non-ASCII text in BOM-less UTF-8 files.
param(
    [int]$Port = 8501,
    [string]$Address = "127.0.0.1"
)
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) { exit 0 }

New-Item -ItemType Directory -Force -Path "$root\logs" | Out-Null
& "$root\.venv\Scripts\python.exe" -m uvicorn web:app --host $Address --port $Port *> "$root\logs\server-$Port.log"
