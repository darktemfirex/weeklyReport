' Starts run_server.ps1 with no console window. Used by the scheduled task made by install_task.ps1.
' NOTE: keep this file ASCII-only.
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
CreateObject("WScript.Shell").Run "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & dir & "\run_server.ps1""", 0, False
