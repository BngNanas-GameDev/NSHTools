"""WE (Eye of Wisdom) — hitung path harian L: tanpa efek samping.

Modul murni (tanpa I/O saat import, stdlib saja): dipakai untuk menemukan
folder hari ini di drive L: dengan mode WAIT bila folder hari belum ada
(tidak membuat folder tanggal otomatis; override manual urusan Phase 2).

Struktur yang diharapkan di bawah folder tahun (mis. ``L:\\2026``)::

    Bulan 09 September 2026/
        28 September 2026 (Senin)/
            AaDevelop/            <- WE monitoring (casing asli disk)
                Impose/           <- Lite OUTPUT + KMA RETURN
                    DEVELOP/      <- KMA INPUT
                    Uda/          <- KMA UDA
                    RICOH/        <- KMA RICOH
                Uda/              <- Lite UDA
"""

import os
import re
from datetime import date
from pathlib import Path

#: Nama bulan Indonesia, indeks 0 = Januari (month-1).
BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni",
         "Juli", "Agustus", "September", "Oktober", "November", "Desember"]

#: Nama hari Indonesia, Senin dulu agar cocok `date.weekday()` (0 = Senin).
HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]

#: Pola folder harian, toleran spasi sebelum '(' hilang: `17 September 2026(Kamis)`.
_DAY_RE = re.compile(r"^(\d{2}) (\w+) (\d{4})\s*\((\w+)\)$", re.IGNORECASE)


def day_labels(today: date) -> dict:
    """Label kanonis bulan+harian untuk tanggal tsb (Indonesia, nol di depan).

    Return ``{"month": "Bulan 09 September 2026", "day": "28 September 2026 (Senin)"}``.
    Input buruk -> dict kosong (tidak raise).
    """
    try:
        if not isinstance(today, date):
            return {}
        m = today.month
        y = today.year
        d = today.day
        nama = BULAN[m - 1]
        dw = HARI[today.weekday()]
        return {
            "month": f"Bulan {m:02d} {nama} {y}",
            "day": f"{d:02d} {nama} {y} ({dw})",
        }
    except Exception:
        return {}


def resolve_aadevelop(day_root: Path) -> Path | None:
    """Cari folder AaDevelop (case-insensitive) satu level di bawah `day_root`.

    Pakai `os.scandir` sekali saja; bandingkan `entry.name.lower() == "aadevelop"`.
    Return path ASLI di disk (casing asli) atau None bila tak ada/error.
    Tidak pernah raise (OSError -> None).
    """
    try:
        if day_root is None:
            return None
        with os.scandir(day_root) as it:
            for entry in it:
                try:
                    if entry.name.lower() == "aadevelop":
                        return Path(entry.path)
                except Exception:
                    continue
        return None
    except Exception:
        return None


def match_day_folder(name: str) -> dict | None:
    """Parse nama folder harian yang sudah ada -> `{d, month_name, y, weekday}`.

    Toleran: spasi sebelum '(' boleh hilang, case-insensitive.
    Contoh: `"28 September 2026 (Senin)"`, `"17 September 2026(Kamis)"`.
    Input buruk/tak cocok -> None (tidak raise).
    """
    try:
        if not isinstance(name, str):
            return None
        m = _DAY_RE.match(name.strip())
        if not m:
            return None
        return {"d": m.group(1), "month_name": m.group(2),
                "y": m.group(3), "weekday": m.group(4)}
    except Exception:
        return None


def daily_paths(root_L: str | Path, today: date) -> dict | None:
    """Susun path harian WE/Lite/KMA untuk tanggal tsb (objek `Path`).

    `root_L` adalah folder tahun (mis. `L:\\2026`), sehingga
    `day_root = root_L / month / day`. Satu-satunya probe disk adalah
    `resolve_aadevelop(day_root)`; bila folder hari ATAU AaDevelop hilang
    -> return None (sinyal mode WAIT, tanpa membuat folder).

    Return::

        {"monitoring": AaDevelop,
         "lite": {"input": Aa, "output": Aa/Impose, "uda": Aa/Uda},
         "kma": {"input": Impose/DEVELOP, "return": Impose,
                 "uda": Impose/Uda, "ricoh": Impose/RICOH}}

    Input buruk -> None (tidak raise).
    """
    try:
        if root_L is None:
            return None
        if isinstance(root_L, str) and not root_L.strip():
            return None
        if not isinstance(today, date):
            return None
        labels = day_labels(today)
        if not labels:
            return None
        day_root = Path(root_L) / labels["month"] / labels["day"]
        aa = resolve_aadevelop(day_root)
        if aa is None:
            return None
        impose = aa / "Impose"
        return {
            "monitoring": aa,
            "lite": {"input": aa, "output": impose, "uda": aa / "Uda"},
            "kma": {"input": impose / "DEVELOP", "return": impose,
                    "uda": impose / "Uda", "ricoh": impose / "RICOH"},
        }
    except Exception:
        return None
