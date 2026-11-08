@echo off
chcp 65001 >nul
powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*run.py*' }; if (-not $p) { Write-Host 'Bot is not running.'; exit 0 }; foreach ($x in $p) { Write-Host ('Stopping PID ' + $x.ProcessId); Stop-Process -Id $x.ProcessId -Force }"
