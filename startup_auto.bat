@echo off
chcp 65001 >nul 2>&1
title NSH Autostart (hidden)
cd /d "%~dp0"

timeout /t 5 /nobreak >nul

echo ============================================
echo   NSH ALL IN ONE — Hub (supervise) + Web
echo   Autostart hidden (delay 5s, pythonw, /min)
echo ============================================
echo.

echo [1] Starting Hub Server --supervise (WS :8765 + HTTP :8766)...
start /min "NSH Hub Server" pythonw hub_server.py --supervise
ping -n 4 127.0.0.1 >nul

echo [2] Starting Web Dashboard (:8443)...
start /min "NSH Web" /D "Dashboard" cmd /c npm run dev
ping -n 6 127.0.0.1 >nul

echo.
echo All started! (autostart hidden)
echo   Web Dashboard (React) : http://localhost:8443
echo   Hub Klasik (HTML)     : http://localhost:8766
echo   WebSocket             : ws://localhost:8765
echo.

exit /b
