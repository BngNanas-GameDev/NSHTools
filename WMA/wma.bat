@echo off
REM WMA v2 - Pusat Database KMMA
chcp 65001 >nul
title WMA v2 - Pusat Database KMMA
cd /d "%~dp0"
color 0A
echo ============================================================
echo   WMA v2 - Pusat Database KMMA
echo   Fetch data selesai dari Dashboard -> parse -> wma_daily.json
echo ============================================================
echo.
echo   [1] Sync sekali (single run)
echo   [2] Loop mode (setiap 60 detik)
echo.
set /p "pil=  Pilih [1/2]: "
if "%pil%"=="2" (
    python wma.py --loop --hub
) else (
    python wma.py --hub %*
)
echo.
pause
