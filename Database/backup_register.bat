@echo off
REM Daftarkan backup otomatis mingguan (Minggu 23:00) via Task Scheduler.
REM Jalankan sekali sebagai Administrator bila ditolak. Untuk hapus: schtasks /delete /tn "NSH DB Backup" /f
chcp 65001 >nul 2>&1
cd /d "%~dp0"
schtasks /create /tn "NSH DB Backup" /tr "pythonw \"%~dp0backup.py\"" /sc WEEKLY /d SUN /st 23:00 /f
if errorlevel 1 (
  echo GAGAL mendaftar. Klik kanan file ini -^> Run as administrator.
) else (
  echo Terdaftar: backup tiap Minggu 23:00 -^> Database\backups\ (4 terbaru)
)
pause
