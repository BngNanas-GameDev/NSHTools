"""Migrasi sekali jalan: JSON lama -> nsh.db. Aman diulang (upsert semua).

  python Database/migrate.py        (dari NSH Tools/)
  python migrate.py                 (dari NSH Tools/Database/)
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
TOOLS = HERE.parent
LEGACY = TOOLS.parent / "Nanas Second Hand"

sys.path.insert(0, str(HERE))
from db import (init_db, get_conn, kmma_set, wma_replace, config_set,
                validasi_bump, event_log)


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _row_variants(nota):
    """Varian baris (bahan,sides,laminating,test,process_type) nota tsb dari wma_seen.
    Fallback [kosong] bila tak ada (perilaku lama)."""
    try:
        with get_conn() as con:
            rows = [tuple(r) for r in con.execute(
                "SELECT DISTINCT bahan,sides,laminating,test,process_type FROM wma_seen WHERE nota=?",
                (nota,))]
        return rows or [("", "", "", 0, "")]
    except Exception:
        return [("", "", "", 0, "")]


def main():
    init_db()
    counts = {}

    # KMMA: completed / skip_forever / skipped_nota -> kmma_state
    # (kunci legacy per-nota direplikasi per varian baris agar tak salah blokir)
    n = 0
    kmma = read_json(TOOLS / "KMMA" / "kmma.json") or {}
    for nota, t in (kmma.get("completed") or {}).items():
        for (b, s, l, tst, p) in _row_variants(nota):
            kmma_set(nota, "completed", "", t if isinstance(t, str) else "",
                     bahan=b or "", sides=s or "", laminating=l or "", test=bool(tst),
                     process_type=p or "")
            n += 1
    for nota, reason in (kmma.get("skip_forever") or {}).items():
        for (b, s, l, tst, p) in _row_variants(nota):
            kmma_set(nota, "skip_forever", str(reason),
                     bahan=b or "", sides=s or "", laminating=l or "", test=bool(tst),
                     process_type=p or "")
            n += 1
    for nota, v in (kmma.get("skipped_nota") or {}).items():
        v = v or {}
        for (b, s, l, tst, p) in _row_variants(nota):
            kmma_set(nota, "skipped", str(v.get("reason", "")),
                     str(v.get("last_try", "")), bahan=b or "", sides=s or "",
                     laminating=l or "", test=bool(tst), process_type=p or "")
            n += 1
    counts["kmma_state"] = n

    # WMA: wma_daily.json -> wma_entries ; wma.json -> config
    daily = read_json(TOOLS / "WMA" / "wma_daily.json") or {}
    entries = daily.get("entries") or []
    if daily.get("date") or entries:
        wma_replace(daily.get("date", ""), entries)
    counts["wma_entries"] = len(entries)
    wma_cfg = read_json(TOOLS / "WMA" / "wma.json")
    if wma_cfg:
        config_set("wma", wma_cfg)

    # KMA + Lite configs
    for modul, path in (("kma", TOOLS / "KMA" / "route.json"),
                        ("lite", TOOLS / "NSH Lite" / "lite.json")):
        cfg = read_json(path)
        if cfg:
            config_set(modul, cfg)
            counts[f"config:{modul}"] = len(cfg)

    # validasi.json -> validasi ; stats.jsonl -> events
    val = read_json(LEGACY / "data" / "validasi.json") or {}
    for pola, c in val.items():
        try:
            validasi_bump(pola, int(c))
        except (ValueError, TypeError):
            pass
    counts["validasi"] = len(val)
    n_ev = 0
    try:
        with open(LEGACY / "data" / "stats.jsonl", encoding="utf-8") as f:
            with get_conn() as con:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    event_log(e.get("ts", 0), str(e.get("type", "")),
                              str(e.get("detail", "")), con)
                    n_ev += 1
    except FileNotFoundError:
        pass
    counts["events"] = n_ev

    print("migrasi OK:", json.dumps(counts, ensure_ascii=False))


if __name__ == "__main__":
    main()
