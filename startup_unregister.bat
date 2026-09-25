@echo off
chcp 65001 >nul 2>&1
echo [NSH] Menghapus task "NSH Autostart"...
schtasks /delete /tn "NSH Autostart" /f
if %errorlevel% neq 0 (
  echo [ERROR] Gagal menghapus task. Mungkin belum terdaftar atau perlu Run as administrator.
  pause
  exit /b 1
)
echo [OK] Task "NSH Autostart" dihapus.
pause
