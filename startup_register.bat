@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo [NSH] Registering autostart task "NSH Autostart" (ONLOGON + delay 5s, HIGHEST)...
schtasks /create /tn "NSH Autostart" /tr "\"%~dp0startup_auto.bat\"" /sc ONLOGON /delay 0000:05 /RL HIGHEST /F
if %errorlevel% neq 0 (
  echo [ERROR] Gagal membuat task. Klik kanan startup_register.bat -^> Run as administrator.
  pause
  exit /b 1
)
echo [OK] Task "NSH Autostart" terdaftar.
echo      Cek: schtasks /query /tn "NSH Autostart" /v /fo LIST
schtasks /query /tn "NSH Autostart" /v /fo LIST 2>nul | findstr /i "TaskName Trigger Run"
pause
