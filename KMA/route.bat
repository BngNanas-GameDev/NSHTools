@echo off
REM NSH Router - INPUT -> Hot Folder printer
chcp 65001 >nul
title NSH Router - Hot Folder
cd /d "%~dp0"
color 0A
echo ============================================================
echo   NSH Router - INPUT -^> Hot Folder (cek 10.157/10.158 dulu)
echo   Tanpa argumen = watcher  ^|  --once = sekali jalan
echo   Tanya 3 path: INPUT, RETURN, UDA (Done -^> Uda)
echo ============================================================
echo.
if "%~1"=="--once" (
  python route.py --once --hub
  echo.
  pause
  exit /b
)
echo  [Watcher] bangun saat ada file masuk (event, bukan polling)
echo  [Alur] COPY -^> hot folder (nama asli), asli -^> UDA
echo  [Return] file tak cocok nama -^> folder RETURN
echo.
python route.py --hub
echo.
echo --- router berhenti ---
pause
