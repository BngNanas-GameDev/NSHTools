@echo off
chcp 65001 >nul 2>&1
title NSH ALL IN ONE
cd /d "%~dp0"

echo ============================================
echo   NSH ALL IN ONE — Hub (supervise) + Web
echo   Modul lite/kma/wma/kmma di-auto-start hub
echo   via --supervise (console masing-masing).
echo   File .bat individual tetap bisa dipakai manual.
echo ============================================
echo.

echo [1] Starting Hub Server --supervise (WS :8765 + HTTP :8766)...
start "NSH Hub Server" python hub_server.py --supervise
ping -n 4 127.0.0.1 >nul

echo [2] Starting Web Dashboard (:8443)...
start "NSH Web" /D "Dashboard" cmd /c npm run dev
ping -n 6 127.0.0.1 >nul

echo.
echo All started!
echo   Web Dashboard (React) : http://localhost:8443
echo   Hub Klasik (HTML)     : http://localhost:8766
echo   WebSocket             : ws://localhost:8765
echo.
echo Press any key to stop all...
pause >nul

echo Stopping supervised modules (via PID file)...
python -c "import json,subprocess; p='supervise_pids.json'; d=json.load(open(p,encoding='utf-8')) if __import__('os').path.exists(p) else {}; [subprocess.run(['taskkill','/PID',str(pid),'/F'],capture_output=True) for pid in d.values()]" >nul 2>&1
del /Q supervise_pids.json >nul 2>&1

echo Stopping Hub + Web...
taskkill /FI "WINDOWTITLE eq NSH Hub Server*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq NSH Web*" /F >nul 2>&1
echo Done.
