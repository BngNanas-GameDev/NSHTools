"""NSH Database — pusat database semua tool (SQLite, WAL).

Satu file: NSH Tools/Database/nsh.db  (backup = copy file ini).
Dipakai: WMA, KMMA, KMA (route.py), NSH Lite — tanpa dependensi baru (sqlite3 stdlib).

Tabel:
  kmma_state  nota PK | status (completed/skip_forever/skipped) | reason | updated_at
  wma_entries nota+bahan PK | date | total_qty | jenis | process_type | operator | machine | files(JSON)
  config      modul PK  | data(JSON)   <- wma.json / route.json / lite.json
  validasi    pola PK   | count
  events      append-only log telemetri (migrasi stats.jsonl)
  kma_stats   date PK   | ok | ret | done            <- angka KMA per hari
  kma_events  id AI     | date | file | category | dest | status | msg | ts
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
  updated_at TEXT DEFAULT '',
  retries INTEGER DEFAULT 0
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
  customer TEXT DEFAULT '',
  verified INTEGER DEFAULT 0,
  sides TEXT DEFAULT '',
  laminating TEXT DEFAULT '',
  PRIMARY KEY (nota, bahan, sides, laminating)
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
CREATE TABLE IF NOT EXISTS kma_stats (
  date TEXT PRIMARY KEY,
  ok INTEGER DEFAULT 0,
  ret INTEGER DEFAULT 0,
  done INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kma_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT DEFAULT '',
  file TEXT DEFAULT '',
  category TEXT DEFAULT '',
  dest TEXT DEFAULT '',
  status TEXT DEFAULT '',
  msg TEXT DEFAULT '',
  ts TEXT DEFAULT '',
  machine TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_kma_events_date ON kma_events(date, id);
CREATE TABLE IF NOT EXISTS wma_seen (
  nota TEXT NOT NULL,
  bahan TEXT NOT NULL DEFAULT '',
  date TEXT DEFAULT '',
  customer TEXT DEFAULT '',
  total_qty INTEGER DEFAULT 0,
  jenis TEXT DEFAULT '',
  process_type TEXT,
  operator TEXT DEFAULT '',
  machine TEXT DEFAULT '',
  files TEXT DEFAULT '[]',
  verified INTEGER DEFAULT 0,
  sides TEXT DEFAULT '',
  laminating TEXT DEFAULT '',
  PRIMARY KEY (nota, bahan, sides, laminating, date)
);
CREATE TABLE IF NOT EXISTS wma_verified (
  nota TEXT PRIMARY KEY,
  ts TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS output_moves (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT DEFAULT '',
  file TEXT DEFAULT '',
  dest TEXT DEFAULT '',
  machine TEXT DEFAULT '',
  operator TEXT DEFAULT ''
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
    # migrasi ringan DB lama: tambah kolom machine bila belum ada
    try:
        con = get_conn(path)
        try:
            con.execute("ALTER TABLE kma_events ADD COLUMN machine TEXT DEFAULT ''")
            con.commit()
        except Exception:
            pass
        try:
            con.execute("ALTER TABLE wma_entries ADD COLUMN customer TEXT DEFAULT ''")
            con.commit()
        except Exception:
            pass
        try:
            con.execute("ALTER TABLE wma_entries ADD COLUMN verified INTEGER DEFAULT 0")
            con.commit()
        except Exception:
            pass
        try:
            con.execute("ALTER TABLE wma_seen ADD COLUMN verified INTEGER DEFAULT 0")
            con.commit()
        except Exception:
            pass
        try:
            con.execute("ALTER TABLE wma_seen ADD COLUMN customer TEXT DEFAULT ''")
            con.commit()
        except Exception:
            pass
        try:
            con.execute("ALTER TABLE kmma_state ADD COLUMN retries INTEGER DEFAULT 0")
            con.commit()
        except Exception:
            pass
        _migrate_wma_pk(con)
        con.close()
    except Exception:
        pass
    return str(path or DB_PATH)


def _table_cols(con, tbl):
    try:
        return [r[1] for r in con.execute(f"PRAGMA table_info({tbl})")]
    except Exception:
        return []


def _migrate_wma_pk(con):
    """PK lama (nota,bahan[+date]) -> (nota,bahan,sides,laminating[+date]).
    Rebuild tabel bila kolom sides belum ada; data lama dipertahankan."""
    for tbl, pk_tail in (("wma_entries", "sides, laminating"),
                         ("wma_seen", "sides, laminating, date")):
        try:
            cols = _table_cols(con, tbl)
            if not cols or "sides" in cols:
                continue
            con.execute(f"""CREATE TABLE IF NOT EXISTS {tbl}_new (
              nota TEXT NOT NULL, bahan TEXT NOT NULL DEFAULT '', date TEXT DEFAULT '',
              total_qty INTEGER DEFAULT 0, jenis TEXT DEFAULT '', process_type TEXT,
              operator TEXT DEFAULT '', machine TEXT DEFAULT '', files TEXT DEFAULT '[]',
              customer TEXT DEFAULT '', verified INTEGER DEFAULT 0,
              sides TEXT DEFAULT '', laminating TEXT DEFAULT '',
              PRIMARY KEY (nota, bahan, {pk_tail}))""")
            con.execute(f"""INSERT OR IGNORE INTO {tbl}_new
              (nota,bahan,date,total_qty,jenis,process_type,operator,machine,files,
               customer,verified,sides,laminating)
              SELECT nota,bahan,date,total_qty,jenis,process_type,operator,machine,files,
               COALESCE(customer,''), COALESCE(verified,0), '','' FROM {tbl}""")
            con.execute(f"DROP TABLE {tbl}")
            con.execute(f"ALTER TABLE {tbl}_new RENAME TO {tbl}")
            con.commit()
        except Exception:
            try:
                con.rollback()
            except Exception:
                pass


# ── KMMA state ────────────────────────────────────────────────
def kmma_get(nota, con=None):
    close = con is None
    con = con or get_conn()
    try:
        r = con.execute("SELECT status, reason, updated_at, retries FROM kmma_state WHERE nota=?",
                        (nota,)).fetchone()
        return dict(r) if r else None
    finally:
        if close:
            con.close()


def kmma_set(nota, status, reason="", updated_at="", retries=None, con=None):
    close = con is None
    con = con or get_conn()
    try:
        if retries is None:
            con.execute(
                "INSERT INTO kmma_state(nota,status,reason,updated_at) VALUES(?,?,?,?)"
                " ON CONFLICT(nota) DO UPDATE SET status=excluded.status,"
                " reason=excluded.reason, updated_at=excluded.updated_at",
                (nota, status, reason, updated_at))
        else:
            con.execute(
                "INSERT INTO kmma_state(nota,status,reason,updated_at,retries) VALUES(?,?,?,?,?)"
                " ON CONFLICT(nota) DO UPDATE SET status=excluded.status,"
                " reason=excluded.reason, updated_at=excluded.updated_at, retries=excluded.retries",
                (nota, status, reason, updated_at, int(retries)))
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
            "operator,machine,files,customer,verified,sides,laminating)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(e.get("nota"), e.get("bahan") or "", date, e.get("total_qty") or 0,
              e.get("jenis") or "", e.get("process_type"),
              e.get("operator") or "", e.get("machine") or "",
              json.dumps(e.get("files") or [], ensure_ascii=False),
              e.get("customer") or "", 1 if e.get("verified") else 0,
              e.get("sides") or "", e.get("laminating") or "")
             for e in entries])
        con.commit()
    finally:
        if close:
            con.close()


def wma_all(date, con=None):
    close = con is None
    con = con or get_conn()
    try:
        return [{**dict(r), "files": json.loads(r["files"] or "[]"),
                 "verified": bool(r["verified"])}
                for r in con.execute(
                    "SELECT nota,bahan,total_qty,jenis,process_type,operator,machine,files,customer,"
                    "verified,sides,laminating"
                    " FROM wma_entries WHERE date=? ORDER BY nota,bahan,sides,laminating", (date,))]
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


# ── KMA harian (restart tak nol; besok hari baru) ──────────────
def kma_stats_get(date, con=None):
    close = con is None
    con = con or get_conn()
    try:
        r = con.execute("SELECT ok,ret,done FROM kma_stats WHERE date=?", (date,)).fetchone()
        return {"ok": r["ok"], "ret": r["ret"], "done": r["done"]} if r else {"ok": 0, "ret": 0, "done": 0}
    finally:
        if close:
            con.close()


def kma_stats_set(date, ok, ret, done, con=None):
    close = con is None
    con = con or get_conn()
    try:
        con.execute(
            "INSERT INTO kma_stats(date,ok,ret,done) VALUES(?,?,?,?)"
            " ON CONFLICT(date) DO UPDATE SET ok=excluded.ok,ret=excluded.ret,done=excluded.done",
            (date, ok, ret, done))
        con.commit()
    finally:
        if close:
            con.close()


def kma_event_add(date, file, category, dest, status, msg, ts, machine="", con=None):
    close = con is None
    con = con or get_conn()
    try:
        con.execute(
            "INSERT INTO kma_events(date,file,category,dest,status,msg,ts,machine)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (date, file, category or "", dest or "", status or "", msg or "", ts or "",
             machine or ""))
        con.commit()
    finally:
        if close:
            con.close()


def kma_events_day(date, limit=200, con=None):
    """Events hari tsb, tertua -> terbaru (maks `limit`)."""
    close = con is None
    con = con or get_conn()
    try:
        return [dict(r) for r in con.execute(
            "SELECT file,category,dest,status,msg,ts,machine FROM kma_events"
            " WHERE date=? ORDER BY id LIMIT ?", (date, limit))]
    finally:
        if close:
            con.close()


def kma_prune(keep_days=30, con=None):
    """Hapus hari lebih tua dari `keep_days` (jaga ukuran DB)."""
    close = con is None
    con = con or get_conn()
    try:
        con.execute("DELETE FROM kma_events WHERE date < date('now','localtime',?)",
                    (f"-{int(keep_days)} days",))
        con.execute("DELETE FROM kma_stats WHERE date < date('now','localtime',?)",
                    (f"-{int(keep_days)} days",))
        con.commit()
    finally:
        if close:
            con.close()


# ── WMA: semua entri terlihat + verifikasi ─────────────────────
def wma_seen_upsert(date, entries, con=None):
    """Simpan semua entri yang pernah terlihat hari ini (tak dihapus KMMA).
    Per nota di-refresh (buang varian basi, mis. bahan terkoreksi saat re-parse),
    kecuali baris final (completed/skip_forever) yang dipertahankan."""
    close = con is None
    con = con or get_conn()
    try:
        notes = {e.get("nota") for e in entries if e.get("nota")}
        keep = []
        if notes:
            q = ",".join("?" * len(notes))
            final_notas = {r[0] for r in con.execute(
                "SELECT nota FROM kmma_state WHERE status IN ('completed','skip_forever')"
                f" AND nota IN ({q})", tuple(notes))}
            if final_notas:
                q2 = ",".join("?" * len(final_notas))
                keep = [dict(r) for r in con.execute(
                    "SELECT nota,bahan,customer,total_qty,jenis,process_type,operator,"
                    "machine,files,verified,sides,laminating FROM wma_seen"
                    f" WHERE date=? AND nota IN ({q2})", (date, *final_notas))]
            con.execute(f"DELETE FROM wma_seen WHERE date=? AND nota IN ({q})",
                        (date, *notes))
        con.executemany(
            "INSERT INTO wma_seen(nota,bahan,date,customer,total_qty,jenis,process_type,"
            "operator,machine,files,verified,sides,laminating) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(nota,bahan,sides,laminating,date) DO UPDATE SET customer=excluded.customer,"
            "total_qty=excluded.total_qty,jenis=excluded.jenis,process_type=excluded.process_type,"
            "operator=excluded.operator,machine=excluded.machine,files=excluded.files,"
            "verified=excluded.verified",
            [(e.get("nota"), e.get("bahan") or "", date, e.get("customer") or "",
              e.get("total_qty") or 0, e.get("jenis") or "", e.get("process_type"),
              e.get("operator") or "", e.get("machine") or "",
              json.dumps(e.get("files") or [], ensure_ascii=False),
              1 if e.get("verified") else 0,
              e.get("sides") or "", e.get("laminating") or "")
             for e in entries]
            + [(k["nota"], k["bahan"] or "", date, k.get("customer") or "",
                k.get("total_qty") or 0, k.get("jenis") or "", k.get("process_type"),
                k.get("operator") or "", k.get("machine") or "",
                k["files"] if isinstance(k.get("files"), str) else json.dumps(k.get("files") or [], ensure_ascii=False),
                1 if k.get("verified") else 0,
                k.get("sides") or "", k.get("laminating") or "")
               for k in keep])
        con.commit()
    finally:
        if close:
            con.close()


def wma_seen_day(date, con=None):
    close = con is None
    con = con or get_conn()
    try:
        return [{**dict(r), "files": json.loads(r["files"] or "[]"),
                 "verified": bool(r["verified"])}
                for r in con.execute(
                    "SELECT nota,bahan,customer,total_qty,jenis,process_type,operator,"
                    "machine,files,verified,sides,laminating FROM wma_seen"
                    " WHERE date=? ORDER BY nota,bahan,sides,laminating", (date,))]
    finally:
        if close:
            con.close()


def wma_verify(nota, ts="", con=None):
    close = con is None
    con = con or get_conn()
    try:
        con.execute("INSERT INTO wma_verified(nota,ts) VALUES(?,?)"
                    " ON CONFLICT(nota) DO UPDATE SET ts=excluded.ts", (nota, ts))
        con.commit()
    finally:
        if close:
            con.close()


def wma_verified_all(con=None):
    close = con is None
    con = con or get_conn()
    try:
        return [r[0] for r in con.execute("SELECT nota FROM wma_verified")]
    finally:
        if close:
            con.close()


# ── Output moves (jejak kirim DEVELOP/RICOH) ───────────────────
def output_move_add(ts, file, dest, machine, operator, con=None):
    close = con is None
    con = con or get_conn()
    try:
        con.execute(
            "INSERT INTO output_moves(ts,file,dest,machine,operator)"
            " VALUES(?,?,?,?,?)", (ts, file, dest, machine, operator))
        con.commit()
    finally:
        if close:
            con.close()
