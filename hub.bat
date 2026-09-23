@echo off
chcp 65001 >nul 2>&1
title NSH Hub
cd /d "%~dp0"

echo ============================================
echo   NSH Hub — WebSocket Server + Modules
echo   (semua modul kirim log --hub; atau pakai start_all.bat --supervise)
echo ============================================
echo.

echo [1] Starting Hub Server...
start "NSH Hub Server" python hub_server.py
ping -n 3 127.0.0.1 >nul

echo [2] Starting WMA (watch + hub)...
start "NSH WMA" python WMA\wma.py --watch --hub
ping -n 2 127.0.0.1 >nul

echo [3] Starting KMMA (watch + hub)...
start "NSH KMMA" python KMMA\kmma.py --watch --hub
ping -n 2 127.0.0.1 >nul

echo [4] Starting KMA (route + hub)...
start "NSH KMA" python KMA\route.py --hub

echo.
echo All modules started!
echo   Dashboard: http://localhost:8766
echo   Press any key to stop all...
pause >nul

echo Stopping...
taskkill /FI "WINDOWTITLE eq NSH Hub Server*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq NSH WMA*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq NSH KMMA*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq NSH KMA*" /F >nul 2>&1
echo Done.
