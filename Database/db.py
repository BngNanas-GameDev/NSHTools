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
