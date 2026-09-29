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
  km_orders   data_id PK | date | nota | bahan_km | qty_km | status_km | flow_json | updated_at
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from pathlib import Path

BASE = Path(__file__).parent
DB_PATH = BASE / "nsh.db"

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=30000;
CREATE TABLE IF NOT EXISTS kmma_state (
  nota TEXT NOT NULL,
  bahan TEXT NOT NULL DEFAULT '',
  sides TEXT DEFAULT '',
  laminating TEXT DEFAULT '',
  test INTEGER DEFAULT 0,
  process_type TEXT DEFAULT '',
  status TEXT NOT NULL,
  reason TEXT DEFAULT '',
  updated_at TEXT DEFAULT '',
  retries INTEGER DEFAULT 0,
  PRIMARY KEY (nota, bahan, sides, laminating, test, process_type)
);
CREATE TABLE IF NOT EXISTS wma_entries (
  nota TEXT NOT NULL,
  bahan TEXT NOT NULL DEFAULT '',
  date TEXT DEFAULT '',
  total_qty INTEGER DEFAULT 0,
  jenis TEXT DEFAULT '',
  process_type TEXT DEFAULT '',
  operator TEXT DEFAULT '',
  machine TEXT DEFAULT '',
  files TEXT DEFAULT '[]',
  customer TEXT DEFAULT '',
  verified INTEGER DEFAULT 0,
  sides TEXT DEFAULT '',
  laminating TEXT DEFAULT '',
  test INTEGER DEFAULT 0,
  PRIMARY KEY (nota, bahan, sides, laminating, test, process_type, date)
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
  process_type TEXT DEFAULT '',
  operator TEXT DEFAULT '',
  machine TEXT DEFAULT '',
  files TEXT DEFAULT '[]',
  verified INTEGER DEFAULT 0,
  sides TEXT DEFAULT '',
  laminating TEXT DEFAULT '',
  test INTEGER DEFAULT 0,
  PRIMARY KEY (nota, bahan, sides, laminating, test, process_type, date)
);
CREATE TABLE IF NOT EXISTS wma_verified (
  nota TEXT NOT NULL,
  bahan TEXT NOT NULL DEFAULT '',
  sides TEXT DEFAULT '',
  laminating TEXT DEFAULT '',
  test INTEGER DEFAULT 0,
  process_type TEXT DEFAULT '',
  ts TEXT DEFAULT '',
  PRIMARY KEY (nota, bahan, sides, laminating, test, process_type)
);
CREATE TABLE IF NOT EXISTS output_moves (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT DEFAULT '',
  file TEXT DEFAULT '',
  dest TEXT DEFAULT '',
  machine TEXT DEFAULT '',
  operator TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS km_orders (
  data_id TEXT PRIMARY KEY,
  date TEXT DEFAULT '',
  nota TEXT DEFAULT '',
  bahan_km TEXT DEFAULT '',
  qty_km INTEGER DEFAULT 0,
  status_km TEXT DEFAULT '',
  flow_json TEXT DEFAULT '[]',
  updated_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_km_orders_date ON km_orders(date, nota);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  pass_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin','user')),
  created_at REAL DEFAULT 0,
  created_by TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  expires_at REAL NOT NULL,
  created_at REAL NOT NULL,
  remember INTEGER DEFAULT 0,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
CREATE TABLE IF NOT EXISTS we_seen (
  filename TEXT NOT NULL, date TEXT NOT NULL DEFAULT '',
  nota TEXT DEFAULT '', bahan TEXT DEFAULT '', sides TEXT DEFAULT '',
  laminating TEXT DEFAULT '', test INTEGER DEFAULT 0, process_type TEXT DEFAULT '',
  customer TEXT DEFAULT '', total_qty INTEGER DEFAULT 0, jenis TEXT DEFAULT '',
  size INTEGER DEFAULT 0, mtime INTEGER DEFAULT 0, first_seen_ts TEXT DEFAULT '',
  PRIMARY KEY (filename, date));
CREATE TABLE IF NOT EXISTS we_printed (
  filename TEXT NOT NULL, date TEXT NOT NULL DEFAULT '',
  nota TEXT DEFAULT '', bahan TEXT DEFAULT '', sides TEXT DEFAULT '',
  laminating TEXT DEFAULT '', test INTEGER DEFAULT 0, process_type TEXT DEFAULT '',
  output_name TEXT DEFAULT '', printed_ts TEXT DEFAULT '',
  operator TEXT DEFAULT '', machine TEXT DEFAULT '',
  PRIMARY KEY (filename, date));
CREATE TABLE IF NOT EXISTS we_verified (
  nota TEXT NOT NULL, bahan TEXT NOT NULL DEFAULT '',
  sides TEXT DEFAULT '', laminating TEXT DEFAULT '', test INTEGER DEFAULT 0,
  process_type TEXT DEFAULT '', ts TEXT DEFAULT '',
  PRIMARY KEY (nota,bahan,sides,laminating,test,process_type));
CREATE INDEX IF NOT EXISTS idx_we_seen_nota ON we_seen(date, nota);
CREATE INDEX IF NOT EXISTS idx_we_printed_nota ON we_printed(date, nota);
"""


def get_conn(path=None):
    """Koneksi WAL + busy timeout 30 dtk (aman tulis bersamaan)."""
    con = sqlite3.connect(str(path or DB_PATH), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    con.execute("PRAGMA busy_timeout=30000;")
    return con


def _seed_admin(con):
    try:
        cnt = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if cnt and int(cnt) > 0:
            return False
        # seed admin/admin123
        uid = str(uuid.uuid4())
        ph = hash_password("admin123")
        con.execute("INSERT INTO users(id,username,pass_hash,role,created_at,created_by) VALUES(?,?,?,?,?,?)",
                    (uid, "admin", ph, "admin", time.time(), "seed"))
        con.commit()
        return True
    except Exception:
        return False


def init_db(path=None):
    with get_conn(path) as con:
        con.executescript(SCHEMA)
        try:
            _seed_admin(con)
        except Exception:
            pass
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
        _migrate_wma_verified_pk(con)
        for _mn in migrate_kmma_nota_state(con):
            print(f"  [migrate] kmma_state multi-baris perlu review: {_mn}")
        con.close()
    except Exception:
        pass
    return str(path or DB_PATH)


def _migrate_wma_verified_pk(con):
    """wma_verified PK nota-only -> (nota,bahan,sides,laminating,test,process_type).
    Rebuild bila PK belum 6-tuple; data lama dipertahankan (nota saja -> baris kosong)."""
    try:
        cols = _table_cols(con, "wma_verified")
        if not cols:
            return
        pk_now = [r[1] for r in con.execute("PRAGMA table_info(wma_verified)").fetchall() if r[5]]
        if pk_now == ["nota", "bahan", "sides", "laminating", "test", "process_type"]:
            return
        # tambah kolom hilang agar SELECT COALESCE aman sebelum rebuild
        for _c, _t in (("bahan", "TEXT NOT NULL DEFAULT ''"),
                       ("sides", "TEXT DEFAULT ''"),
                       ("laminating", "TEXT DEFAULT ''"),
                       ("test", "INTEGER DEFAULT 0"),
                       ("process_type", "TEXT DEFAULT ''"),
                       ("ts", "TEXT DEFAULT ''")):
            if _c not in cols:
                try:
                    con.execute(f"ALTER TABLE wma_verified ADD COLUMN {_c} {_t}")
                    con.commit()
                    cols.append(_c)
                except Exception:
                    pass
        pk_now = [r[1] for r in con.execute("PRAGMA table_info(wma_verified)").fetchall() if r[5]]
        if pk_now == ["nota", "bahan", "sides", "laminating", "test", "process_type"]:
            return
        con.execute("""CREATE TABLE IF NOT EXISTS wma_verified_new (
          nota TEXT NOT NULL, bahan TEXT NOT NULL DEFAULT '',
          sides TEXT DEFAULT '', laminating TEXT DEFAULT '', test INTEGER DEFAULT 0,
          process_type TEXT DEFAULT '', ts TEXT DEFAULT '',
          PRIMARY KEY (nota,bahan,sides,laminating,test,process_type))""")
        # copy — dukung skema lama (nota,ts) maupun setengah-migrasi
        try:
            has_bahan = "bahan" in cols
            if has_bahan:
                con.execute("""INSERT OR IGNORE INTO wma_verified_new(nota,bahan,sides,laminating,test,process_type,ts)
                               SELECT nota, COALESCE(bahan,''), COALESCE(sides,''), COALESCE(laminating,''), COALESCE(test,0), COALESCE(process_type,''), COALESCE(ts,'') FROM wma_verified""")
            else:
                con.execute("""INSERT OR IGNORE INTO wma_verified_new(nota,bahan,sides,laminating,test,process_type,ts)
                               SELECT nota, '','','',0,'', COALESCE(ts,'') FROM wma_verified""")
        except Exception:
            pass
        con.execute("DROP TABLE wma_verified")
        con.execute("ALTER TABLE wma_verified_new RENAME TO wma_verified")
        con.commit()
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass


def _table_cols(con, tbl):
    try:
        return [r[1] for r in con.execute(f"PRAGMA table_info({tbl})")]
    except Exception:
        return []


def _migrate_wma_pk(con):
    """PK lama -> (nota,bahan,sides,laminating,test,process_type[+date]).
    Rebuild tabel bila PK belum cocok; data lama dipertahankan."""
    for tbl, pk_tail in (("wma_entries", "sides, laminating, test, process_type"),
                         ("wma_seen", "sides, laminating, test, process_type, date")):
        try:
            cols = _table_cols(con, tbl)
            if not cols:
                continue
            for _c, _t in (("customer", "TEXT DEFAULT ''"),
                           ("verified", "INTEGER DEFAULT 0"),
                           ("sides", "TEXT DEFAULT ''"),
                           ("laminating", "TEXT DEFAULT ''"),
                           ("test", "INTEGER DEFAULT 0")):
                if _c not in cols:
                    try:
                        con.execute(f"ALTER TABLE {tbl} ADD COLUMN {_c} {_t}")
                        con.commit()
                    except Exception:
                        pass
            pk_now = [r[1] for r in con.execute(f"PRAGMA table_info({tbl})").fetchall() if r[5]]
            if pk_now == ["nota", "bahan"] + [c.strip() for c in pk_tail.split(",")]:
                continue
            con.execute(f"""CREATE TABLE IF NOT EXISTS {tbl}_new (
              nota TEXT NOT NULL, bahan TEXT NOT NULL DEFAULT '', date TEXT DEFAULT '',
              total_qty INTEGER DEFAULT 0, jenis TEXT DEFAULT '', process_type TEXT DEFAULT '',
              operator TEXT DEFAULT '', machine TEXT DEFAULT '', files TEXT DEFAULT '[]',
              customer TEXT DEFAULT '', verified INTEGER DEFAULT 0,
              sides TEXT DEFAULT '', laminating TEXT DEFAULT '', test INTEGER DEFAULT 0,
              PRIMARY KEY (nota, bahan, {pk_tail}))""")
            con.execute(f"""INSERT OR IGNORE INTO {tbl}_new
              (nota,bahan,date,total_qty,jenis,process_type,operator,machine,files,
               customer,verified,sides,laminating,test)
              SELECT nota,bahan,date,total_qty,jenis,COALESCE(process_type,''),operator,machine,files,
               COALESCE(customer,''), COALESCE(verified,0), COALESCE(sides,''), COALESCE(laminating,''), COALESCE(test,0) FROM {tbl}""")
            con.execute(f"DROP TABLE {tbl}")
            con.execute(f"ALTER TABLE {tbl}_new RENAME TO {tbl}")
            con.commit()
        except Exception:
            try:
                con.rollback()
            except Exception:
                pass


# ── kunci baris (nota+bahan+sides+laminating+test+process_type) ──
def ekey(entry):
    """Key tuple dari entry WMA. Dipakai KMMA/hub/dashboard (via skey)."""
    e = entry or {}
    return (e.get("nota") or "", e.get("bahan") or "", e.get("sides") or "",
            e.get("laminating") or "", 1 if e.get("test") else 0,
            e.get("process_type") or "")


def skey(key):
    """Key string kanonis (JSON/hub/dashboard). Format:
    'nota||bahan||sides||lam||1/0' + '||proc' (proc kosong = digital/lama)."""
    try:
        n, b, s, l, t = key[0], key[1], key[2], key[3], key[4]
        p = key[5] if len(key) > 5 else ""
    except Exception:
        return str(key)
    return "||".join([n or "", b or "", s or "", l or "",
                      "1" if t else "0", p or ""])


# ── KMMA state ────────────────────────────────────────────────
def kmma_get(nota, bahan="", sides="", laminating="", test=False, process_type="", con=None):
    close = con is None
    con = con or get_conn()
    try:
        r = con.execute("SELECT status, reason, updated_at, retries FROM kmma_state"
                        " WHERE nota=? AND bahan=? AND sides=? AND laminating=? AND test=?"
                        " AND COALESCE(process_type,'')=COALESCE(?,'')",
                        (nota, bahan, sides, laminating, 1 if test else 0, process_type or "")).fetchone()
        return dict(r) if r else None
    finally:
        if close:
            con.close()


def kmma_set(nota, status, reason="", updated_at="", retries=None,
             bahan="", sides="", laminating="", test=False, process_type="", con=None):
    close = con is None
    con = con or get_conn()
    t = 1 if test else 0
    p = process_type or ""
    try:
        if retries is None:
            con.execute(
                "INSERT INTO kmma_state(nota,bahan,sides,laminating,test,process_type,status,reason,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(nota,bahan,sides,laminating,test,process_type) DO UPDATE SET status=excluded.status,"
                " reason=excluded.reason, updated_at=excluded.updated_at",
                (nota, bahan, sides, laminating, t, p, status, reason, updated_at))
        else:
            con.execute(
                "INSERT INTO kmma_state(nota,bahan,sides,laminating,test,process_type,status,reason,updated_at,retries)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(nota,bahan,sides,laminating,test,process_type) DO UPDATE SET status=excluded.status,"
                " reason=excluded.reason, updated_at=excluded.updated_at, retries=excluded.retries",
                (nota, bahan, sides, laminating, t, p, status, reason, updated_at, int(retries)))
        con.commit()
    finally:
        if close:
            con.close()


def kmma_by_status(status, con=None):
    """{(nota,bahan,sides,lam,test,proc): {reason, updated_at}}."""
    close = con is None
    con = con or get_conn()
    try:
        return {(r["nota"], r["bahan"] or "", r["sides"] or "",
                 r["laminating"] or "", r["test"] or 0, r["process_type"] or ""):
                {"reason": r["reason"], "updated_at": r["updated_at"]}
                for r in con.execute(
                    "SELECT nota,bahan,sides,laminating,test,process_type,reason,updated_at"
                    " FROM kmma_state WHERE status=?", (status,))}
    finally:
        if close:
            con.close()


def kmma_delete(nota, bahan="", sides="", laminating="", test=False, process_type="", con=None):
    close = con is None
    con = con or get_conn()
    try:
        con.execute("DELETE FROM kmma_state WHERE nota=? AND bahan=? AND sides=?"
                    " AND laminating=? AND test=? AND COALESCE(process_type,'')=COALESCE(?,'')",
                    (nota, bahan, sides, laminating, 1 if test else 0, process_type or ""))
        con.commit()
    finally:
        if close:
            con.close()


def migrate_kmma_nota_state(con=None):
    """Sekali jalan: PK nota -> (nota,bahan,sides,laminating,test,process_type).
    Baris legacy (kunci kosong) direplikasi per varian baris di wma_seen.
    Return daftar nota multi-baris yg direplikasi (perlu review manusia)."""
    close = con is None
    con = con or get_conn()
    multi = []
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(kmma_state)").fetchall()]
        if not cols:
            return []
        pk = [r[1] for r in con.execute("PRAGMA table_info(kmma_state)").fetchall() if r[5]]
        if pk == ["nota", "bahan", "sides", "laminating", "test", "process_type"]:
            return []
        rows = []
        for r in con.execute("SELECT * FROM kmma_state"):
            d = dict(r)
            rows.append({"nota": d.get("nota"), "status": d.get("status"),
                         "reason": d.get("reason") or "",
                         "updated_at": d.get("updated_at") or "",
                         "retries": int(d.get("retries") or 0)})
        con.execute("""CREATE TABLE IF NOT EXISTS kmma_state_new (
          nota TEXT NOT NULL, bahan TEXT NOT NULL DEFAULT '',
          sides TEXT DEFAULT '', laminating TEXT DEFAULT '', test INTEGER DEFAULT 0,
          process_type TEXT DEFAULT '',
          status TEXT NOT NULL, reason TEXT DEFAULT '', updated_at TEXT DEFAULT '',
          retries INTEGER DEFAULT 0,
          PRIMARY KEY (nota,bahan,sides,laminating,test,process_type))""")
        for row in rows:
            n = row["nota"]
            variants = [tuple(v) for v in con.execute(
                "SELECT DISTINCT bahan,sides,laminating,test,process_type FROM wma_seen WHERE nota=?", (n,))]
            if not variants:
                variants = [("", "", "", 0, "")]
            if len(variants) > 1 and row["status"] in ("completed", "skip_forever"):
                multi.append(n)
            for (b, s, l, t, p) in variants:
                con.execute(
                    "INSERT OR IGNORE INTO kmma_state_new(nota,bahan,sides,laminating,test,process_type,"
                    "status,reason,updated_at,retries) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (n, b or "", s or "", l or "", int(t or 0), p or "",
                     row["status"], row["reason"], row["updated_at"], row["retries"]))
        con.execute("DROP TABLE kmma_state")
        con.execute("ALTER TABLE kmma_state_new RENAME TO kmma_state")
        con.commit()
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        return []
    finally:
        if close:
            con.close()
    return sorted(set(multi))


# ── WMA entries ───────────────────────────────────────────────
def wma_replace(date, entries, con=None):
    """Ganti entries tanggal tsb (sekali sync = satu transaksi)."""
    close = con is None
    con = con or get_conn()
    try:
        con.execute("DELETE FROM wma_entries WHERE date=?", (date,))
        con.executemany(
            "INSERT INTO wma_entries(nota,bahan,date,total_qty,jenis,process_type,"
            "operator,machine,files,customer,verified,sides,laminating,test)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(e.get("nota"), e.get("bahan") or "", date, e.get("total_qty") or 0,
              e.get("jenis") or "", e.get("process_type") or "",
              e.get("operator") or "", e.get("machine") or "",
              json.dumps(e.get("files") or [], ensure_ascii=False),
              e.get("customer") or "", 1 if e.get("verified") else 0,
              e.get("sides") or "", e.get("laminating") or "",
              1 if e.get("test") else 0)
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
                 "verified": bool(r["verified"]), "test": bool(r["test"])}
                for r in con.execute(
                    "SELECT nota,bahan,total_qty,jenis,process_type,operator,machine,files,"
                    "customer,verified,sides,laminating,test"
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


def kma_events_day(date, limit=500, con=None):
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
                    "machine,files,verified,sides,laminating,test FROM wma_seen"
                    f" WHERE date=? AND nota IN ({q2})", (date, *final_notas))]
            con.execute(f"DELETE FROM wma_seen WHERE date=? AND nota IN ({q})",
                        (date, *notes))
        con.executemany(
            "INSERT INTO wma_seen(nota,bahan,date,customer,total_qty,jenis,process_type,"
            "operator,machine,files,verified,sides,laminating,test) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(nota,bahan,sides,laminating,test,process_type,date) DO UPDATE SET customer=excluded.customer,"
            "total_qty=excluded.total_qty,jenis=excluded.jenis,process_type=excluded.process_type,"
            "operator=excluded.operator,machine=excluded.machine,files=excluded.files,"
            "verified=excluded.verified",
            [(e.get("nota"), e.get("bahan") or "", date, e.get("customer") or "",
              e.get("total_qty") or 0, e.get("jenis") or "", e.get("process_type") or "",
              e.get("operator") or "", e.get("machine") or "",
              json.dumps(e.get("files") or [], ensure_ascii=False),
              1 if e.get("verified") else 0,
              e.get("sides") or "", e.get("laminating") or "",
              1 if e.get("test") else 0)
             for e in entries]
            + [(k["nota"], k["bahan"] or "", date, k.get("customer") or "",
                k.get("total_qty") or 0, k.get("jenis") or "", k.get("process_type") or "",
                k.get("operator") or "", k.get("machine") or "",
                k["files"] if isinstance(k.get("files"), str) else json.dumps(k.get("files") or [], ensure_ascii=False),
                1 if k.get("verified") else 0,
                k.get("sides") or "", k.get("laminating") or "",
                1 if k.get("test") else 0)
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
                 "verified": bool(r["verified"]), "test": bool(r["test"])}
                for r in con.execute(
                    "SELECT nota,bahan,customer,total_qty,jenis,process_type,operator,"
                    "machine,files,verified,sides,laminating,test FROM wma_seen"
                    " WHERE date=? ORDER BY nota,bahan,sides,laminating", (date,))]
    finally:
        if close:
            con.close()


def wma_verify_row(nota, bahan="", sides="", laminating="", test=False, process_type="", ts="", con=None):
    """Verifikasi per-baris 6-tuple."""
    close = con is None
    con = con or get_conn()
    try:
        con.execute(
            "INSERT INTO wma_verified(nota,bahan,sides,laminating,test,process_type,ts)"
            " VALUES(?,?,?,?,?,?,?) ON CONFLICT(nota,bahan,sides,laminating,test,process_type)"
            " DO UPDATE SET ts=excluded.ts",
            (nota or "", bahan or "", sides or "", laminating or "", 1 if test else 0, process_type or "", ts or ""))
        con.commit()
    finally:
        if close:
            con.close()


def wma_verify(nota, ts="", con=None, bahan="", sides="", laminating="", test=False, process_type=""):
    """Kompatibel lama (nota,ts) + baru per-baris.
    - wma_verify("ON123", "2026-09-25 10:00") -> per-baris kosong
    - wma_verify("ON123", bahan="X", sides="1s", ..., ts="...") -> per-baris penuh
    - wma_verify(("ON123","X","1s","",0,""), ts) -> tuple key
    """
    # tuple/list key
    if isinstance(nota, (tuple, list)):
        key = list(nota) + [""] * 6
        n, b, s, l, t, p = key[0] or "", key[1] or "", key[2] or "", key[3] or "", key[4], key[5] or ""
        actual_ts = ts if isinstance(ts, str) else ""
        return wma_verify_row(n, b, s, l, bool(t) if isinstance(t, int) else bool(t), p, actual_ts, con)
    # heuristik legacy: wma_verify(nota, ts) dimana bahan berisi timestamp
    # jika bahan disalah-artikan sebagai ts karena signature lama positional kedua
    # kita deteksi bila ts kosong tapi bahan berisi ' ' atau ':' dan sisi/test kosong
    if ts == "" and bahan and not sides and not laminating and not test and not process_type:
        # kemungkinan pemanggilan lama wma_verify(nota, ts_value) dipetakan ke bahan
        # jika bahan tampak seperti timestamp, koreksi
        if (":" in bahan or " " in bahan or "-" in bahan) and len(bahan) > 6:
            return wma_verify_row(nota or "", "", "", "", False, "", bahan, con)
    # panggilan baru per-baris via kwargs
    if bahan or sides or laminating or test or process_type:
        return wma_verify_row(nota or "", bahan, sides, laminating, test, process_type, ts, con)
    # legacy simple per-nota -> map ke baris kosong (wildcard legacy)
    return wma_verify_row(nota or "", "", "", "", False, "", ts or "", con)


def wma_verified_all(con=None):
    """Return set skey per-baris: 'nota||bahan||sides||lam||1/0||proc'."""
    close = con is None
    con = con or get_conn()
    try:
        out = set()
        for r in con.execute("SELECT nota,bahan,sides,laminating,test,process_type FROM wma_verified"):
            out.add(skey((r[0], r[1] or "", r[2] or "", r[3] or "", r[4] or 0, r[5] or "")))
        return out
    finally:
        if close:
            con.close()


def wma_verified_all_legacy(con=None):
    """Compat: list nota saja (legacy)."""
    close = con is None
    con = con or get_conn()
    try:
        return [r[0] for r in con.execute("SELECT DISTINCT nota FROM wma_verified")]
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


# ── KM Orders (workflow 1: snapshot harian semua order KM) ──────
def km_orders_replace(date, orders, con=None):
    """Ganti snapshot order KM hari ini. orders = list dict dari KM API
    (wajib punya data_id/nota)."""
    close = con is None
    con = con or get_conn()
    try:
        con.execute("DELETE FROM km_orders WHERE date=?", (date,))
        rows = []
        for o in orders or []:
            rows.append((o.get("data_id") or o.get("nota") or "",
                         date, o.get("nota") or "", o.get("bahan_km") or o.get("bahan") or "",
                         int(o.get("qty_km") or 0), o.get("status_km") or o.get("status") or "",
                         json.dumps(o.get("flow") or o.get("flow_json") or [], ensure_ascii=False),
                         o.get("updated_at") or ""))
        if rows:
            con.executemany(
                "INSERT INTO km_orders(data_id,date,nota,bahan_km,qty_km,status_km,flow_json,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?)", rows)
        con.commit()
    finally:
        if close:
            con.close()


def km_orders_all(date, con=None):
    close = con is None
    con = con or get_conn()
    try:
        return [{**dict(r), "flow": json.loads(r["flow_json"] or "[]")}
                for r in con.execute(
                    "SELECT data_id,date,nota,bahan_km,qty_km,status_km,flow_json,updated_at"
                    " FROM km_orders WHERE date=? ORDER BY nota", (date,))]
    finally:
        if close:
            con.close()


# ── Auth: users + sessions ───────────────────────────────────────
USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,32}$")
PBKDF2_ITER = 260000


def normalize_username(u: str) -> str:
    return (u or "").strip().lower()


def validate_username(u: str) -> str:
    n = normalize_username(u)
    if not USERNAME_RE.match(n):
        raise ValueError("username 3-32 char [a-z0-9._-] (lowercase)")
    return n


def validate_password(p: str) -> str:
    if not isinstance(p, str) or len(p) < 6:
        raise ValueError("password min 6 karakter")
    if len(p) > 128:
        raise ValueError("password max 128 karakter")
    return p


def hash_password(pwd: str) -> str:
    try:
        import bcrypt  # type: ignore
        return bcrypt.hashpw(pwd.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    except Exception:
        salt = secrets.token_bytes(16)
        dk = hashlib.pbkdf2_hmac("sha256", pwd.encode("utf-8"), salt, PBKDF2_ITER)
        return f"pbkdf2${PBKDF2_ITER}${salt.hex()}${dk.hex()}"


def verify_password(pwd: str, stored: str) -> bool:
    try:
        if stored.startswith("$2"):
            try:
                import bcrypt  # type: ignore
                return bcrypt.checkpw(pwd.encode("utf-8"), stored.encode("utf-8"))
            except Exception:
                return False
        if stored.startswith("pbkdf2$"):
            parts = stored.split("$")
            if len(parts) != 4:
                return False
            it = int(parts[1])
            salt = bytes.fromhex(parts[2])
            expect = parts[3]
            dk = hashlib.pbkdf2_hmac("sha256", pwd.encode("utf-8"), salt, it)
            return hmac.compare_digest(dk.hex(), expect)
        return False
    except Exception:
        return False


def user_get_by_username(username: str, con=None):
    n = normalize_username(username)
    close = con is None
    con = con or get_conn()
    try:
        r = con.execute("SELECT id,username,pass_hash,role,created_at,created_by FROM users WHERE username=?", (n,)).fetchone()
        return dict(r) if r else None
    finally:
        if close:
            con.close()


def user_get_by_id(uid: str, con=None):
    close = con is None
    con = con or get_conn()
    try:
        r = con.execute("SELECT id,username,pass_hash,role,created_at,created_by FROM users WHERE id=?", (uid,)).fetchone()
        return dict(r) if r else None
    finally:
        if close:
            con.close()


def user_list(con=None):
    close = con is None
    con = con or get_conn()
    try:
        return [dict(r) for r in con.execute("SELECT id,username,role,created_at,created_by FROM users ORDER BY username")]
    finally:
        if close:
            con.close()


def user_create(username: str, password: str, role: str = "user", created_by: str = "", con=None):
    n = validate_username(username)
    validate_password(password)
    if role not in ("admin", "user"):
        raise ValueError("role harus admin/user")
    close = con is None
    con = con or get_conn()
    try:
        if con.execute("SELECT 1 FROM users WHERE username=?", (n,)).fetchone():
            raise ValueError("username sudah ada")
        uid = str(uuid.uuid4())
        ph = hash_password(password)
        con.execute("INSERT INTO users(id,username,pass_hash,role,created_at,created_by) VALUES(?,?,?,?,?,?)",
                    (uid, n, ph, role, time.time(), created_by or ""))
        con.commit()
        return {"id": uid, "username": n, "role": role}
    finally:
        if close:
            con.close()


def user_delete(username: str, con=None):
    n = normalize_username(username)
    close = con is None
    con = con or get_conn()
    try:
        cur = con.execute("DELETE FROM users WHERE username=?", (n,))
        con.commit()
        # cascade sessions via FK or manual
        try:
            con.execute("DELETE FROM sessions WHERE user_id NOT IN (SELECT id FROM users)")
            con.commit()
        except Exception:
            pass
        return cur.rowcount > 0
    finally:
        if close:
            con.close()


def user_set_password(username: str, new_password: str, con=None):
    n = normalize_username(username)
    validate_password(new_password)
    close = con is None
    con = con or get_conn()
    try:
        ph = hash_password(new_password)
        cur = con.execute("UPDATE users SET pass_hash=? WHERE username=?", (ph, n))
        con.commit()
        return cur.rowcount > 0
    finally:
        if close:
            con.close()


def session_create(user_id: str, remember: bool = False, con=None):
    close = con is None
    con = con or get_conn()
    try:
        token = secrets.token_urlsafe(32)
        now = time.time()
        max_age = 7 * 86400 if remember else 2 * 86400
        exp = now + max_age
        con.execute("INSERT INTO sessions(token,user_id,expires_at,created_at,remember) VALUES(?,?,?,?,?)",
                    (token, user_id, exp, now, 1 if remember else 0))
        con.commit()
        return token, exp, max_age
    finally:
        if close:
            con.close()


def session_get(token: str, con=None):
    if not token:
        return None
    close = con is None
    con = con or get_conn()
    try:
        now = time.time()
        # cleanup expired lazily
        try:
            con.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            con.commit()
        except Exception:
            pass
        r = con.execute("SELECT token,user_id,expires_at,created_at,remember FROM sessions WHERE token=? AND expires_at > ?", (token, now)).fetchone()
        if not r:
            return None
        u = con.execute("SELECT id,username,role FROM users WHERE id=?", (r["user_id"],)).fetchone()
        if not u:
            return None
        return {"token": r["token"], "user_id": r["user_id"], "expires_at": r["expires_at"], "remember": bool(r["remember"]),
                "user": {"id": u["id"], "username": u["username"], "role": u["role"]}}
    finally:
        if close:
            con.close()


def session_delete(token: str, con=None):
    if not token:
        return False
    close = con is None
    con = con or get_conn()
    try:
        cur = con.execute("DELETE FROM sessions WHERE token=?", (token,))
        con.commit()
        return cur.rowcount > 0
    finally:
        if close:
            con.close()


def session_delete_by_user(user_id: str, con=None):
    close = con is None
    con = con or get_conn()
    try:
        con.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        con.commit()
    finally:
        if close:
            con.close()


# ── helper untuk hub_server exec (admin SQL editor) ──────────────
def db_execute(sql: str, *, timeout: float = 5.0, max_rows: int = 100):
    """Eksekusi 1 statement admin (SELECT/INSERT/UPDATE/DELETE).
    Dipanggil dari hub_server; hub tetap yang validasi auth/admin."""
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        raise ValueError("sql kosong")
    return sql


# ── WE (Eye of Wisdom): file terlihat + tercetak + verifikasi ──────
def _we_test_int(v):
    """Normalisasi flag test -> 0/1 meniru gaya wma_* (`1 if test else 0`).

    Kompatibel bool/int seperti wma; tambahan toleransi string:
    ''/'0'/'false'/'no'/'off'/'tidak' -> 0, '1'/'true'/'ya' -> 1,
    string angka lain ikut nilai int-nya, string asing non-kosong -> 1.
    """
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return 1 if v else 0
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("", "0", "false", "f", "no", "n", "off", "tidak",
                 "tdk", "bukan", "none", "null", "-"):
            return 0
        if s in ("1", "true", "t", "yes", "y", "on", "ya", "yaa"):
            return 1
        try:
            # string angka: "2"/"-1" -> ikut truthiness int-nya
            return 1 if int(s) != 0 else 0
        except Exception:
            return 1 if s else 0
    return 1 if v else 0


def _we_proc_str(p):
    """Normalisasi process_type -> string (None -> ''), meniru `p or ''` wma."""
    if p is None:
        return ""
    if isinstance(p, str):
        return p
    try:
        return p or ""
    except Exception:
        return ""


def we_seen_upsert(date, rows, con=None):
    """Catat file terlihat hari ini per-FILE (PK filename+date).

    first_seen_ts dipertahankan yang paling awal (monotonik maju): bila baris
    sudah ada, ts baru hanya menang bila lebih kecil (lebih awal) secara
    leksikografis (format ISO); bila salah satu kosong, yang non-kosong menang.
    """
    close = con is None
    con = con or get_conn()
    try:
        data = []
        for r in rows or []:
            fn = (r.get("filename") or "").strip() if isinstance(r.get("filename"), str) else (r.get("filename") or "")
            if not fn:
                continue
            data.append((fn, date or "",
                         r.get("nota") or "", r.get("bahan") or "",
                         r.get("sides") or "", r.get("laminating") or "",
                         _we_test_int(r.get("test")), _we_proc_str(r.get("process_type")),
                         r.get("customer") or "", int(r.get("total_qty") or 0),
                         r.get("jenis") or "", int(r.get("size") or 0),
                         int(r.get("mtime") or 0), r.get("first_seen_ts") or ""))
        if data:
            con.executemany(
                "INSERT INTO we_seen(filename,date,nota,bahan,sides,laminating,test,"
                "process_type,customer,total_qty,jenis,size,mtime,first_seen_ts)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(filename,date) DO UPDATE SET nota=excluded.nota,"
                "bahan=excluded.bahan,sides=excluded.sides,laminating=excluded.laminating,"
                "test=excluded.test,process_type=excluded.process_type,"
                "customer=excluded.customer,total_qty=excluded.total_qty,jenis=excluded.jenis,"
                "size=excluded.size,mtime=excluded.mtime,"
                "first_seen_ts=CASE"
                " WHEN we_seen.first_seen_ts IS NULL OR we_seen.first_seen_ts='' THEN excluded.first_seen_ts"
                " WHEN excluded.first_seen_ts IS NULL OR excluded.first_seen_ts='' THEN we_seen.first_seen_ts"
                " WHEN excluded.first_seen_ts < we_seen.first_seen_ts THEN excluded.first_seen_ts"
                " ELSE we_seen.first_seen_ts END",
                data)
        con.commit()
    finally:
        if close:
            con.close()


def we_seen_day(date, con=None):
    """Daftar file terlihat pada tanggal tsb, urut filename."""
    close = con is None
    con = con or get_conn()
    try:
        return [{**dict(r), "test": bool(r["test"])}
                for r in con.execute(
                    "SELECT filename,date,nota,bahan,sides,laminating,test,process_type,"
                    "customer,total_qty,jenis,size,mtime,first_seen_ts FROM we_seen"
                    " WHERE date=? ORDER BY filename", (date,))]
    finally:
        if close:
            con.close()


def we_printed_mark(date, rows, con=None):
    """Tandai file tercetak (idempoten, per-FILE PK filename+date).

    printed_ts monotonik maju: tandai ulang dengan ts lebih lama TIDAK
    memundurkan ts tersimpan (hanya ts lebih baru/sama yang menang; bila salah
    satu kosong, yang non-kosong menang). Field deskriptif nota/bahan/.../
    output_name selalu di-refresh ke nilai terbaru. operator/machine:
    nilai NON-KOSONG menang ("" tak pernah menghapus yg sudah tercatat —
    kalau tidak, tick otomatis tiap 5 dtk akan menimpa pilihan manual
    "Rahmadi" dgn "" sehingga tampilan flip-flop -/nama/-); utk machine,
    placeholder "Impose"/"Manual" juga tak pernah menimpa (bukan mesin asli).
    """
    close = con is None
    con = con or get_conn()
    try:
        data = []
        for r in rows or []:
            fn = (r.get("filename") or "").strip() if isinstance(r.get("filename"), str) else (r.get("filename") or "")
            if not fn:
                continue
            data.append((fn, date or "",
                         r.get("nota") or "", r.get("bahan") or "",
                         r.get("sides") or "", r.get("laminating") or "",
                         _we_test_int(r.get("test")), _we_proc_str(r.get("process_type")),
                         r.get("output_name") or "", r.get("printed_ts") or "",
                         r.get("operator") or "", r.get("machine") or ""))
        if data:
            con.executemany(
                "INSERT INTO we_printed(filename,date,nota,bahan,sides,laminating,test,"
                "process_type,output_name,printed_ts,operator,machine)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(filename,date) DO UPDATE SET nota=excluded.nota,"
                "bahan=excluded.bahan,sides=excluded.sides,laminating=excluded.laminating,"
                "test=excluded.test,process_type=excluded.process_type,"
                "output_name=excluded.output_name,"
                "operator=CASE WHEN excluded.operator IS NULL OR excluded.operator=''"
                " THEN we_printed.operator ELSE excluded.operator END,"
                "machine=CASE WHEN excluded.machine IS NULL OR excluded.machine IN ('','Impose','Manual')"
                " THEN we_printed.machine ELSE excluded.machine END,"
                "printed_ts=CASE"
                " WHEN excluded.printed_ts IS NULL OR excluded.printed_ts='' THEN we_printed.printed_ts"
                " WHEN we_printed.printed_ts IS NULL OR we_printed.printed_ts='' THEN excluded.printed_ts"
                " WHEN excluded.printed_ts >= we_printed.printed_ts THEN excluded.printed_ts"
                " ELSE we_printed.printed_ts END",
                data)
        con.commit()
    finally:
        if close:
            con.close()


def we_printed_day(date, con=None):
    """Daftar file tercetak pada tanggal tsb, urut filename."""
    close = con is None
    con = con or get_conn()
    try:
        return [{**dict(r), "test": bool(r["test"])}
                for r in con.execute(
                    "SELECT filename,date,nota,bahan,sides,laminating,test,process_type,"
                    "output_name,printed_ts,operator,machine FROM we_printed"
                    " WHERE date=? ORDER BY filename", (date,))]
    finally:
        if close:
            con.close()


def we_verify_row(nota, bahan="", sides="", laminating="", test=False, process_type="", ts="", con=None):
    """Verifikasi per-baris 6-tuple (cermin `wma_verify_row`)."""
    close = con is None
    con = con or get_conn()
    try:
        con.execute(
            "INSERT INTO we_verified(nota,bahan,sides,laminating,test,process_type,ts)"
            " VALUES(?,?,?,?,?,?,?) ON CONFLICT(nota,bahan,sides,laminating,test,process_type)"
            " DO UPDATE SET ts=excluded.ts",
            (nota or "", bahan or "", sides or "", laminating or "",
             _we_test_int(test), _we_proc_str(process_type), ts or ""))
        con.commit()
    finally:
        if close:
            con.close()


def we_verified_all(con=None):
    """Return set skey per-baris: 'nota||bahan||sides||lam||1/0||proc' (cermin `wma_verified_all`)."""
    close = con is None
    con = con or get_conn()
    try:
        out = set()
        for r in con.execute("SELECT nota,bahan,sides,laminating,test,process_type FROM we_verified"):
            out.add(skey((r[0], r[1] or "", r[2] or "", r[3] or "", r[4] or 0, r[5] or "")))
        return out
    finally:
        if close:
            con.close()
