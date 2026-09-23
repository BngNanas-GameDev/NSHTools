"""Backup mingguan nsh.db — snapshot konsisten via VACUUM INTO (aman saat DB dipakai).

Dipakai scheduler mingguan (backup_register.bat) atau manual:
  python backup.py
Hasil: Database/backups/nsh_YYYYMMDD_HHMMSS.db  (4 terbaru dipertahankan)
"""
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
BACKUP_DIR = HERE / "backups"
KEEP = 4

sys.path.insert(0, str(HERE))
from db import DB_PATH, get_conn


def main():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"nsh_{stamp}.db"
    with get_conn() as con:
        con.execute(f"VACUUM INTO '{dest}'")
    # prune: simpan KEEP terbaru
    files = sorted(BACKUP_DIR.glob("nsh_*.db"), key=lambda p: p.name)
    for old in files[:-KEEP] if len(files) > KEEP else []:
        try:
            old.unlink()
        except OSError:
            pass
    kept = sorted(BACKUP_DIR.glob("nsh_*.db"), key=lambda p: p.name)
    print(f"backup OK: {dest.name} ({dest.stat().st_size // 1024} KB, simpan {len(kept)})")


if __name__ == "__main__":
    main()
