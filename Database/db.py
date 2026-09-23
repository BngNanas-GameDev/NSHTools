"""NSH Database — pusat database semua tool (SQLite, WAL).

Satu file: NSH Tools/Database/nsh.db  (backup = copy file ini).
Dipakai: WMA, KMMA, KMA (route.py), NSH Lite — tanpa dependensi baru (sqlite3 stdlib).

Tabel:
  kmma_state  nota PK | status (completed/skip_forever/skipped) | reason | updated_at
  wma_entries nota+bahan PK | date | total_qty | jenis | process_type | operator | machine | files(JSON)
  config      modul PK  | data(JSON)   <- wma.json / route.json / lite.json
  validasi    pola PK   | count
  events      append-only log telemetri (migrasi stats.jsonl)
"""
import json
import sqlite3
from pathlib import Path

BASE = Path(__file__).parent
DB_PATH = BASE / "nsh.db"

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=30000;
CREATE TABLE IF NOT EXISTS kmma_state (
  nota TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  reason TEXT DEFAULT '',
  updated_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS wma_entries (
  nota TEXT NOT NULL,
  bahan TEXT NOT NULL DEFAULT '',
  date TEXT DEFAULT '',
  total_qty INTEGER DEFAULT 0,
  jenis TEXT DEFAULT '',
  process_type TEXT,
  operator TEXT DEFAULT '',
  machine TEXT DEFAULT '',
  files TEXT DEFAULT '[]',
  PRIMARY KEY (nota, bahan)
);
CREATE TABLE IF NOT EXISTS config (
  modul TEXT PRIMARY KEY,
  data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS validasi (
  pola TEXT PRIMARY KEY,
  count INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL DEFAULT 0,
  type TEXT DEFAULT '',
  detail TEXT DEFAULT ''
);
"""


def get_conn(path=None):
    """Koneksi WAL + busy timeout 30 dtk (aman tulis bersamaan)."""
    con = sqlite3.connect(str(path or DB_PATH), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    con.execute("PRAGMA busy_timeout=30000;")
    return con


def init_db(path=None):
    with get_conn(path) as con:
        con.executescript(SCHEMA)
    return str(path or DB_PATH)


# ── KMMA state ────────────────────────────────────────────────
def kmma_get(nota, con=None):
    close = con is None
    con = con or get_conn()
    try:
        r = con.execute("SELECT status, reason, updated_at FROM kmma_state WHERE nota=?",
                        (nota,)).fetchone()
        return dict(r) if r else None
    finally:
        if close:
            con.close()


def kmma_set(nota, status, reason="", updated_at="", con=None):
    close = con is None
    con = con or get_conn()
    try:
        con.execute(
            "INSERT INTO kmma_state(nota,status,reason,updated_at) VALUES(?,?,?,?)"
            " ON CONFLICT(nota) DO UPDATE SET status=excluded.status,"
            " reason=excluded.reason, updated_at=excluded.updated_at",
            (nota, status, reason, updated_at))
        con.commit()
    finally:
        if close:
            con.close()


def kmma_by_status(status, con=None):
    """{nota: {reason, updated_at}} — pengganti completed/skip_forever dict."""
    close = con is None
    con = con or get_conn()
    try:
        return {r["nota"]: {"reason": r["reason"], "updated_at": r["updated_at"]}
                for r in con.execute(
                    "SELECT nota, reason, updated_at FROM kmma_state WHERE status=?",
                    (status,))}
    finally:
        if close:
            con.close()


def kmma_delete(nota, con=None):
    close = con is None
    con = con or get_conn()
    try:
        con.execute("DELETE FROM kmma_state WHERE nota=?", (nota,))
        con.commit()
    finally:
        if close:
            con.close()


# ── WMA entries ───────────────────────────────────────────────
def wma_replace(date, entries, con=None):
    """Ganti entries tanggal tsb (sekali sync = satu transaksi)."""
    close = con is None
    con = con or get_conn()
    try:
        con.execute("DELETE FROM wma_entries WHERE date=?", (date,))
        con.executemany(
            "INSERT INTO wma_entries(nota,bahan,date,total_qty,jenis,process_type,"
            "operator,machine,files) VALUES(?,?,?,?,?,?,?,?,?)",
            [(e.get("nota"), e.get("bahan") or "", date, e.get("total_qty") or 0,
              e.get("jenis") or "", e.get("process_type"),
              e.get("operator") or "", e.get("machine") or "",
              json.dumps(e.get("files") or [], ensure_ascii=False))
             for e in entries])
        con.commit()
    finally:
        if close:
            con.close()


def wma_all(date, con=None):
    close = con is None
    con = con or get_conn()
    try:
        return [{**dict(r), "files": json.loads(r["files"] or "[]")}
                for r in con.execute(
                    "SELECT nota,bahan,total_qty,jenis,process_type,operator,machine,files"
                    " FROM wma_entries WHERE date=? ORDER BY nota,bahan", (date,))]
    finally:
        if close:
            con.close()


# ── config per modul ──────────────────────────────────────────
def config_get(modul, con=None):
    close = con is None
    con = con or get_conn()
    try:
        r = con.execute("SELECT data FROM config WHERE modul=?", (modul,)).fetchone()
        return json.loads(r["data"]) if r else {}
    finally:
        if close:
            con.close()


def config_set(modul, data, con=None):
    close = con is None
    con = con or get_conn()
    try:
        con.execute(
            "INSERT INTO config(modul,data) VALUES(?,?)"
            " ON CONFLICT(modul) DO UPDATE SET data=excluded.data",
            (modul, json.dumps(data, ensure_ascii=False)))
        con.commit()
    finally:
        if close:
            con.close()


# ── validasi + events ─────────────────────────────────────────
def validasi_bump(pola, n=1, con=None):
    close = con is None
    con = con or get_conn()
    try:
        con.execute(
            "INSERT INTO validasi(pola,count) VALUES(?,?)"
            " ON CONFLICT(pola) DO UPDATE SET count=count+excluded.count",
            (pola, n))
        con.commit()
    finally:
        if close:
            con.close()


def validasi_all(con=None):
    close = con is None
    con = con or get_conn()
    try:
        return {r["pola"]: r["count"] for r in con.execute("SELECT pola,count FROM validasi")}
    finally:
        if close:
            con.close()


def event_log(ts, type, detail="", con=None):
    close = con is None
    con = con or get_conn()
    try:
        con.execute("INSERT INTO events(ts,type,detail) VALUES(?,?,?)", (ts, type, detail))
        con.commit()
    finally:
        if close:
            con.close()
