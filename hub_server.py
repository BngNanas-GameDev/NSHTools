"""NSH Hub — WebSocket Server + Real-time Dashboard
Menengahi komunikasi WMA ↔ KMMA ↔ KMA + serve web dashboard.

Jalankan:
  python hub_server.py           → WS ws://0.0.0.0:8765 + Dashboard http://localhost:8766
  python hub_server.py --port 9000  → custom WS port

KMA (route.py NSH Lite) melapor via POST /ingest (tanpa dependensi baru).
"""
import asyncio
import datetime
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path
import http.server
import logging
import shutil
import urllib.request
import websockets

# Bunyi "opening handshake failed" = klien non-WS mengetuk port WS
# (mis. browser dibuka ke http://localhost:8765). Bising tapi tak berbahaya —
# redam agar konsol hub tetap bersih. Error handler kita sendiri tetap tampil.
logging.getLogger("websockets.server").setLevel(logging.CRITICAL)

# ══════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════
HOST = "0.0.0.0"
WS_PORT = 8765
HTTP_PORT = 8766
BASE_DIR = Path(__file__).resolve().parent
PID_FILE = BASE_DIR / "supervise_pids.json"

# Modul yang dikelola supervisor (nama, command, cwd).
# - lite : watcher imposisi, TANPA argumen (ada argumen = mode sekali jalan!)
# - kma  : router, --hub agar melapor via POST /ingest
# - wma  : --watch --hub (event-driven + WebSocket role 'wma')
# - kmma : --watch --hub (event-driven + WebSocket role 'kmma')
MANAGED_MODULES = ("lite", "kma", "wma", "kmma")
MODULE_ACTIONS = ("module_on", "module_off", "module_restart")
FORWARD_ACTIONS = ("retry", "skip", "process_all")

# ── Auth (4-Fase login) ───────────────────────────────────────────
# bcrypt bila ada else pbkdf2 260k, rate-limit 5/menit/IP, cookie httpOnly, session 2d/7d
AUTH_MAX_AGE = 2 * 86400
AUTH_MAX_AGE_REMEMBER = 7 * 86400
AUTH_RATE_LIMIT = 5
AUTH_RATE_WINDOW = 60
_login_attempts: dict = {}  # ip -> [ts,...]

# RBAC: admin-only vs user-allowed
ADMIN_ONLY_ACTIONS = {"hub_restart", "set_folders", "db_write", "db_exec", "users_create", "users_delete", "users_reset", "module_on", "module_off", "module_restart"}
USER_ALLOWED_ACTIONS = {"get_folders", "list_output", "move_output", "verify", "db_query", "db_tables"}


def _default_modules(ws_port):
    # KMA melapor via POST /ingest (butuh HTTP), WMA/KMMA via WebSocket.
    ws_url = f"ws://localhost:{ws_port}"
    http_url = f"http://localhost:{ws_port + 1}"
    py = sys.executable
    return {
        "lite": {"command": [py, "lite.py"], "cwd": str(BASE_DIR / "NSH Lite")},
        "kma": {"command": [py, "route.py", "--hub", "--hub-url", http_url],
                "cwd": str(BASE_DIR / "KMA")},
        "wma": {"command": [py, "wma.py", "--watch", "--hub", "--hub-url", ws_url],
                "cwd": str(BASE_DIR / "WMA")},
        "kmma": {"command": [py, "kmma.py", "--watch", "--hub", "--hub-url", ws_url],
                 "cwd": str(BASE_DIR / "KMMA")},
    }


def _load_module_override():
    """Override command modul via env NSH_SUPERVISE_OVERRIDE (JSON).

    Dipakai untuk pengujian dengan proses dummy, mis:
      {"lite": ["python", "-c", "import time; time.sleep(300)"]}
    atau {"lite": {"command": [...], "cwd": "..."}}.
    """
    raw = os.environ.get("NSH_SUPERVISE_OVERRIDE", "")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    out = {}
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, list):
                out[k] = {"command": v}
            elif isinstance(v, dict) and isinstance(v.get("command"), list):
                out[k] = v
    return out


# ══════════════════════════════════════════════════════════════
# SUPERVISOR (opt-in via --supervise; default MATI)
# ══════════════════════════════════════════════════════════════
SUPERVISE = False
SUPERVISED = {}  # nama → {"command": [...], "cwd": str, "proc": Popen|None}
AUTO_RESTART = True        # modul mati -> dinyalakan lagi (anti "connect/disconnect" forever)
RESTART_BACKOFF_S = (5, 10, 20, 40, 60, 120)  # naik sampai 120s, lalu mengulang
_MANUAL_OFF = set()   # modul yang operator matikan lewat module_off (JANGAN auto-restart)
_RESTART_STATE = {}  # nama → {"fails": int, "next": epoch_ts, "at": epoch_ts}


def supervise_init(ws_port):
    """Bangun konfigurasi modul yang dikelola (tanpa men-start)."""
    global SUPERVISED
    cfg = _default_modules(ws_port)
    for name, ov in _load_module_override().items():
        if name in cfg:
            cfg[name].update(ov)
    SUPERVISED = {n: {"command": c["command"], "cwd": c["cwd"], "proc": None}
                  for n, c in cfg.items()}


def _new_console_flags():
    return getattr(subprocess, "CREATE_NEW_CONSOLE", 0)


def module_alive(name):
    """True bila proses modul berjalan (poll() is None)."""
    info = SUPERVISED.get(name)
    if not info:
        return False
    proc = info.get("proc")
    return bool(proc is not None and proc.poll() is None)


def poll_modules():
    """Liveness semua modul dari status proses ASLI. Return {nama: bool}."""
    return {n: module_alive(n) for n in MANAGED_MODULES if n in SUPERVISED}


def _write_pids():
    try:
        pids = {n: info["proc"].pid for n, info in SUPERVISED.items()
                if info.get("proc") is not None and module_alive(n)}
        PID_FILE.write_text(json.dumps(pids, indent=2), encoding="utf-8")
    except Exception:
        pass


def _module_log_path(name, info):
    """Path log per-modul: <cwd modul>/<nama>.log.

    Pakai info["cwd"] (str, bisa berisi spasi mis. "NSH Lite") supaya path-nya
    persis di folder kerja modul — tempat file keluaran modul juga.dump."""
    try:
        return Path(info["cwd"]) / f"{name}.log"
    except Exception:
        return None


def start_module(name):
    """Start modul sebagai subprocess (log ke <cwd>/<nama>.log). Return (ok, msg)."""
    info = SUPERVISED.get(name)
    if not info:
        return False, f"modul tak dikenal: {name}"
    if module_alive(name):
        return True, f"{name} sudah berjalan (pid {info['proc'].pid})"
    # stdout/stderr -> file log (append, utf-8 lenient) supaya output tak hilang
    # dan encoding tak pernah jadi alasan print gagal.
    log_fh = None
    log_path = _module_log_path(name, info)
    if log_path is not None:
        try:
            log_fh = open(log_path, "a", encoding="utf-8", errors="replace")
        except Exception:
            log_fh = None
    # Tanpa console baru: pythonw + CREATE_NEW_CONSOLE justru bikin handle
    # stdout tak valid (OSError Errno 22). Log file = handle valid, dan operator
    # tetap bisa baca lewat file. Bila file log tak bisa dibuka, jatuh ke
    # DEVNULL (bukan console) — karena DEVNULL selalu punya handle yang valid.
    try:
        proc = subprocess.Popen(
            info["command"],
            cwd=info["cwd"],
            stdin=subprocess.DEVNULL,  # non-interaktif: ask_folders() via EOFError
            stdout=log_fh if log_fh is not None else subprocess.DEVNULL,
            stderr=log_fh if log_fh is not None else subprocess.DEVNULL,
            creationflags=0,
        )
    except Exception as e:
        return False, f"gagal start {name}: {e}"
    finally:
        # Popen sudah menduplikasi handle untuk child, jadi handle induk boleh
        # ditutup di sini tanpa memutus stdout child (dibatasi Windows: dup).
        if log_fh is not None:
            try:
                log_fh.close()
            except Exception:
                pass
    info["proc"] = proc
    _write_pids()
    return True, f"{name} dijalankan (pid {proc.pid})"


def stop_module(name):
    """Hentikan modul (terminate, fallback kill). Return (ok, msg)."""
    info = SUPERVISED.get(name)
    if not info:
        return False, f"modul tak dikenal: {name}"
    proc = info.get("proc")
    if proc is None or proc.poll() is not None:
        info["proc"] = None
        _write_pids()
        return True, f"{name} sudah berhenti"
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    except Exception as e:
        return False, f"gagal stop {name}: {e}"
    info["proc"] = None
    _write_pids()
    return True, f"{name} dihentikan"


def restart_module(name):
    if name not in SUPERVISED:
        return False, f"modul tak dikenal: {name}"
    ok, msg = stop_module(name)
    if not ok:
        return ok, msg
    time.sleep(1)
    return start_module(name)


def handle_module_command(action, module):
    """KONSUMSI command modul di hub (jangan diteruskan). Return (ok, msg)."""
    if action not in MODULE_ACTIONS:
        return False, f"aksi tak dikenal: {action}"
    if module not in MANAGED_MODULES or module not in SUPERVISED:
        return False, f"modul tak dikenal: {module}"
    if not SUPERVISE:
        return False, "supervisor mati (hub tanpa --supervise)"
    if action == "module_on":
        _MANUAL_OFF.discard(module)
        return start_module(module)
    if action == "module_off":
        _MANUAL_OFF.add(module)
        return stop_module(module)
    _MANUAL_OFF.discard(module)
    _RESTART_STATE.pop(module, None)  # restart manual = ulang dari backoff nol
    return restart_module(module)


def auto_restart_dead():
    """Nyalakan lagi modul yang mati mendadak (bukan operator-off).
    Backoff naik supaya modul crash-loop tak membanjiri hub/dashboard."""
    now = time.time()
    restarted = []
    for name in MANAGED_MODULES:
        if name not in SUPERVISED or name in _MANUAL_OFF:
            continue
        if module_alive(name):
            _RESTART_STATE.pop(name, None)
            continue
        st = _RESTART_STATE.setdefault(name, {"fails": 0, "next": 0.0, "at": 0.0})
        if now < st["next"]:
            continue
        ok, msg = start_module(name)
        if ok:
            restarted.append(name)
            print(f"  [supervise] auto-restart {msg}")
        st["fails"] += 1
        delay = RESTART_BACKOFF_S[min(st["fails"], len(RESTART_BACKOFF_S)) - 1]
        st["at"] = now
        st["next"] = now + delay
        add_log("supervise", f"{name} mati (fails ke-{st['fails']}), coba lagi {delay}s")
    return restarted


def stop_all_modules():
    for n in MANAGED_MODULES:
        try:
            if n in SUPERVISED:
                stop_module(n)
        except Exception:
            pass
    try:
        if PID_FILE.exists():
            PID_FILE.unlink()
    except Exception:
        pass


def handle_hub_restart():
    """Restart hub: luncurkan pengganti (console "NSH Hub Server" baru),
    lalu matikan diri + modul supervisi. Pengganti coba bind port berulang
    (hub lama butuh ±detik untuk bebas) dan HANYA nyalakan modul bila bind
    sukses — anti duplikat, anti mati-total. Dashboard/modul reconnect otomatis."""
    try:
        script = os.path.abspath(sys.argv[0])
        # JANGAN kutip manual: list2cmdline yang mengutip (kutip ganda
        # membuat cmd salah parse -> "cannot find the file ...").
        subprocess.Popen(
            ["cmd", "/c", "start", "NSH Hub Server", sys.executable, script]
            + [str(a) for a in sys.argv[1:]],
            cwd=str(BASE_DIR),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    except Exception as e:
        return False, f"gagal meluncurkan pengganti: {e}"

    def _do():
        try:
            if SUPERVISE:
                stop_all_modules()
        finally:
            os._exit(0)
    threading.Timer(1.0, _do).start()  # jeda: biarkan balasan terkirim dulu
    return True, "HUB restart ±15 detik (dashboard reconnect otomatis)"


# ── Auth helpers (hub + db bridge) ─────────────────────────────────
def _auth_ip(req_ip: str) -> str:
    return (req_ip or "unknown").strip() or "unknown"


def _auth_rate_ok(ip: str) -> bool:
    now = time.time()
    arr = _login_attempts.get(ip, [])
    arr = [t for t in arr if now - t < AUTH_RATE_WINDOW]
    if len(arr) >= AUTH_RATE_LIMIT:
        _login_attempts[ip] = arr
        return False
    arr.append(now)
    _login_attempts[ip] = arr
    return True


def _auth_token_from_headers(headers) -> str:
    try:
        cookie = headers.get("Cookie") or headers.get("cookie") or ""
        for part in str(cookie).split(";"):
            if part.strip().startswith("nsh_token="):
                return part.strip().split("=", 1)[1].strip()
    except Exception:
        pass
    try:
        auth = headers.get("Authorization") or headers.get("authorization") or ""
        if str(auth).lower().startswith("bearer "):
            return str(auth).split(" ", 1)[1].strip()
    except Exception:
        pass
    return ""


def _auth_token_from_ws(ws, msg_token=None):
    # 1) msg.token
    if msg_token:
        return str(msg_token).strip()
    # 2) ?token= in WS request path
    try:
        req = getattr(ws, "request", None)
        if req is not None:
            path = getattr(req, "path", "") or ""
            # Request.path may be string path including query?
            qs = ""
            if "?" in path:
                qs = path.split("?", 1)[1]
            else:
                # fallback: try headers host query?
                pass
            if qs:
                qp = urllib.parse.parse_qs(qs)
                t = (qp.get("token") or [""])[0]
                if t:
                    return t.strip()
            # 3) Cookie header
            try:
                hdrs = getattr(req, "headers", {}) or {}
                c = _auth_token_from_headers(hdrs)
                if c:
                    return c
            except Exception:
                pass
    except Exception:
        pass
    return ""


def _auth_lookup(token: str):
    if not token:
        return None
    try:
        db = _hub_db()
        if db is None:
            return None
        return db.session_get(token)
    except Exception:
        return None


def _auth_required_role(action: str, role: str) -> bool:
    if role == "admin":
        return True
    if action in ADMIN_ONLY_ACTIONS:
        return False
    # user allowed actions; unknown forward actions default deny for user
    if action in USER_ALLOWED_ACTIONS:
        return True
    if action in FORWARD_ACTIONS:
        return True  # retry/skip handled downstream but allowed for user
    return False


# ══════════════════════════════════════════════════════════════
# FOLDER CONFIG (dashboard ↔ lite.json / route.json)
# ══════════════════════════════════════════════════════════════
# Lite butuh INPUT+OUTPUT+UDA, KMA butuh INPUT+RETURN+UDA.
# Hub baca/tulis file config langsung — modul cukup di-restart
# (tombol power) agar path baru berlaku. Tanpa --supervise pun jalan.
FOLDER_FILES = {
    "lite": "NSH Lite/lite.json",
    "kma": "KMA/route.json",
}
FOLDER_KEYS = {
    "lite": ["input_folder", "output_folder", "uda_folder"],
    "kma": ["input_folder", "return_folder", "uda_folder", "ricoh_folder"],
}
# Kunci on/off (bukan path) khusus lite: "1" = ON, "0" = OFF. Default ON
# bila kunci belum ada (perilaku lama = otomatis jalan).
LITE_FLAGS = ["auto_impose", "copy_kisscut"]
# boleh kosong (= fitur mati; watcher RICOH off bila kosong)
FOLDER_OPTIONAL = {
    "lite": [],
    "kma": ["ricoh_folder"],
}
FOLDER_ACTIONS = ("get_folders", "set_folders")
OUTPUT_ACTIONS = ("list_output", "move_output")


def _folders_path(module):
    return BASE_DIR / FOLDER_FILES[module]


def _norm_flag(v):
    """Normalisasi flag on/off -> '1' (ON) atau '0' (OFF). Default ON
    (kunci tak ada / tak dikenal = perilaku lama: otomatis jalan)."""
    if v is None:
        return "1"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return "0" if v == 0 else "1"
    s = str(v).strip().lower()
    if s in ("0", "false", "off", "tidak", "no", "n"):
        return "0"
    return "1"


def _read_folders_file(module):
    try:
        data = json.loads(_folders_path(module).read_text(encoding="utf-8"))
    except Exception:
        data = {}
    out = {k: str(data.get(k, "")) for k in FOLDER_KEYS[module]}
    if module == "lite":
        for k in LITE_FLAGS:
            out[k] = _norm_flag(data.get(k, "1"))
    return out


def handle_folders_command(action, module, folders=None):
    """KONSUMSI di hub. get: baca file → (ok, msg, dict).
    set: validasi + tulis file (+ db bila bisa) → (ok, msg, dict)."""
    if action not in FOLDER_ACTIONS:
        return False, f"aksi tak dikenal: {action}", {}
    if module not in FOLDER_FILES:
        return False, f"modul tak dikenal: {module}", {}

    if action == "get_folders":
        current = _read_folders_file(module)
        return True, "ok", current

    # set_folders
    if not isinstance(folders, dict):
        return False, "folders kosong", {}
    keys = FOLDER_KEYS[module]
    optional = FOLDER_OPTIONAL.get(module, [])
    cleaned = {k: str(folders.get(k, "")).strip().strip('"').strip("'")
               for k in keys}
    # flag on/off lite (auto_impose, copy_kisscut): bukan path, dinormalisasi "1"/"0"
    flag_vals = {}
    if module == "lite":
        for k in LITE_FLAGS:
            if k in folders:
                flag_vals[k] = _norm_flag(folders.get(k))
    required = [k for k in keys if k not in optional]
    if any(not cleaned[k] for k in required):
        return False, "semua path wajib diisi", _read_folders_file(module)
    if not os.path.isdir(cleaned["input_folder"]):
        return False, f"INPUT tidak ada: {cleaned['input_folder']}", _read_folders_file(module)
    for k in keys[1:]:
        if not cleaned[k]:
            continue  # opsional kosong = fitur mati
        try:
            os.makedirs(cleaned[k], exist_ok=True)
        except Exception as e:
            return False, f"gagal siapkan {k}: {e}", _read_folders_file(module)
    try:
        path = _folders_path(module)
        data = {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
        data.update(cleaned)
        data.update(flag_vals)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        return False, f"gagal tulis config: {e}", _read_folders_file(module)
    try:
        sys.path.insert(0, str(BASE_DIR / "Database"))
        import db as nshdb
        merged = dict(cleaned)
        merged.update(flag_vals)
        nshdb.config_set(module, merged)
    except Exception:
        pass
    add_log("HUB", f"folders {module} disimpan — restart modul agar berlaku")
    # WE Phase 2: set_folders manual (lite/kma) = override ON (auto-path hanya log)
    try:
        _we_set_override(True, reason=f"set_folders {module} manual")
    except Exception:
        pass
    return True, "tersimpan — modul restart otomatis (±30 dtk)", cleaned


OPERATORS = ("Nanas", "Eric", "Rahmadi")


def _resolve_machine(dest):
    """Mesin untuk catatan move: DEVELOP 1/2 ikut printer aktif, RICOH tetap."""
    if dest == "ricoh":
        return "Ricoh"
    for p in kma_cache.get("printers", []) or []:
        if p.get("active"):
            ip = p.get("ip", "")
            if ip.endswith(".157"):
                return "Develop 1"
            if ip.endswith(".158"):
                return "Develop 2"
    return "Develop 1"


def _push_moves_to_watcher(items, operator):
    """Tandai Selesai (bukan verified) di watcher .54 per nama file.
    items = [(filename, machine)]. Return (updated, matched, total, err)."""
    try:
        today = datetime.date.today().isoformat()
        yest = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        by_name = {}
        # file baru dipindah perlu waktu watcher indeks (poll), coba beberapa kali
        for attempt in range(3):
            by_name.clear()
            for d in (today, yest):
                try:
                    _, data = _watcher_api(
                        "GET", f"/api/files?date_from={d}&date_to={d}", timeout=5)
                    rows = data if isinstance(data, list) else (data.get("value") or [])
                    for r in rows:
                        if r.get("filename") and r.get("id") and r["filename"] not in by_name:
                            by_name[r["filename"]] = r["id"]
                except Exception:
                    pass
            matched = []
            for fname, machine in items:
                fid = by_name.get(fname)
                if not fid and fname.lower().endswith("-ipy.pdf"):
                    # file impose ada suffix -ipy, cari nama aslinya di watcher
                    base = fname[:-8] + ".pdf"
                    fid = by_name.get(base)
                if fid:
                    matched.append((fname, machine, fid))
            if len(matched) == len(items) or attempt == 2:
                break
            time.sleep(2)
        ok_n = 0
        err = ""
        for fname, machine, fid in matched:
            try:
                st, _ = _watcher_api("POST", f"/api/files/{fid}",
                                     {"status": "done", "operator": operator,
                                      "machine_name": machine}, timeout=4)
                if 200 <= st < 300:
                    ok_n += 1
                else:
                    err = f"HTTP {st}"
            except Exception as e:
                err = str(e)[:80]
        return ok_n, len(matched), len(items), err
    except Exception as e:
        return 0, 0, len(items), str(e)[:80]


output_cache = []  # snapshot terakhir OUTPUT lite (didorong ke dashboard)
output_cache_ts = 0.0


def _snapshot_output():
    """Baca OUTPUT lite sekarang. Return (ok, files). Dipakai command + watcher."""
    try:
        lite_cfg = _read_folders_file("lite")
        raw_out = (lite_cfg.get("output_folder") or "").strip()
        if not raw_out:
            return False, []
        outdir = os.path.realpath(raw_out)
        if not os.path.isdir(outdir):
            return False, []
        files = []
        try:
            names = sorted(os.listdir(outdir))
        except OSError:
            return False, []
        for n in names:
            if not n.lower().endswith(".pdf"):
                continue
            try:
                st = os.stat(os.path.join(outdir, n))
                files.append({"name": n, "size": st.st_size, "ts": int(st.st_mtime)})
            except OSError:
                pass
        return True, files
    except Exception:
        return False, []


def _output_watcher():
    """Thread: awasi OUTPUT lite (snapshot 3 dtk, andal di drive jaringan),
    siarkan output_update bila berubah. Dashboard event-driven penuh."""
    global output_cache, output_cache_ts
    last = ""
    while True:
        try:
            ok, files = _snapshot_output()
            if ok:
                sig = json.dumps(files, ensure_ascii=False)
                if sig != last:
                    last = sig
                    output_cache = files
                    output_cache_ts = time.time()
                    # dari thread: wajib via threadsafe (bukan broadcast biasa)
                    broadcast_threadsafe(json.dumps({"type": "output_update", "files": files},
                                                    ensure_ascii=False))
        except Exception:
            pass
        time.sleep(3)


# ══════════════════════════════════════════════════════════════
# WE (Eye of Wisdom) — PHASE 2: watcher + auto-path + state API
# ══════════════════════════════════════════════════════════════
# - MONITORING = folder AaDevelop hari ini (WE/paths.py daily_paths).
#   Root tahun (mis. L:\2026) diturunkan dari ancestor input_folder lite
#   (nama dir 4-digit); TANPA fallback hardcoded — bila tak terpecahkan,
#   log sekali + lewati tick (didokumentasikan, bukan diam-diam).
# - Thread _we_watcher meniru _output_watcher (daemon, tick ±5 dtk,
#   try/except per-tick agar satu tick buruk tak membunuh thread).
#   Auto-path jalan di thread yang sama tiap 60 dtk (pilihan desain:
#   hemat 1 thread; didokumentasikan di sini).
# - Parser nama file LOKAL hub (_we_parse_filename): meniru pola regex
#   WMA/wma.py (R_NOTA/R_TOTAL/sides/lam/kisscut/diecut/test) + pola bahan
#   SEDERHANA lokal (tanpa bahan_dict agar hub tetap bebas-dependensi di
#   sini). KETERBATASAN: nama bahan eksotis di luar pola bisa "" / kasar —
#   cukup untuk bookkeeping WE, bukan agregasi presisi WMA.
# - Kunci cocok impose: lower() + kupas akhiran '-ipy' sebelum '.pdf';
#   fallback cocok via kesetaraan R_NOTA bila nama beda total.
# - output_moves tak punya kolom tanggal (ts = HH:MM:SS saja) → saring
#   "hari ini" mustahil; gantinya cocokkan N move terbaru per nama file
#   persis (best-effort, didokumentasikan).
# - Subfolder relatif hanya dipakai sbg info (petunjuk mesin) di
#   memori/broadcast — skema Phase-1 beku, tanpa kolom baru.
WE_STABLE_S = 5          # file lebih muda dari ini = setengah-copy, lewati (catat tick berikut)
WE_SCAN_INTERVAL_S = 5   # tick watcher WE (jaring pengaman; deteksi utama = watchdog di bawah)
WE_AUTO_INTERVAL_S = 60  # tick auto-path
WE_LIST_CAP = 500        # cap list spt endpoint list lain (kma events/limit 500)
WE_EVENT_STABLE_WAIT_S = 30  # batas tunggu file event stabil sebelum menyerah

we_cache = {"seen": [], "printed": [], "verified": []}  # cermin DB utk tanggal aktif
we_auto = {"mode": "wait", "monitoring": "", "override": False}  # mode wait/auto + flag manual
_we_today = datetime.date.today().isoformat()  # tanggal aktif (pemicu rollover)
_we_last_sig = None      # tanda siaran terakhir (anti spam WS)
_we_wait_logged = False  # banner WAIT sekali per episode folder-hilang
_we_year_warned = False  # peringatan root-tahun sekali per episode
_we_last_auto = 0.0      # ts auto-path terakhir
_we_auto_pending_sig = ""  # tanda perubahan tertahan (log sekali per perubahan)
_WE_PATHS_MOD = None     # modul WE/paths.py (lazy, cache)
_WE_EVENT_Q = []         # antre path PDF dari watchdog (top-level MONITORING saja)
_WE_EVENT_LOCK = threading.Lock()  # kunci antre + jadwal observer
_WE_EVENT = threading.Event()  # bangunkan worker saat ada event FS
_WE_WATCH_DIR = ""       # folder yg sedang diobserve (ganti saat rollover/path berubah)
_WE_OBSERVER = None      # watchdog Observer aktif (None = belum/tak bisa start)

# Regex lokal hub (cermin WMA/wma.py; hub DILARANG import WMA).
_WE_R_NOTA = re.compile(r"(ON\d{12})")
_WE_R_TOTAL = re.compile(r"TOTAL\s*=\s*(\d+)\s*(BESAR|KECIL)", re.I)
_WE_R_SIDES = re.compile(r"(?<![a-z0-9])([12])\s*s(?![a-z])", re.I)
_WE_R_LAM = re.compile(r"(?<![a-z])(doff?|glossy?)(?![a-z])", re.I)
_WE_R_KISS = re.compile(r"kiss\s*cut", re.I)
_WE_R_DIE = re.compile(r"die\s*cut", re.I)
_WE_R_TEST = re.compile(r"(?<![a-z])tes(t)?(?![a-z])", re.I)
_WE_R_BAHAN = re.compile(
    r"(Ap\d+gr|Ac\d+gr|(?:Stiker|Sticker)\s+\w+|HVS|Concord|BC\s+\w+|Master|BNTU"
    r"|NewToop\s*\d*\s*mic|Toop\s*\d*\s*mic|Art\s*Paper|Art\s*Carton|ArtPaper|ArtCarton"
    r"|(?:Kertas\s+)?[KC]rom[oe]|Vinyl|Vyinil|Vynil|Rajawali|Pindo|Jasmine|Ivory"
    r"|Transparant|Hologram|Gold|Silver)", re.I)


def _we_norm_date(v=None):
    """Normalisasi tanggal -> (obj_date|None, iso_str). Terima date/datetime/string ISO/None(=hari ini)."""
    try:
        if v is None:
            d = datetime.date.today()
            return d, d.isoformat()
        if isinstance(v, datetime.datetime):
            d = v.date()
            return d, d.isoformat()
        if isinstance(v, datetime.date):
            return v, v.isoformat()
        if isinstance(v, str):
            d = datetime.date.fromisoformat(v.strip()[:10])
            return d, d.isoformat()
    except Exception:
        pass
    d = datetime.date.today()
    return d, d.isoformat()


_WE_GUESS = None      # bahan_dict.guess_bahan (kanonik, spt WMA)
_WE_RESOLVE = None    # konica_printer.resolve_bahan (spt WMA)
_WE_BAHAN_TRIED = False  # import dicoba sekali saja


def _we_canon_bahan(filename, raw):
    """Kanonisasi bahan persis spt WMA (wma.py): raw regex -> guess_bahan ->
    resolve_bahan. Tanpa ini skey WE (mis. 'Sticker kromo') tak pernah cocok
    skey WMA ('Stiker Kromo') sehingga pill VERIFIED tak pernah menyala.
    Gagal import/unit -> kembalikan raw. Tak pernah raise."""
    global _WE_GUESS, _WE_RESOLVE, _WE_BAHAN_TRIED
    try:
        if not _WE_BAHAN_TRIED:
            _WE_BAHAN_TRIED = True
            try:
                sys.path.insert(0, str(BASE_DIR / "NSH Lite"))
                from bahan_dict import guess_bahan as _g
                _WE_GUESS = _g
            except Exception:
                _WE_GUESS = None
            try:
                from konica_printer import resolve_bahan as _r
                _WE_RESOLVE = _r
            except Exception:
                _WE_RESOLVE = None
        if _WE_GUESS is not None:
            try:
                canon = _WE_GUESS(filename)
            except Exception:
                canon = ""
            if canon:
                try:
                    if _WE_RESOLVE is not None:
                        return _WE_RESOLVE(filename, canon) or canon
                except Exception:
                    pass
                return canon
    except Exception:
        pass
    return raw


def _we_parse_filename(filename):
    """Parser nama file LOKAL hub (jangan import WMA): nota via ON\\d{12},
    TOTAL qty/jenis, sides 1s/2s, laminating doff/glossy, kisscut/diecut,
    test, + bahan pola sederhana lokal (keterbatasan: bahan eksotis bisa
    '' — cukup utk bookkeeping WE). Tak pernah raise."""
    out = {"nota": "", "bahan": "", "sides": "", "laminating": "",
           "test": False, "process_type": "", "customer": "",
           "total_qty": 0, "jenis": ""}
    try:
        fn = filename or ""
        m = _WE_R_NOTA.search(fn)
        if m:
            out["nota"] = m.group(1)
        m = _WE_R_BAHAN.search(fn)
        if m:
            out["bahan"] = m.group(1).strip()
        # Kanonisasi spt WMA (cromo->Kromo, BC Pindo->Pindo, ...): WAJIB agar
        # skey WE cocok dgn skey wma_verified (pill VERIFIED bisa menyala).
        try:
            canon = _we_canon_bahan(fn, out["bahan"])
            if canon:
                out["bahan"] = canon
        except Exception:
            pass
        m = _WE_R_TOTAL.search(fn)
        if m:
            try:
                out["total_qty"] = int(m.group(1))
            except Exception:
                out["total_qty"] = 0
            out["jenis"] = m.group(2).upper()
        if _WE_R_KISS.search(fn):
            out["process_type"] = "kisscut"
        elif _WE_R_DIE.search(fn):
            out["process_type"] = "diecut"
        m = _WE_R_SIDES.search(fn)
        if m:
            out["sides"] = m.group(1) + "s"
        m = _WE_R_LAM.search(fn)
        if m:
            low = (m.group(1) or "").lower()
            out["laminating"] = "doff" if low.startswith("do") else "glossy"
        out["test"] = bool(_WE_R_TEST.search(fn))
    except Exception:
        pass
    return out


def _we_norm_impose_key(name):
    """Kunci cocok impose: lower() + kupas akhiran '-ipy' sebelum '.pdf'."""
    try:
        s = (name or "").lower()
        if s.endswith("-ipy.pdf"):
            s = s[:-8] + ".pdf"
        return s
    except Exception:
        return (name or "").lower()


def _we_norm_path(p):
    """Normalisasi path utk banding config (slash + casefold, toleran win)."""
    try:
        return os.path.normpath((p or "").strip()).casefold().replace("/", "\\")
    except Exception:
        return (p or "").strip().casefold()


def _we_year_root(lite_input=None):
    """Root tahun = ancestor input_folder lite yg bernama 4-digit (mis. L:\\2026).

    TANPA fallback hardcoded: tak terpecahkan -> None (pemanggil log sekali
    + lewati tick). Tak pernah raise."""
    try:
        raw = lite_input
        if not raw:
            try:
                raw = (_read_folders_file("lite").get("input_folder") or "").strip()
            except Exception:
                raw = ""
        if not raw:
            return None
        p = Path(os.path.normpath(raw))
        for anc in [p] + list(p.parents):
            if re.fullmatch(r"\d{4}", anc.name or ""):
                return str(anc)
        return None
    except Exception:
        return None


def _we_daily_paths(year_root, tdate):
    """Bungkus WE/paths.py daily_paths (load lazy via lokasi file, cache modul).

    Dipakai agar hub tak butuh WE sbg package. Gagal -> None (sinyal WAIT)."""
    global _WE_PATHS_MOD
    try:
        if _WE_PATHS_MOD is None:
            import importlib.util
            fp = BASE_DIR / "WE" / "paths.py"
            spec = importlib.util.spec_from_file_location("we_paths_hub", str(fp))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _WE_PATHS_MOD = mod
        return _WE_PATHS_MOD.daily_paths(year_root, tdate)
    except Exception:
        return None


def _we_output_moves_map(limit=2000):
    """{filename: {dest, machine, operator, ts}} dari move terbaru.

    KETERBATASAN (skema output_moves beku): tanpa kolom tanggal (ts cuma
    HH:MM:SS) sehingga saring harian mustahil — cocokkan move terbaru per
    nama file persis (best-effort). Tak pernah raise."""
    try:
        db = _hub_db()
        if db is None:
            return {}
        con = db.get_conn()
        try:
            rows = con.execute(
                "SELECT file,dest,machine,operator,ts FROM output_moves"
                " ORDER BY id DESC LIMIT ?", (int(limit or 2000),)).fetchall()
        finally:
            try:
                con.close()
            except Exception:
                pass
        m = {}
        for r in rows:
            try:
                d = dict(r)
            except Exception:
                continue
            fn = (d.get("file") or "").strip()
            if fn and fn not in m:
                m[fn] = {"dest": d.get("dest") or "", "machine": d.get("machine") or "",
                         "operator": d.get("operator") or "", "ts": d.get("ts") or ""}
        return m
    except Exception:
        return {}


def _we_kma_printable_map(iso, limit=2000):
    """{filename: {dest, machine, status, ts}} dari kma_events yg menandakan cetak.

    KMA (route.py) menulis langsung ke DB via kma_event_add — TIDAK pernah
    membuat baris output_moves (hanya dashboard move_output yg mencatat ke
    output_moves via db.output_move_add). Tanpa aturan ini, file yg dirutekan
    KMA otomatis akan macet di seen setelah aturan output_cache dihapus.

    dest aktual KMA (dibaca dari KMA/route.py):
    - status 'ok'   -> dest = nama hot folder (ST_GLOSS_1S, ART_230_1S, ...)
      + machine DEVELOP 1/2 (COPY ke printer + arsip Uda)
    - status 'done' -> dest = nama hot folder + machine DEVELOP 1/2 (Done->Uda)
    - status 'ricoh'-> dest = 'RICOH' + machine 'RICOH' (watcher, tanpa dipindah)
    - status 'return'/'wait'/'error' -> GAGAL/belum cetak -> DIABAIKAN
      (dest RETURN/folder tapi bukan bukti cetak).
    Hanya status ok/done/ricoh yg dianggap printed. Operator kosong
    (KMA otomatis, tanpa operator manusia). Tak pernah raise."""
    try:
        db = _hub_db()
        if db is None:
            return {}
        try:
            evs = db.kma_events_day(iso, int(limit or 2000))
        except Exception:
            return {}
        m = {}
        for e in evs or []:
            try:
                if not isinstance(e, dict):
                    continue
                st = str(e.get("status") or "").strip().lower()
                # Hanya bukti cetak: ok/done/ricoh. return/wait/error diabaikan.
                if st not in ("ok", "done", "ricoh"):
                    continue
                fn = str(e.get("file") or "").strip()
                if not fn:
                    continue
                # evs tertua->terbaru: timpa agar yg terbaru menang.
                m[fn] = {"dest": str(e.get("dest") or ""),
                         "machine": str(e.get("machine") or ""),
                         "status": st,
                         "ts": str(e.get("ts") or "")}
            except Exception:
                continue
        return m
    except Exception:
        return {}


def _we_refresh_cache(iso=None, db=None):
    """Segarkan we_cache dari DB (seen+printed tanggal tsb, verified global)."""
    try:
        iso = iso or _we_today
        if db is None:
            db = _hub_db()
        if db is None:
            return
        try:
            seen = db.we_seen_day(iso) or []
        except Exception:
            seen = []
        try:
            printed = db.we_printed_day(iso) or []
        except Exception:
            printed = []
        try:
            ver = sorted(db.we_verified_all() or set())
        except Exception:
            ver = []
        we_cache["seen"] = seen
        we_cache["printed"] = printed
        we_cache["verified"] = ver
    except Exception:
        pass


def _we_sig():
    """Tanda perubahan cache: hitungan + ts termuda (cukup utk anti spam WS)."""
    try:
        seen = we_cache.get("seen") or []
        printed = we_cache.get("printed") or []
        ver = we_cache.get("verified") or []
        mx_s = ""
        for r in seen:
            v = str((r or {}).get("first_seen_ts") or "")
            if v > mx_s:
                mx_s = v
        mx_p = ""
        for r in printed:
            v = str((r or {}).get("printed_ts") or "")
            if v > mx_p:
                mx_p = v
        return (len(seen), len(printed), len(ver), mx_s, mx_p)
    except Exception:
        return None


def _we_pending_names(seen, printed):
    """Nama file seen yg BELUM printed (semantik hitungan tampilan).
    Dipakai snapshot WS + HTTP agar konsisten. Tak pernah raise."""
    try:
        done = set()
        for p in printed or []:
            try:
                fn = ((p or {}).get("filename") or "").strip() if isinstance(p, dict) else ""
                if fn:
                    done.add(fn)
            except Exception:
                continue
        out = []
        for r in seen or []:
            try:
                fn = ((r or {}).get("filename") or "").strip() if isinstance(r, dict) else ""
                if fn and fn not in done:
                    out.append(fn)
            except Exception:
                continue
        return out
    except Exception:
        return []


def _we_pending_count():
    """Jumlah seen yg BELUM printed (seen minus printed, per filename).

    Tampilan memakai semantik gerak: file yg sudah printed keluar dari hitungan
    seen (0 seen 3 printed bila semua tercetak). DB tetap menyimpan riwayat
    penuh (seen_rows tak pernah dihapus) utk pencocokan + audit."""
    try:
        seen = we_cache.get("seen") or []
        printed = we_cache.get("printed") or []
        return len(_we_pending_names(seen, printed))
    except Exception:
        return 0


def _we_snapshot():
    """Snapshot WE utk WS init + we_update + GET /api/we/state (cap 500).

    seen_count = pending (belum printed); seen_total_count = riwayat.
    List seen/printed dikirim penuh (UI butuh semua baris utk pill + tombol)."""
    try:
        seen = we_cache.get("seen") or []
        printed = we_cache.get("printed") or []
        ver = we_cache.get("verified") or []
        return {"date": _we_today, "monitoring": we_auto.get("monitoring") or "",
                "mode": we_auto.get("mode") or "wait",
                "override": bool(we_auto.get("override")),
                "seen_count": _we_pending_count(), "printed_count": len(printed),
                "seen_total_count": len(seen),
                "verified_count": len(ver),
                "seen": list(seen)[:WE_LIST_CAP],
                "printed": list(printed)[:WE_LIST_CAP],
                "verified": list(ver)[:WE_LIST_CAP]}
    except Exception:
        return {"date": "", "monitoring": "", "mode": "wait", "override": False,
                "seen_count": 0, "printed_count": 0, "verified_count": 0,
                "seen": [], "printed": [], "verified": []}


def _we_backfill_seen_fields(iso, db):
    """Tulis-ulang field parse-an we_seen hari ini dgn parser TERBARU.

    Baris lama (sebelum kanonisasi bahan) punya bahan mentah ('Sticker kromo')
    yg skey-nya tak cocok wma_verified ('Stiker Kromo') — pill VERIFIED mati
    selamanya utk file-file itu. Backfill bandingkan per-baris & UPDATE hanya
    yg berubah (filename/date/size/mtime/first_seen_ts tak tersentuh).
    Return jumlah baris diperbarui. Tak pernah raise."""
    n = 0
    try:
        if db is None:
            return 0
        try:
            rows = db.we_seen_day(iso) or []
        except Exception:
            return 0
        for r in rows:
            try:
                fn = (r or {}).get("filename") or ""
                if not fn:
                    continue
                pr = _we_parse_filename(fn)
                new = {"bahan": pr.get("bahan") or "",
                       "sides": pr.get("sides") or "",
                       "laminating": pr.get("laminating") or "",
                       "test": 1 if pr.get("test") else 0,
                       "process_type": pr.get("process_type") or "",
                       "customer": pr.get("customer") or "",
                       "total_qty": int(pr.get("total_qty") or 0),
                       "jenis": pr.get("jenis") or ""}
                old = {"bahan": (r.get("bahan") or ""),
                       "sides": (r.get("sides") or ""),
                       "laminating": (r.get("laminating") or ""),
                       "test": 1 if r.get("test") else 0,
                       "process_type": (r.get("process_type") or ""),
                       "customer": (r.get("customer") or ""),
                       "total_qty": int(r.get("total_qty") or 0),
                       "jenis": (r.get("jenis") or "")}
                if new == old:
                    continue
                try:
                    con = db.get_conn()
                except Exception:
                    continue
                try:
                    con.execute(
                        "UPDATE we_seen SET bahan=?, sides=?, laminating=?, test=?,"
                        " process_type=?, customer=?, total_qty=?, jenis=?"
                        " WHERE filename=? AND date=?",
                        (new["bahan"], new["sides"], new["laminating"], new["test"],
                         new["process_type"], new["customer"], new["total_qty"],
                         new["jenis"], fn, iso))
                    con.commit()
                    n += 1
                except Exception:
                    pass
                finally:
                    try:
                        con.close()
                    except Exception:
                        pass
            except Exception:
                continue
    except Exception:
        pass
    return n


def _preload_we_from_db(today=None):
    """Muat WE hari ini dari DB ke we_cache (sblm bind port, sblm _preload WMA).
    Pulihkan juga flag override manual persisten (config tabel 'we')."""
    global _we_today
    try:
        _tdate, iso = _we_norm_date(today)
    except Exception:
        iso = datetime.date.today().isoformat()
    _we_today = iso
    db = None
    try:
        db = _hub_db()
        if db is not None:
            try:
                cur = db.config_get("we") or {}
            except Exception:
                cur = {}
            if isinstance(cur, dict) and cur.get("manual_override"):
                we_auto["override"] = True
    except Exception:
        pass
    try:
        n_fix = _we_backfill_seen_fields(iso, db)
        if n_fix:
            try:
                add_log("WE", f"backfill parse {n_fix} baris seen hari ini (kanonisasi bahan)")
            except Exception:
                pass
    except Exception:
        pass
    _we_refresh_cache(iso, db)
    try:
        add_log("HUB", f"WE hari ini dimuat dari DB: {len(we_cache.get('seen') or [])} seen, "
                       f"{len(we_cache.get('printed') or [])} printed, "
                       f"{len(we_cache.get('verified') or [])} verified")
    except Exception:
        pass


def _we_set_override(val, reason=""):
    """Set flag override manual (memori + persisten config tabel 'we')."""
    we_auto["override"] = bool(val)
    try:
        db = _hub_db()
        if db is not None:
            try:
                cur = db.config_get("we") or {}
            except Exception:
                cur = {}
            if not isinstance(cur, dict):
                cur = {}
            cur["manual_override"] = bool(val)
            db.config_set("we", cur)
    except Exception:
        pass
    try:
        add_log("WE", f"override manual {'ON' if val else 'OFF'}"
                       + (f" ({reason})" if reason else ""))
    except Exception:
        pass


def _we_check_rollover(iso):
    """Rollover tengah malam: reset cache tanggal, muat ulang tanggal baru,
    log ganti hari, bersihkan override. Return True bila rollover terjadi."""
    global _we_today, _we_last_sig, _we_wait_logged
    try:
        if not iso or iso == _we_today:
            return False
        old = _we_today
        _we_today = iso
        we_cache["seen"] = []
        we_cache["printed"] = []
        _we_last_sig = None
        _we_wait_logged = False
        try:
            _we_set_override(False, reason="ganti hari")
        except Exception:
            pass
        try:
            _preload_we_from_db(iso)
        except Exception:
            pass
        try:
            add_log("WE", f"ganti hari {old}→{iso} (cache tanggal dimuat ulang, override dibersihkan)")
        except Exception:
            pass
        return True
    except Exception:
        return False


def _we_scan_once(today=None, year_root=None):
    """Satu siklus scan WE: MONITORING -> we_seen -> transisi printed.

    - year_root eksplisit utk injeksi uji; None = turunkan dari lite input.
    - WAIT (daily None): set we_auto mode wait, banner log sekali per episode.
    - Hanya *.pdf (case-insensitive) LEVEL ATAS MONITORING yg masuk we_seen;
      SUBFOLDER (Impose/Uda/DEVELOP/RICOH/...) TIDAK diintip — sesuai arahan
      operator, WE cukup melihat folder AaDevelop itu sendiri.
    - File labil (mtime < WE_STABLE_S) dilewati, tercatat tick berikut.
    - we_seen_upsert SATU batch; printed HANYA bila (a) ada di output_moves
      (aksi move_output dashboard: operator+mesin asli) ATAU (b) ada di
      kma_events dgn status ok/done/ricoh (rute KMA otomatis: operator kosong,
      mesin dari event) — cocok nama ternormalisasi kupas '-ipy', fallback
      kesetaraan NOTA dari nama file. Muncul di OUTPUT/Impose SAJA (tanpa
      move/event/manual) TIDAK lagi menandai printed — harus ada input
      pengguna (pindah dashboard / tandai manual) atau rute KMA.
      Manual POST /api/we/mark-printed di luar scan (operator/mesin dropdown).
      Lalu we_printed_mark + refresh cache + siaran we_update HANYA bila
      berubah. Return dict ringkasan."""
    global _we_wait_logged, _we_year_warned, _we_last_sig
    try:
        tdate, iso = _we_norm_date(today)
    except Exception:
        return {"ok": False, "mode": "wait", "reason": "tanggal buruk"}
    try:
        _we_check_rollover(iso)
    except Exception:
        pass
    try:
        yroot = year_root if year_root is not None else _we_year_root()
    except Exception:
        yroot = None
    if not yroot:
        # TANPA fallback hardcoded (mis. L:\\2026 dilarang): log sekali + lewati
        if not _we_year_warned:
            _we_year_warned = True
            try:
                add_log("WE", "tunggu: root tahun tak terpecahkan dari lite input_folder "
                               "(tanpa fallback) — lewati tick")
            except Exception:
                pass
        we_auto["mode"] = "wait"
        return {"ok": False, "mode": "wait", "reason": "year-root tak terpecahkan"}
    _we_year_warned = False
    try:
        paths = _we_daily_paths(yroot, tdate)
    except Exception:
        paths = None
    if not paths:
        we_auto["mode"] = "wait"
        we_auto["monitoring"] = ""
        if not _we_wait_logged:
            _we_wait_logged = True
            try:
                lite = _read_folders_file("lite")
                add_log("WE", f"tunggu: folder hari/AaDevelop belum ada "
                               f"(lite input={lite.get('input_folder', '?')}) — mode WAIT")
            except Exception:
                pass
        return {"ok": True, "mode": "wait", "monitoring": "",
                "seen": 0, "printed": 0, "verified": len(we_cache.get("verified") or [])}
    _we_wait_logged = False
    mon_s = str(paths.get("monitoring") or "")
    we_auto["mode"] = "auto"
    we_auto["monitoring"] = mon_s
    now = time.time()
    now_s = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    seen_rows = []
    try:
        # Level atas saja: tidak masuk subfolder (Impose/Uda/DEVELOP/RICOH/...).
        with os.scandir(mon_s) as it:
            entries = [e for e in it if e.is_file(follow_symlinks=False)]
    except OSError:
        entries = []
    for e in entries:
        fn = e.name
        try:
            if not fn.lower().endswith(".pdf"):
                continue  # .cdr/.txt/.bak/.tmp/non-PDF diabaikan
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            if now - st.st_mtime < WE_STABLE_S:
                continue  # setengah-copy: catat tick berikut bila stabil
            pr = _we_parse_filename(fn)
            seen_rows.append({"filename": fn, "nota": pr.get("nota") or "",
                              "bahan": pr.get("bahan") or "",
                              "sides": pr.get("sides") or "",
                              "laminating": pr.get("laminating") or "",
                              "test": pr.get("test") or False,
                              "process_type": pr.get("process_type") or "",
                              "customer": pr.get("customer") or "",
                              "total_qty": pr.get("total_qty") or 0,
                              "jenis": pr.get("jenis") or "",
                              "size": int(st.st_size or 0),
                              "mtime": int(st.st_mtime or 0),
                              "first_seen_ts": now_s})
        except Exception:
            continue
    db = None
    try:
        db = _hub_db()
        if db is not None and seen_rows:
            db.we_seen_upsert(iso, seen_rows)  # SATU batch, bukan per-file
    except Exception:
        pass
    # Transisi seen->printed, tick yg sama: (a) output_moves (aksi move_output
    # dashboard: operator+mesin asli) ATAU (b) kma_events ok/done/ricoh (rute
    # KMA otomatis: operator kosong, mesin dari event). Cocok nama
    # ternormalisasi kupas '-ipy', fallback kesetaraan NOTA dari nama file.
    # Muncul di OUTPUT/Impose SAJA tanpa move/event/manual TIDAK printed.
    try:
        moves = _we_output_moves_map()
    except Exception:
        moves = {}
    try:
        moves_norm = {}
        moves_nota = {}
        for mfn, mv in (moves or {}).items():
            try:
                moves_norm.setdefault(_we_norm_impose_key(mfn), (mfn, mv))
                mn = _we_parse_filename(mfn).get("nota") or ""
                if mn:
                    moves_nota.setdefault(mn, (mfn, mv))
            except Exception:
                continue
    except Exception:
        moves_norm = {}
        moves_nota = {}
    try:
        kma_map = _we_kma_printable_map(iso)
    except Exception:
        kma_map = {}
    try:
        kma_norm = {}
        kma_nota = {}
        for kfn, kev in (kma_map or {}).items():
            try:
                if not kfn or not str(kfn).lower().endswith(".pdf"):
                    continue
                kma_norm.setdefault(_we_norm_impose_key(kfn), (kfn, kev))
                kn = _we_parse_filename(kfn).get("nota") or ""
                if kn:
                    kma_nota.setdefault(kn, (kfn, kev))
            except Exception:
                continue
    except Exception:
        kma_norm = {}
        kma_nota = {}
    # Kandidat seen = scan saat ini + riwayat DB hari ini. Riwayat WAJIB ikut:
    # Lite memindah input ke uda() begitu impose selesai, jadi file yg outputnya
    # baru muncul sering sudah hilang dari scan — tanpa riwayat ia tak pernah
    # jadi printed. Scan saat ini menang bila bentrok.
    try:
        hist = (db.we_seen_day(iso) or []) if db is not None else []
    except Exception:
        hist = []
    try:
        seen_by_norm = {}
        seen_by_nota = {}
        for h in hist:
            try:
                hf = (h.get("filename") or "").strip()
                if not hf:
                    continue
                seen_by_norm.setdefault(_we_norm_impose_key(hf), h)
                hn = (h.get("nota") or "").strip()
                if hn:
                    seen_by_nota.setdefault(hn, h)
            except Exception:
                continue
        for s in seen_rows:
            try:
                fn = (s.get("filename") or "").strip()
                if not fn:
                    continue
                seen_by_norm[_we_norm_impose_key(fn)] = s
                sn = (s.get("nota") or "").strip()
                if sn:
                    seen_by_nota[sn] = s
            except Exception:
                continue
    except Exception:
        seen_by_norm = {}
        seen_by_nota = {}
    # Rekonstruksi DIBATASI (pelajaran insiden: 319 baris basi 1 tick karena
    # kandidat tanpa filter tanggal; lalu 276 baris basi dari moves): hanya
    # bila ada bukti output HARI INI yg terbukti tanggalnya. Aturan:
    # - cache OUTPUT: file harus ada dgn mtime (ts = st_mtime via
    #   _snapshot_output) >= 00:00 hari ini (day-start = mktime(tdate);
    #   sisa kemarin yg nangkring di folder = basi, abaikan).
    # - event KMA: sudah tersaring harian via kma_events_day -> boleh.
    # - moves: TANPA tanggal di skema (ts cuma HH:MM:SS) -> DILARANG jadi
    #   kandidat rekonstruksi (membangkitkan file basi); moves HANYA menandai
    #   printed pada seen yg SUDAH ada (blok `if s is None: continue` di bawah
    #   tetap). File pindahan dashboard tetap tercakup karena saat dipindah
    #   outputnya ada di OUTPUT (aturan mtime-segar) kecuali balapan <5 dtk.
    # Kandidat output = cache OUTPUT hub + event KMA SAJA (tanpa moves).
    try:
        try:
            _day_start = time.mktime(tdate.timetuple())
        except Exception:
            _day_start = 0
        cands = []
        try:
            for _f in (output_cache or []):
                try:
                    _nm = (_f or {}).get("name") or ""
                    if not _nm or not _nm.lower().endswith(".pdf"):
                        continue
                    try:
                        _ts = int((_f or {}).get("ts") or 0)
                    except Exception:
                        _ts = 0
                    if _day_start and _ts < _day_start:
                        continue  # sisa kemarin, bukan output hari ini
                    cands.append(_nm)
                except Exception:
                    continue
        except Exception:
            pass
        for _kfn, _kev in list((kma_norm or {}).values()) + list((kma_nota or {}).values()):
            cands.append(_kfn)
        for _co in cands:
            try:
                if not _co:
                    continue
                _nk = _we_norm_impose_key(_co)
                if _nk in seen_by_norm:
                    continue
                # TANPA penjaga NOTA: satu nota boleh dipakai banyak file
                # (mis. Eci01/02/03 satu nota) — tiap kunci-norm unik wajib
                # dapat baris seen sendiri via _we_ensure_seen_for_output.
                _row = _we_ensure_seen_for_output(_co, iso, now_s, db)
                if _row and (_row.get("filename") or "").strip():
                    seen_by_norm.setdefault(_we_norm_impose_key(_row["filename"]), _row)
                    _rn = (_row.get("nota") or "").strip()
                    if _rn:
                        seen_by_nota.setdefault(_rn, _row)
            except Exception:
                continue
    except Exception:
        pass
    printed_rows = []
    try:
        matched_norm_keys = set()
        matched_notas = set()
        # (a) tiap move -> baris seen-nya (operator/mesin asli dari move)
        for nk, (mfn, mv) in (moves_norm or {}).items():
            try:
                s = seen_by_norm.get(nk)
                if s is None:
                    continue
                fn = (s.get("filename") or "").strip()
                if not fn:
                    continue
                matched_norm_keys.add(nk)
                printed_rows.append({"filename": fn, "nota": s.get("nota") or "",
                                     "bahan": s.get("bahan") or "",
                                     "sides": s.get("sides") or "",
                                     "laminating": s.get("laminating") or "",
                                     "test": s.get("test") or False,
                                     "process_type": s.get("process_type") or "",
                                     "output_name": mfn,
                                     "printed_ts": now_s,
                                     "operator": mv.get("operator") or "",
                                     "machine": mv.get("machine") or ""})
            except Exception:
                continue
        for nota_key, (mfn, mv) in (moves_nota or {}).items():
            try:
                if nota_key in matched_notas:
                    continue
                s = seen_by_nota.get(nota_key)
                if s is None:
                    continue
                fn = (s.get("filename") or "").strip()
                if not fn:
                    continue
                if _we_norm_impose_key(fn) in matched_norm_keys:
                    continue  # sudah kena via nama persis
                matched_notas.add(nota_key)
                printed_rows.append({"filename": fn, "nota": s.get("nota") or "",
                                     "bahan": s.get("bahan") or "",
                                     "sides": s.get("sides") or "",
                                     "laminating": s.get("laminating") or "",
                                     "test": s.get("test") or False,
                                     "process_type": s.get("process_type") or "",
                                     "output_name": mfn,
                                     "printed_ts": now_s,
                                     "operator": mv.get("operator") or "",
                                     "machine": mv.get("machine") or ""})
            except Exception:
                continue
        # (b) tiap event KMA ok/done/ricoh -> baris seen-nya (otomatis: operator
        # kosong, mesin dari event). Lewati yg sudah kena via move agar
        # operator asli move tidak tertimpa operator kosong KMA.
        for nk, (kfn, kev) in (kma_norm or {}).items():
            try:
                if nk in matched_norm_keys:
                    continue  # sudah printed via move (operator asli menang)
                s = seen_by_norm.get(nk)
                if s is None:
                    continue
                fn = (s.get("filename") or "").strip()
                if not fn:
                    continue
                matched_norm_keys.add(nk)
                printed_rows.append({"filename": fn, "nota": s.get("nota") or "",
                                     "bahan": s.get("bahan") or "",
                                     "sides": s.get("sides") or "",
                                     "laminating": s.get("laminating") or "",
                                     "test": s.get("test") or False,
                                     "process_type": s.get("process_type") or "",
                                     "output_name": kfn,
                                     "printed_ts": now_s,
                                     "operator": "",
                                     "machine": (kev or {}).get("machine") or ""})
            except Exception:
                continue
        for nota_key, (kfn, kev) in (kma_nota or {}).items():
            try:
                if nota_key in matched_notas:
                    continue
                s = seen_by_nota.get(nota_key)
                if s is None:
                    continue
                fn = (s.get("filename") or "").strip()
                if not fn:
                    continue
                if _we_norm_impose_key(fn) in matched_norm_keys:
                    continue  # sudah kena via nama persis
                matched_notas.add(nota_key)
                printed_rows.append({"filename": fn, "nota": s.get("nota") or "",
                                     "bahan": s.get("bahan") or "",
                                     "sides": s.get("sides") or "",
                                     "laminating": s.get("laminating") or "",
                                     "test": s.get("test") or False,
                                     "process_type": s.get("process_type") or "",
                                     "output_name": kfn,
                                     "printed_ts": now_s,
                                     "operator": "",
                                     "machine": (kev or {}).get("machine") or ""})
            except Exception:
                continue
    except Exception:
        pass
    try:
        if db is not None and printed_rows:
            db.we_printed_mark(iso, printed_rows)
    except Exception:
        pass
    _we_refresh_cache(iso, db)
    try:
        sig = _we_sig()
        if sig != _we_last_sig:
            _we_last_sig = sig
            try:
                broadcast_threadsafe(json.dumps({"type": "we_update", **_we_snapshot()},
                                                ensure_ascii=False))
            except Exception:
                pass
    except Exception:
        pass
    return {"ok": True, "mode": "auto", "monitoring": mon_s,
            "seen": _we_pending_count(),
            "seen_total": len(we_cache.get("seen") or []),
            "printed": len(we_cache.get("printed") or []),
            "verified": len(we_cache.get("verified") or [])}


def _we_refresh_announce(iso=None, db=None):
    """Segarkan we_cache + siarkan we_update bila berubah.

    Jalur pasca-tulis yg SAMA dipakai ekor _we_scan_once (refresh cache,
    banding tanda, broadcast threadsafe) — bukan mekanisme siaran baru.
    Dipakai endpoint tulis-manual WE agar dashboard ikut segar."""
    global _we_last_sig
    try:
        try:
            _d, _iso = _we_norm_date(iso or _we_today)
        except Exception:
            _iso = _we_today
        try:
            _we_refresh_cache(_iso, db)
        except Exception:
            pass
        try:
            sig = _we_sig()
            if sig != _we_last_sig:
                _we_last_sig = sig
                try:
                    broadcast_threadsafe(json.dumps({"type": "we_update", **_we_snapshot()},
                                                    ensure_ascii=False))
                except Exception:
                    pass
        except Exception:
            pass
    except Exception:
        pass


def _we_apply_paths(want_lite, want_kma):
    """Terapkan path harian (cermin semantik handle_folders_command).

    - INPUT harus ada; buat HANYA subfolder Impose/DEVELOP/Uda/RICOH —
      JANGAN pernah buat folder tanggal itu sendiri.
    - Tulis lite.json + config.set('lite') DAN route.json + config.set('kma')
      (pertahankan kunci lain spt flag lite). Return (ok, msg)."""
    try:
        aa_in = ((want_lite or {}).get("input_folder") or "").strip()
        if not aa_in or not os.path.isdir(aa_in):
            return False, f"INPUT tidak ada: {aa_in or '?'}"
        for sub in (((want_lite or {}).get("output_folder") or ""),
                    ((want_lite or {}).get("uda_folder") or ""),
                    ((want_kma or {}).get("input_folder") or ""),
                    ((want_kma or {}).get("uda_folder") or ""),
                    ((want_kma or {}).get("ricoh_folder") or "")):
            s = (sub or "").strip()
            if not s:
                continue
            try:
                os.makedirs(s, exist_ok=True)
            except Exception as e:
                return False, f"gagal siapkan {s}: {e}"
        try:
            lp = _folders_path("lite")
            data_l = {}
            try:
                data_l = json.loads(lp.read_text(encoding="utf-8"))
            except Exception:
                pass
            data_l.update({k: str((want_lite or {}).get(k) or "") for k in FOLDER_KEYS["lite"]})
            lp.write_text(json.dumps(data_l, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            return False, f"gagal tulis lite.json: {e}"
        try:
            kp = _folders_path("kma")
            data_k = {}
            try:
                data_k = json.loads(kp.read_text(encoding="utf-8"))
            except Exception:
                pass
            data_k.update({k: str((want_kma or {}).get(k) or "") for k in FOLDER_KEYS["kma"]})
            kp.write_text(json.dumps(data_k, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            return False, f"gagal tulis route.json: {e}"
        try:
            db = _hub_db()
            if db is not None:
                try:
                    db.config_set("lite", _read_folders_file("lite"))
                except Exception:
                    pass
                try:
                    db.config_set("kma", _read_folders_file("kma"))
                except Exception:
                    pass
        except Exception:
            pass
        try:
            add_log("WE", f"auto path diterapkan: {aa_in}")
        except Exception:
            pass
        return True, f"auto path diterapkan: {aa_in}"
    except Exception as e:
        return False, f"error: {e}"


def _we_auto_tick(today=None, year_root=None):
    """Satu siklus auto-path (dipanggil tiap 60 dtk dari thread WE).

    Beda dari config tersimpan (banding casefold/slash-normal) -> terapkan
    via _we_apply_paths; saat override manual ON hanya LOG sekali per
    perubahan (tanpa spam). WAIT (daily None) -> lewati. Return dict."""
    global _we_auto_pending_sig
    try:
        tdate, iso = _we_norm_date(today)
    except Exception:
        return {"applied": False, "reason": "tanggal buruk"}
    try:
        yroot = year_root if year_root is not None else _we_year_root()
    except Exception:
        yroot = None
    if not yroot:
        return {"applied": False, "reason": "year-root tak terpecahkan"}
    try:
        paths = _we_daily_paths(yroot, tdate)
    except Exception:
        paths = None
    if not paths:
        we_auto["mode"] = "wait"
        return {"applied": False, "reason": "WAIT (folder hari/AaDevelop belum ada)"}
    we_auto["mode"] = "auto"
    we_auto["monitoring"] = str(paths.get("monitoring") or "")
    try:
        lp = paths.get("lite") or {}
        kp = paths.get("kma") or {}
        want_lite = {"input_folder": str(lp.get("input") or ""),
                     "output_folder": str(lp.get("output") or ""),
                     "uda_folder": str(lp.get("uda") or "")}
        want_kma = {"input_folder": str(kp.get("input") or ""),
                    "return_folder": str(kp.get("return") or ""),
                    "uda_folder": str(kp.get("uda") or ""),
                    "ricoh_folder": str(kp.get("ricoh") or "")}
    except Exception:
        return {"applied": False, "reason": "paths buruk"}
    if not all(want_lite.values()) or not all(want_kma.values()):
        return {"applied": False, "reason": "paths tak lengkap"}
    try:
        cur_lite = _read_folders_file("lite")
        cur_kma = _read_folders_file("kma")
    except Exception:
        return {"applied": False, "reason": "config tak terbaca"}
    same_lite = all(_we_norm_path(cur_lite.get(k)) == _we_norm_path(v)
                    for k, v in want_lite.items())
    same_kma = all(_we_norm_path(cur_kma.get(k)) == _we_norm_path(v)
                   for k, v in want_kma.items())
    if same_lite and same_kma:
        _we_auto_pending_sig = ""
        return {"applied": False, "reason": "sudah cocok"}
    sig = json.dumps({"lite": want_lite, "kma": want_kma}, ensure_ascii=False)
    if we_auto.get("override"):
        if sig != _we_auto_pending_sig:
            _we_auto_pending_sig = sig
            try:
                add_log("WE", f"auto ditahan (override manual ON): akan pindah ke "
                               f"{want_lite['input_folder']}")
            except Exception:
                pass
        return {"applied": False, "reason": "override manual ON (hanya log)"}
    _we_auto_pending_sig = ""
    ok, msg = _we_apply_paths(want_lite, want_kma)
    return {"applied": bool(ok), "reason": msg}


def _we_note_event(path):
    """Tampung path PDF dari watchdog utk diproses worker (cepat, tak pernah raise)."""
    try:
        if not path:
            return
        with _WE_EVENT_LOCK:
            if path not in _WE_EVENT_Q:
                _WE_EVENT_Q.append(path)
            _WE_EVENT.set()
    except Exception:
        pass


def _we_record_file(fp, iso, now_s, timeout_s=0, fast=True):
    """Catat SATU file ke we_seen (dipakai jalur watchdog event-driven).

    fast=True (default): catat SECEPATNYA tanpa tunggu stabil — begitu event
    masuk + file ada, langsung upsert. Meta (size/mtime) yg mungkin masih
    bertumbuh (copy berjalan) di-refresh tick/event berikut via upsert, dan
    first_seen_ts terawal selalu dijaga. Ini yg membuat deteksi <3 dtk:
    event watchdog -> antre -> catat (biasanya <1 dtk).
    fast=False: perilaku lama, tunggu stabil (umur >= WE_STABLE_S + ukuran
    sama 2x cek) max timeout_s, utk diagnostik/pemakaian khusus.
    File hilang sebelum tercatat -> False. Tak pernah raise.
    Return True bila tercatat."""
    try:
        timeout_s = timeout_s or WE_EVENT_STABLE_WAIT_S
    except Exception:
        timeout_s = 30
    try:
        t0 = time.time()
        last_size = -1
        stable_hits = 0
        size = 0
        st = None
        while True:
            try:
                st = os.stat(fp)
            except OSError:
                return False  # hilang/dipindah sebelum tercatat
            try:
                size = int(st.st_size or 0)
                age = time.time() - float(st.st_mtime or 0)
            except Exception:
                return False
            if fast:
                break  # catat sekarang; meta menyusul via upsert berikut
            if size == last_size and age >= WE_STABLE_S:
                stable_hits += 1
            else:
                stable_hits = 0
            last_size = size
            if stable_hits >= 1:
                break
            if time.time() - t0 >= timeout_s:
                return False
            time.sleep(1)
        fn = os.path.basename(fp)
        if not fn.lower().endswith(".pdf"):
            return False
        try:
            pr = _we_parse_filename(fn)
        except Exception:
            return False
        try:
            db = _hub_db()
            if db is None:
                return False
            db.we_seen_upsert(iso, [{"filename": fn, "nota": pr.get("nota") or "",
                                     "bahan": pr.get("bahan") or "",
                                     "sides": pr.get("sides") or "",
                                     "laminating": pr.get("laminating") or "",
                                     "test": pr.get("test") or False,
                                     "process_type": pr.get("process_type") or "",
                                     "customer": pr.get("customer") or "",
                                     "total_qty": pr.get("total_qty") or 0,
                                     "jenis": pr.get("jenis") or "",
                                     "size": size, "mtime": int(st.st_mtime or 0),
                                     "first_seen_ts": now_s}])
        except Exception:
            return False
        try:
            _we_refresh_announce(iso, db)
        except Exception:
            pass
        return True
    except Exception:
        return False


def _we_ensure_seen_for_output(out_name, iso, now_s, db=None):
    """Pastikan output punya baris seen (rekonstruksi bila file input lenyap
    sebelum terpantau — mis. Lite memindah ke uda dalam hitungan detik).

    Cocok riwayat we_seen hari ini HANYA via kunci-norm (lower + kupas
    '-ipy'); ketemu -> kembalikan barisnya (anti-duplikat). Cocok NOTA saja
    SENGAJA tak mengembalikan baris saudara — satu nota boleh dipakai banyak
    file (mis. Eci01/02/03 satu nota); mengembalikan saudara membuat file lain
    terkunci selamanya. Tanpa cocok kunci-norm -> SELALU turunkan baris baru
    dari nama output ASLI (kupas '-ipy', jaga casing; parse spt biasa) lalu
    upsert + kembalikan. Return dict baris atau None. Tak pernah raise."""
    try:
        if db is None:
            try:
                db = _hub_db()
            except Exception:
                db = None
        if db is None:
            return None
        try:
            hist = db.we_seen_day(iso) or []
        except Exception:
            hist = []
        nk = ""
        try:
            nk = _we_norm_impose_key(out_name)
        except Exception:
            nk = (out_name or "").lower()
        for h in hist:
            try:
                hf = ((h or {}).get("filename") or "").strip()
                if not hf:
                    continue
                if _we_norm_impose_key(hf) == nk:
                    return h
                # SENGAJA tanpa fallback NOTA: nota sama tak boleh
                # mengembalikan baris saudara yg salah.
            except Exception:
                continue
        # Tak ada riwayat: turunkan nama input dari nama output ASLI (jaga
        # casing; JANGAN pakai kunci-norm lowercase — regex NOTA case-sensitive
        # dan nama file asli memudahkan cocok ulang).
        try:
            s = (out_name or "").strip()
            low = s.lower()
            if low.endswith("-ipy.pdf"):
                cand = s[:-8] + ".pdf"
            elif low.endswith(".pdf"):
                cand = s
            else:
                return None
            if not cand:
                return None
            pr = _we_parse_filename(cand)
            row = {"filename": cand, "nota": pr.get("nota") or "",
                   "bahan": pr.get("bahan") or "",
                   "sides": pr.get("sides") or "",
                   "laminating": pr.get("laminating") or "",
                   "test": pr.get("test") or False,
                   "process_type": pr.get("process_type") or "",
                   "customer": pr.get("customer") or "",
                   "total_qty": pr.get("total_qty") or 0,
                   "jenis": pr.get("jenis") or "",
                   "size": 0, "mtime": 0, "first_seen_ts": now_s}
            try:
                db.we_seen_upsert(iso, [row])
            except Exception:
                return None
            return row
        except Exception:
            return None
    except Exception:
        return None


def _we_event_worker():
    """Thread worker antre watchdog: catat file event satu per satu.

    Daemon, try/except per-file; jeda 1 dtk antar file agar DB tak dibanjiri
    saat operator drop belasan file sekaligus. Tak pernah mati."""
    while True:
        try:
            _WE_EVENT.wait(5)
            _WE_EVENT.clear()
            while True:
                try:
                    with _WE_EVENT_LOCK:
                        if not _WE_EVENT_Q:
                            break
                        fp = _WE_EVENT_Q.pop(0)
                except Exception:
                    break
                try:
                    tdate = datetime.date.today()
                    iso = tdate.isoformat()
                except Exception:
                    continue
                try:
                    now_s = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    now_s = ""
                try:
                    # Hanya file yg masih di MONITORING aktif (abaikan basi
                    # dari folder kemarin / path yg sudah tak relevan).
                    mon = (we_auto.get("monitoring") or "").strip()
                    if not mon or os.path.dirname(os.path.abspath(fp)) != os.path.abspath(mon):
                        continue
                except Exception:
                    continue
                try:
                    _we_record_file(fp, iso, now_s)
                except Exception:
                    pass
                try:
                    time.sleep(0.2)  # jeda antar file kecil (deteksi <3 dtk)
                except Exception:
                    pass
        except Exception:
            try:
                time.sleep(5)
            except Exception:
                pass


def _we_ensure_observer(mon_s):
    """Pastikan watchdog observe folder MONITORING aktif (top-level saja).

    Dipanggil tiap tick: bila folder berubah -> jadwal ulang; bila folder
    hilang -> hentikan observe (tunggu folder kembali). Gagal import/start
    watchdog -> diam (polling tick tetap jalan sbg jaring pengaman).
    Tak pernah raise. Return True bila observer aktif utk mon_s."""
    global _WE_WATCH_DIR, _WE_OBSERVER
    try:
        want = (mon_s or "").strip()
        with _WE_EVENT_LOCK:
            cur_obs = _WE_OBSERVER
            cur_dir = _WE_WATCH_DIR
        if not want or not os.path.isdir(want):
            if cur_obs is not None:
                try:
                    cur_obs.unschedule_all()
                    cur_obs.stop()
                except Exception:
                    pass
                with _WE_EVENT_LOCK:
                    _WE_OBSERVER = None
                    _WE_WATCH_DIR = ""
            return False
        if cur_obs is not None and cur_dir == os.path.abspath(want):
            try:
                if cur_obs.is_alive():
                    return True
            except Exception:
                pass
        # (re)schedule: hentikan yg lama dulu
        if cur_obs is not None:
            try:
                cur_obs.unschedule_all()
                cur_obs.stop()
            except Exception:
                pass
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except Exception:
            with _WE_EVENT_LOCK:
                _WE_OBSERVER = None
                _WE_WATCH_DIR = ""
            return False

        root = os.path.abspath(want)

        class _WeHandler(FileSystemEventHandler):
            def _push(self, p):
                try:
                    if not p:
                        return
                    ap = os.path.abspath(p)
                    if os.path.dirname(ap) != root:
                        return  # subfolder diabaikan (WE hanya level atas)
                    if not os.path.basename(ap).lower().endswith(".pdf"):
                        return
                    _we_note_event(ap)
                except Exception:
                    pass

            def on_created(self, event):
                try:
                    if not event.is_directory:
                        self._push(event.src_path)
                except Exception:
                    pass

            def on_modified(self, event):
                try:
                    if not event.is_directory:
                        self._push(event.src_path)
                except Exception:
                    pass

            def on_moved(self, event):
                try:
                    if not event.is_directory:
                        self._push(event.dest_path)
                except Exception:
                    pass

        obs = Observer()
        try:
            obs.schedule(_WeHandler(), root, recursive=False)
            obs.daemon = True
            obs.start()
        except Exception:
            try:
                obs.stop()
            except Exception:
                pass
            with _WE_EVENT_LOCK:
                _WE_OBSERVER = None
                _WE_WATCH_DIR = ""
            return False
        with _WE_EVENT_LOCK:
            _WE_WATCH_DIR = root
            _WE_OBSERVER = obs
        return True
    except Exception:
        return False


def _we_watcher():
    """Thread WE: scan MONITORING tiap tick + auto-path tiap 60 dtk.
    Cermin _output_watcher (daemon, try/except per-tick). Deteksi utama
    = watchdog event (instant, anti-terskip); scan tick = jaring pengaman
    (termasuk transisi printed via moves yg tak hasilkan event FS)."""
    global _we_last_auto
    while True:
        try:
            try:
                today = datetime.date.today()
            except Exception:
                today = None
            if today is not None:
                try:
                    _we_scan_once(today)
                except Exception:
                    pass
                # Pastikan watchdog observe folder MONITORING aktif
                # (top-level saja; atur ulang saat rollover/ganti path).
                try:
                    mon = (we_auto.get("monitoring") or "").strip()
                    if mon:
                        _we_ensure_observer(mon)
                except Exception:
                    pass
                try:
                    if time.time() - _we_last_auto >= WE_AUTO_INTERVAL_S:
                        _we_auto_tick(today)
                        _we_last_auto = time.time()
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(WE_SCAN_INTERVAL_S)


def _we_http_state(date_str):
    """Payload GET /api/we/state (selalu dari DB utk tanggal tsb; cap 500)."""
    try:
        _d, iso = _we_norm_date(date_str or datetime.date.today())
    except Exception:
        iso = datetime.date.today().isoformat()
    seen = []
    printed = []
    ver = []
    try:
        db = _hub_db()
        if db is not None:
            try:
                seen = db.we_seen_day(iso) or []
            except Exception:
                seen = []
            try:
                printed = db.we_printed_day(iso) or []
            except Exception:
                printed = []
            try:
                ver = sorted(db.we_verified_all() or set())
            except Exception:
                ver = []
    except Exception:
        pass
    return {"ok": True, "date": iso, "monitoring": we_auto.get("monitoring") or "",
            "auto": {"mode": we_auto.get("mode") or "wait",
                     "override": bool(we_auto.get("override"))},
            "seen_count": len(_we_pending_names(seen, printed)),
            "seen_total_count": len(seen), "printed_count": len(printed),
            "verified_count": len(ver),
            "seen": seen[:WE_LIST_CAP], "printed": printed[:WE_LIST_CAP],
            "verified": ver[:WE_LIST_CAP]}


def _we_query_files(state, date="", limit=20, offset=0, search=""):
    """Query paginasi we_seen/we_printed (cermin _db_query_table; limit 1..100)."""
    st = (state or "").strip().lower()
    if st not in ("seen", "printed"):
        return False, "state harus seen|printed"
    try:
        limit = max(1, min(100, int(limit or 20)))
    except Exception:
        limit = 20
    try:
        offset = max(0, int(offset or 0))
    except Exception:
        offset = 0
    search = (search or "").strip()
    try:
        _d, iso = _we_norm_date(date or datetime.date.today())
    except Exception:
        iso = datetime.date.today().isoformat()
    tbl = "we_seen" if st == "seen" else "we_printed"
    try:
        db = _hub_db()
        if db is None:
            return False, "DB tidak tersedia"
        con = db.get_conn()
        try:
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{tbl}")').fetchall()]
            if not cols:
                return False, f"tabel {tbl} tidak ada"
            params: list = [iso]
            where = " WHERE date=?"
            if search:
                conds = " OR ".join(f'CAST("{c}" AS TEXT) LIKE ?' for c in cols)
                where += " AND (" + conds + ")"
                params += [f"%{search}%"] * len(cols)
            total = con.execute(f'SELECT COUNT(*) FROM "{tbl}"' + where, params).fetchone()[0]
            rows = []
            for r in con.execute(f'SELECT * FROM "{tbl}"' + where +
                                 " ORDER BY filename LIMIT ? OFFSET ?",
                                 params + [limit, offset]):
                d = dict(r)
                if isinstance(d.get("test"), int):
                    d["test"] = bool(d["test"])
                rows.append(d)
            return True, {"state": st, "date": iso, "columns": cols, "rows": rows,
                          "total": int(total or 0), "limit": limit,
                          "offset": offset, "search": search}
        finally:
            try:
                con.close()
            except Exception:
                pass
    except Exception as e:
        return False, f"error: {e}"


def handle_output_command(action, msg):
    """KONSUMSI di hub. list_output: daftar PDF di OUTPUT lite.
    move_output: pindahkan ke DEVELOP (input KMA) / RICOH (ricoh KMA) +
    catat operator/mesin ke DB + tandai Selesai (bukan verified) di watcher .54.
    Validasi ketat: src harus di dalam OUTPUT lite, dst hanya 2 folder KMA.
    Return (ok, msg, files)."""
    try:
        lite_cfg = _read_folders_file("lite")
        raw_out = (lite_cfg.get("output_folder") or "").strip()
        if not raw_out:
            return False, "OUTPUT lite belum dikonfigurasi", []
        outdir = os.path.realpath(raw_out)
        if not os.path.isdir(outdir):
            return False, "OUTPUT lite tak ada", []
        if action == "list_output":
            ok, files = _snapshot_output()
            if ok:
                global output_cache, output_cache_ts
                output_cache = files
                output_cache_ts = time.time()
                return True, "ok", files
            return False, "OUTPUT lite tak ada/tak terbaca", []
        if action == "move_output":
            dest = (msg.get("dest") or "").strip().lower()
            names = msg.get("files") or []
            operator = (msg.get("operator") or "").strip()
            if operator not in OPERATORS:
                return False, "pilih operator dulu (Nanas/Eric/Rahmadi)", []
            kma_cfg = _read_folders_file("kma")
            destmap = {"develop": kma_cfg.get("input_folder", ""),
                       "ricoh": kma_cfg.get("ricoh_folder", "")}
            raw_dst = (destmap.get(dest, "") or "").strip()
            if not raw_dst:
                return False, f"tujuan {dest or '?'} belum dikonfigurasi", []
            dstdir = os.path.realpath(raw_dst)
            if not os.path.isdir(dstdir):
                return False, f"tujuan {dest} tak ada: {raw_dst}", []
            if not isinstance(names, list) or not names:
                return False, "tak ada file dipilih", []
            moved, failed = [], []
            for n in names:
                if not isinstance(n, str) or not n or n != os.path.basename(n):
                    failed.append(f"{n}: nama tak valid")
                    continue
                src = os.path.realpath(os.path.join(outdir, n))
                if not src.startswith(outdir + os.sep) or not os.path.isfile(src):
                    failed.append(f"{n}: tak ada di OUTPUT")
                    continue
                dst = os.path.join(dstdir, n)
                if os.path.exists(dst):
                    failed.append(f"{n}: sudah ada di tujuan")
                    continue
                try:
                    shutil.move(src, dst)
                    moved.append({"name": n})
                except OSError as e:
                    failed.append(f"{n}: {e}")
            add_log("HUB", f"output -> {dest}: {len(moved)} pindah"
                           + (f", {len(failed)} gagal" if failed else ""))
            if not moved:
                return False, "; ".join(failed) or "tak ada yang pindah", []
            machine = _resolve_machine(dest)
            try:
                db = _hub_db()
                if db is not None:
                    for m in moved:
                        db.output_move_add(ts(), m["name"], dest, machine, operator)
            except Exception:
                pass
            upd, matched, total, werr = _push_moves_to_watcher(
                [(m["name"], machine) for m in moved], operator)
            res = f"{len(moved)} file -> {dest.upper()} [{machine} • {operator}]"
            if failed:
                res += f" ({len(failed)} gagal: " + "; ".join(failed)[:120] + ")"
            if matched == 0:
                res += " (.54: tak ada baris cocok)"
            elif upd == matched:
                res += f" (.54: {upd} Selesai)"
            else:
                res += f" (.54: {upd}/{matched}" + (f" — {werr}" if werr else "") + ")"
            return True, res, moved
        return False, f"aksi tak dikenal: {action}", []
    except Exception as e:
        return False, f"error: {e}", []


# ── DB read-only (phpMyAdmin mini) ─────────────────────────────────────
# Whitelist tabel yang boleh di-browse (read-only, anti SQL injection).
DB_ALLOWED = {
    "kmma_state", "wma_entries", "wma_seen", "kma_stats", "kma_events",
    "km_orders", "wma_verified", "config", "output_moves", "validasi", "events",
    "we_seen", "we_printed", "we_verified",
}

def _db_list_tables():
    """Return [{name, count, columns, pk, indexes}] untuk tabel whitelist yang ada."""
    try:
        db = _hub_db()
        if db is None:
            return []
        con = db.get_conn()
        try:
            existing = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            out = []
            for tbl in sorted(DB_ALLOWED):
                if tbl not in existing:
                    continue
                try:
                    cnt = con.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
                except Exception:
                    cnt = 0
                try:
                    info = con.execute(f'PRAGMA table_info("{tbl}")').fetchall()
                    cols = [r[1] for r in info]
                    pk = [r[1] for r in info if r[5]]
                except Exception:
                    cols = []
                    pk = []
                try:
                    idx_rows = con.execute(f'PRAGMA index_list("{tbl}")').fetchall()
                    indexes = []
                    for ir in idx_rows:
                        iname = ir[1] if len(ir) > 1 else str(ir[0])
                        try:
                            ii = con.execute(f'PRAGMA index_info("{iname}")').fetchall()
                            icols = [x[2] for x in ii]
                        except Exception:
                            icols = []
                        indexes.append({"name": iname, "unique": bool(ir[2]) if len(ir) > 2 else False, "columns": icols})
                except Exception:
                    indexes = []
                out.append({"name": tbl, "count": int(cnt or 0), "columns": cols, "pk": pk, "indexes": indexes})
            return out
        finally:
            try:
                con.close()
            except Exception:
                pass
    except Exception:
        return []


# ── SQL exec admin (POST /api/db/exec) ──────────────────────────────────
_FORBIDDEN_SQL = re.compile(r"\b(DROP|ALTER|TRUNCATE|ATTACH|DETACH|VACUUM|REINDEX|PRAGMA)\b", re.I)
_ALLOWED_START = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE)\b", re.I)

def _db_exec_sql(sql: str):
    """Validasi + eksekusi 1 statement admin. Return dict {ok, columns, rows, rowcount, lastRowId}."""
    raw = (sql or "").strip()
    if not raw:
        return False, "sql kosong"
    # split ; limit 1 statement per klik (allow trailing ;)
    stmts = [s.strip() for s in raw.split(";") if s.strip()]
    if len(stmts) != 1:
        return False, "hanya 1 statement per eksekusi (hapus ; berlebih)"
    stmt = stmts[0]
    if _FORBIDDEN_SQL.search(stmt):
        return False, "statement terlarang (DROP/ALTER/PRAGMA/VACUUM/ATTACH tidak diizinkan)"
    if not _ALLOWED_START.match(stmt):
        return False, "hanya SELECT/INSERT/UPDATE/DELETE yang diizinkan"
    # exec dengan timeout 5s + transaction
    try:
        db = _hub_db()
        if db is None:
            return False, "DB tidak tersedia"
        con = db.get_conn()
        con.execute("PRAGMA busy_timeout=5000;")
        # timeout via progress handler not needed; sqlite busy_timeout + python timeout via signal is enough
        # optional backup before big write: VACUUM INTO skipped (cheap) - log only
        is_select = bool(re.match(r"^\s*SELECT\b", stmt, re.I))
        try:
            cur = con.execute("BEGIN IMMEDIATE")
        except Exception:
            pass
        try:
            if is_select:
                cur = con.execute(stmt)
                cols = [d[0] for d in cur.description] if cur.description else []
                rows_raw = cur.fetchmany(101)
                # limit 100 rows
                truncated = len(rows_raw) > 100
                rows = rows_raw[:100]
                # dict rows
                dict_rows = [dict(zip(cols, r)) if not isinstance(r, dict) else r for r in rows]
                # stringify bytes
                for d in dict_rows:
                    for k, v in list(d.items()):
                        if isinstance(v, bytes):
                            try:
                                d[k] = v.decode("utf-8", errors="replace")
                            except Exception:
                                d[k] = str(v)
                con.execute("COMMIT")
                return True, {"columns": cols, "rows": dict_rows, "rowcount": len(dict_rows), "truncated": truncated, "lastRowId": cur.lastrowid or 0}
            else:
                cur = con.execute(stmt)
                rc = cur.rowcount if cur.rowcount != -1 else 0
                lid = cur.lastrowid or 0
                con.execute("COMMIT")
                return True, {"columns": [], "rows": [], "rowcount": int(rc), "lastRowId": int(lid)}
        except Exception as e:
            try:
                con.execute("ROLLBACK")
            except Exception:
                pass
            return False, f"SQL error: {e}"
        finally:
            try:
                con.close()
            except Exception:
                pass
    except Exception as e:
        return False, f"error: {e}"


def _db_query_table(name, limit=20, offset=0, search=""):
    """Read-only paginated query. Return (ok, payload|msg)."""
    if name not in DB_ALLOWED:
        return False, "tabel tidak diizinkan"
    try:
        limit = max(1, min(100, int(limit or 20)))
    except Exception:
        limit = 20
    try:
        offset = max(0, int(offset or 0))
    except Exception:
        offset = 0
    search = (search or "").strip()
    try:
        db = _hub_db()
        if db is None:
            return False, "DB tidak tersedia"
        con = db.get_conn()
        try:
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{name}")').fetchall()]
            if not cols:
                return False, f"tabel {name} tidak ada"
            where = ""
            params: list = []
            if search:
                conds = []
                for c in cols:
                    conds.append(f'CAST("{c}" AS TEXT) LIKE ?')
                    params.append(f"%{search}%")
                where = " WHERE " + " OR ".join(conds)
            total = con.execute(f'SELECT COUNT(*) FROM "{name}"' + where, params).fetchone()[0]
            rows = []
            q = f'SELECT * FROM "{name}"' + where + f' ORDER BY rowid DESC LIMIT ? OFFSET ?'
            q_params = params + [limit, offset]
            for r in con.execute(q, q_params):
                d = dict(r)
                # stringify bytes / json-ish untuk aman di JSON
                for k, v in list(d.items()):
                    if isinstance(v, bytes):
                        try:
                            d[k] = v.decode("utf-8", errors="replace")
                        except Exception:
                            d[k] = str(v)
                rows.append(d)
            return True, {"name": name, "columns": cols, "rows": rows, "total": int(total or 0),
                          "limit": limit, "offset": offset, "search": search}
        finally:
            try:
                con.close()
            except Exception:
                pass
    except Exception as e:
        return False, f"error: {e}"


def refresh_supervised_status():
    """Sinkronkan module_status dari poll() proses. Return nama yg berubah."""
    changed = []
    for name, alive in poll_modules().items():
        prev = module_status.get(name, {}).get("connected")
        if prev != alive:
            module_status[name] = {"connected": alive, "ts": ts()}
            changed.append(name)
    return changed

# ══════════════════════════════════════════════════════════════
# STATE
# ══════════════════════════════════════════════════════════════
clients = {}
log_history = []
entries_cache = []
entries_all_cache = {}  # (nota,bahan,sides,lam,test) -> entry terlihat hari ini
verified_cache = set()  # skey per-baris terverifikasi: "nota||bahan||sides||lam||1/0||proc"
kmma_timer_cache = None  # {next_in, interval} hitung mundur siklus KMMA
status_cache = {}  # skey "nota||bahan||sides||lam||1/0" -> {status,msg,ts}


def _rkey(nota, bahan="", sides="", laminating="", test=False, process_type=""):
    """Kunci status kanonis (sama dgn db.skey + dashboard)."""
    if isinstance(test, str):
        t = "1" if test.strip() in ("1", "true", "True", "TRUE", "yes", "Yes") else "0"
    else:
        t = "1" if test else "0"
    return "||".join([nota or "", bahan or "", sides or "", laminating or "", t, process_type or ""])


def _ekey_of(e):
    e = e or {}
    return (e.get("nota") or "", e.get("bahan") or "", e.get("sides") or "",
            e.get("laminating") or "", 1 if e.get("test") else 0,
            e.get("process_type") or "")


def _pkey(s):
    """'nota||..||1/0[||proc]' -> tuple. Legacy nota-only
    -> (nota,'','','',-1,'') dgn -1 = cocok SEMUA varian (kompatibel lama)."""
    try:
        if "||" not in (s or ""):
            return (s or "", "", "", "", -1, "")
        p = (s or "").split("||")
        while len(p) < 6:
            p.append("")
        return (p[0], p[1], p[2], p[3], 1 if p[4] == "1" else 0, p[5])
    except Exception:
        return (s or "", "", "", "", -1, "")


def _key_match(ek, sk):
    return ek[0] == sk[0] and (sk[1:] == ("", "", "", -1, "") or ek[1:] == sk[1:])
module_status = {}  # role → {"connected": bool, "ts": "..."}
step_cache = {}  # nota → [{name, machine, material, sdm, qty, status, msg}]
kma_cache = {"routes": [], "printers": [], "rules": [], "stats": {}, "seen": 0.0}
KMA_STALE_S = 90  # tanpa laporan > ini → KMA dianggap offline

HUB_LOOP = None  # event loop utama (untuk broadcast dari thread HTTP)


def ts():
    return datetime.datetime.now().strftime("%H:%M:%S")


async def _safe_send(ws, msg):
    try:
        await ws.send(msg)
    except Exception:
        pass


def broadcast(msg, roles=None):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            loop = asyncio.get_event_loop()
        except Exception:
            return
    for ws, info in list(clients.items()):
        if roles and info["role"] not in roles:
            continue
        try:
            loop.create_task(_safe_send(ws, msg))
        except Exception:
            pass


def broadcast_threadsafe(payload):
    """Broadcast dari thread lain (HTTP ingest)."""
    if HUB_LOOP is None:
        return
    for ws in list(clients.keys()):
        try:
            asyncio.run_coroutine_threadsafe(_safe_send(ws, payload), HUB_LOOP)
        except Exception:
            pass


def add_log(module, msg):
    entry = {"type": "log", "module": module, "msg": msg, "ts": ts()}
    log_history.append(entry)
    if len(log_history) > 200:
        log_history.pop(0)
    payload = json.dumps(entry, ensure_ascii=False)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        broadcast_threadsafe(payload)  # dipanggil dari thread HTTP
    else:
        broadcast(payload)


def apply_kma_state(data):
    """Terapkan laporan KMA (WS maupun HTTP). Return payload broadcast."""
    kma_cache["routes"] = (data.get("routes") or [])[:500]
    kma_cache["printers"] = data.get("printers") or []
    kma_cache["rules"] = data.get("rules") or []
    kma_cache["stats"] = data.get("stats") or {}
    kma_cache["seen"] = time.time()
    module_status["kma"] = {"connected": True, "ts": ts()}
    return json.dumps({"type": "kma_state", "routes": kma_cache["routes"],
                       "printers": kma_cache["printers"], "rules": kma_cache["rules"],
                       "stats": kma_cache["stats"]}, ensure_ascii=False)


def ingest_kma(data):
    """Terima laporan route.py via POST /ingest. Return True bila valid."""
    if not isinstance(data, dict) or data.get("source") != "kma":
        return False
    kind = data.get("kind", "kma_state")
    if kind == "log":
        add_log("KMA", str(data.get("msg", "")))
        return True
    if kind != "kma_state":
        return False
    payload = apply_kma_state(data)
    broadcast_threadsafe(payload)
    broadcast_threadsafe(json.dumps({"type": "module_status", "module": "kma",
                                     "connected": True, "ts": ts()}))
    return True


# ══════════════════════════════════════════════════════════════
# DASHBOARD HTML
# ══════════════════════════════════════════════════════════════
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NSH Hub Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}
.header{background:linear-gradient(135deg,#1e3a5f,#0f172a);padding:16px 24px;border-bottom:1px solid #334155;display:flex;align-items:center;gap:16px}
.header h1{font-size:20px;font-weight:700;color:#38bdf8}
.header .dots{display:flex;gap:12px;font-size:12px;align-items:center}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:4px}
.dot.on{background:#22c55e;box-shadow:0 0 6px #22c55e}
.dot.off{background:#ef4444}
.container{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:12px;height:calc(100vh - 60px)}
.panel{background:#1e293b;border-radius:8px;border:1px solid #334155;display:flex;flex-direction:column;overflow:hidden}
.panel-header{padding:10px 14px;border-bottom:1px solid #334155;font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.5px;display:flex;justify-content:space-between;align-items:center}
.panel-header.wma{color:#38bdf8}.panel-header.kmma{color:#a78bfa}.panel-header.kma{color:#34d399}
.panel-header.entries{color:#fbbf24}.panel-header.status{color:#34d399}
.panel-body{flex:1;overflow-y:auto;padding:8px 10px;font-size:11px;font-family:'Cascadia Code','Fira Code',monospace}
.log-entry{padding:2px 0;border-bottom:1px solid rgba(51,65,85,.3);line-height:1.4}
.log-entry .ts{color:#64748b;margin-right:6px}
.log-entry.WMA{color:#38bdf8}.log-entry.KMMA{color:#a78bfa}.log-entry.KMA{color:#34d399}.log-entry.HUB{color:#fbbf24}
.entry-card{background:#0f172a;border-radius:6px;padding:8px 10px;margin:3px 0;border-left:3px solid #334155}
.entry-card.ok{border-left-color:#22c55e}.entry-card.error{border-left-color:#ef4444}.entry-card.pending{border-left-color:#fbbf24}
.entry-card .nota{font-weight:700;color:#f8fafc;font-size:12px}
.entry-card .detail{color:#94a3b8;font-size:10px;margin-top:2px}
.badge{font-size:9px;padding:1px 5px;border-radius:3px;font-weight:600;margin-left:6px}
.badge.ok{background:#166534;color:#22c55e}.badge.error{background:#7f1d1d;color:#ef4444}.badge.pending{background:#713f12;color:#fbbf24}
.empty{color:#475569;text-align:center;padding:30px;font-size:12px}
@media(max-width:768px){.container{grid-template-columns:1fr;height:auto}}
</style>
</head>
<body>
<div class="header">
  <h1>NSH Hub</h1>
  <div class="dots">
    <span><span class="dot off" id="dot-lite"></span>Lite</span>
    <span><span class="dot off" id="dot-wma"></span>WMA</span>
    <span><span class="dot off" id="dot-kmma"></span>KMMA</span>
    <span><span class="dot off" id="dot-kma"></span>KMA</span>
  </div>
</div>
<div class="container">
  <div class="panel">
    <div class="panel-header wma"><span>WMA</span><span id="c-wma">0</span></div>
    <div class="panel-body" id="log-wma"><div class="empty">Connecting...</div></div>
  </div>
  <div class="panel">
    <div class="panel-header kmma"><span>KMMA</span><span id="c-kmma">0</span></div>
    <div class="panel-body" id="log-kmma"><div class="empty">Connecting...</div></div>
  </div>
  <div class="panel">
    <div class="panel-header kma"><span>KMA</span><span id="c-kma">0</span></div>
    <div class="panel-body" id="log-kma"><div class="empty">Connecting...</div></div>
  </div>
  <div class="panel">
    <div class="panel-header entries"><span>Entries</span><span id="c-ent">0</span></div>
    <div class="panel-body" id="list-ent"><div class="empty">Belum ada entries</div></div>
  </div>
  <div class="panel">
    <div class="panel-header status"><span>Status</span></div>
    <div class="panel-body" id="list-st"><div class="empty">Belum ada status</div></div>
  </div>
</div>
<script>
let ws;const L={WMA:[],KMMA:[],KMA:[]},E=[],S={},MX=120;
function connect(){
  const p=location.port==='8766'?'8765':location.port||'8765';
  const u=(location.protocol==='https:'?'wss://':'ws://')+location.hostname+':'+p;
  ws=new WebSocket(u);
  ws.onopen=()=>{
    ws.send(JSON.stringify({type:'subscribe',role:'dashboard'}));
  };
  ws.onclose=()=>{
    document.getElementById('dot-wma').className='dot off';
    document.getElementById('dot-kmma').className='dot off';
    setTimeout(connect,2000);
  };
  ws.onerror=()=>{};
  ws.onmessage=e=>handle(JSON.parse(e.data));
}
function handle(d){
  if(d.type==='log'){const m=d.module||'HUB';const a=L[m]||L.WMA;a.push(d);if(a.length>MX)a.shift();rLog(m)}
  if(d.type==='entries'){E.length=0;d.entries.forEach(e=>E.push(e));rEnt()}
  if(d.type==='status_update'){S[d.nota]={s:d.status,m:d.msg||'',t:d.ts||''};rSt()}
  if(d.type==='module_status'){const el=document.getElementById('dot-'+d.module);if(el)el.className=d.connected?'dot on':'dot off'}
  if(d.type==='init'){d.logs.forEach(l=>handle(l));E.length=0;if(d.entries)d.entries.forEach(e=>E.push(e));if(d.status)Object.assign(S,d.status);if(d.modules){Object.keys(d.modules).forEach(k=>{const m=d.modules[k];const el=document.getElementById('dot-'+k);if(el)el.className=m.connected?'dot on':'dot off'})}rEnt();rSt()}
}
function rLog(m){const el=document.getElementById('log-'+m.toLowerCase());if(!el)return;const a=L[m]||[];el.innerHTML=a.map(l=>'<div class="log-entry '+m+'"><span class="ts">'+l.ts+'</span>'+esc(l.msg)+'</div>').join('');el.scrollTop=el.scrollHeight;document.getElementById('c-'+m.toLowerCase()).textContent=a.length}
function rEnt(){const el=document.getElementById('list-ent');if(!E.length){el.innerHTML='<div class="empty">Belum ada entries</div>';return}el.innerHTML=E.map(e=>{const s=S[e.nota];const c=s?(s.s==='ok'?'ok':s.s==='error'?'error':'pending'):'pending';const b=s?'<span class="badge '+c+'">'+s.s.toUpperCase()+'</span>':'<span class="badge pending">PENDING</span>';return'<div class="entry-card '+c+'"><div class="nota">'+e.nota+b+'</div><div class="detail">'+esc(e.bahan||'')+' x '+(e.total_qty||'')+' | '+(e.machine||'')+' | '+(e.operator||'')+'</div></div>'}).join('');document.getElementById('c-ent').textContent=E.length}
function rSt(){const el=document.getElementById('list-st');const k=Object.keys(S);if(!k.length){el.innerHTML='<div class="empty">Belum ada status</div>';return}el.innerHTML=k.map(n=>{const s=S[n];const c=s.s==='ok'?'ok':s.s==='error'?'error':'pending';return'<div class="entry-card '+c+'"><div class="nota">'+n+' <span class="badge '+c+'">'+s.s.toUpperCase()+'</span></div><div class="detail">'+esc(s.m)+' ('+s.t+')</div></div>'}).join('');el.scrollTop=el.scrollHeight}
function esc(s){return(s||'').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
connect();
</script>
</body>
</html>"""


def _resolve_output_file(name):
    """Resolve nama file OUTPUT (basename .pdf tervalidasi) ke path nyata.

    Cari di output_folder lite, fallback jalan di sekitar parent
    (aaDevelop — file hari sebelumnya yg masih di list).
    Raise FileNotFoundError bila tak ketemu.
    Dipakai /api/output-file dan /api/output-info agar resolusi tunggal."""
    lite_cfg = _read_folders_file("lite")
    outdir = os.path.realpath((lite_cfg.get("output_folder") or "").strip())
    if not outdir or not os.path.isdir(outdir):
        raise FileNotFoundError
    fpath = os.path.realpath(os.path.join(outdir, name))
    if not fpath.startswith(outdir + os.sep) or not os.path.isfile(fpath):
        # fallback: cari di sekitar aaDevelop (mis. file dari hari sebelumnya masih di list)
        parent = os.path.dirname(outdir)
        found = None
        try:
            for r, dirs, files in os.walk(parent):
                if name in files:
                    cand = os.path.realpath(os.path.join(r, name))
                    if cand.startswith(os.path.realpath(parent) + os.sep) and os.path.isfile(cand):
                        found = cand
                        break
                # batasi kedalaman agar tidak scan terlalu jauh
                if r.count(os.sep) - parent.count(os.sep) > 3:
                    dirs[:] = []
        except Exception:
            found = None
        if found:
            fpath = found
        else:
            raise FileNotFoundError
    return fpath


def _read_output_pdf_info(fpath):
    """Jumlah halaman + dimensi halaman pertama (pt). Gagal parse → (0, 0, 0).
    Import PyPDF2 lazy (di dalam fungsi) agar lib PDF yg rusak/hilang
    tak pernah mencegah hub startup."""
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(fpath)
        n = len(reader.pages)
        if n <= 0:
            return 0, 0, 0
        box = reader.pages[0].mediabox
        w = int(round(float(box.width)))
        h = int(round(float(box.height)))
        return n, w, h
    except Exception:
        return 0, 0, 0


def _validate_output_pages_arg(pages_raw, total):
    """Validasi argumen pages 1-based dari JSON. Return (ok, sorted_list | msg_id)."""
    if not isinstance(pages_raw, list) or not pages_raw:
        return False, "halaman kosong (isi nomor halaman dulu)"
    nums = []
    for p in pages_raw:
        # bool adalah subclass int di Python — tolak tegas
        if isinstance(p, bool) or not isinstance(p, int):
            return False, "nomor halaman harus bilangan bulat"
        nums.append(p)
    if any(p < 1 or p > total for p in nums):
        return False, f"halaman harus 1..{total}"
    return True, sorted(set(nums))


def _ensure_output_backup(fpath):
    """Backup sekali: salin fpath -> fpath+'.bak' HANYA bila .bak belum ada.

    Tanpa ekstensi .pdf agar file backup tak pernah muncul di listing OUTPUT.
    Return (ok, msg). Bila backup gagal, pemanggil WAJIB batal (jangan ubah file)."""
    try:
        bak = fpath + ".bak"
        if os.path.exists(bak):
            return True, "bak sudah ada"
        shutil.copy2(fpath, bak)
        return True, "ok"
    except Exception as e:
        return False, f"gagal backup: {e}"


def _output_apply_bw(fpath, pages_raw):
    """Ubah halaman target jadi grayscale (raster 300 DPI + kurva gamma gray 2.2).

    Pola andal: bangun PDF BARU di memori — halaman target diganti raster gray
    seukuran mediabox asli, halaman lain disalin lossless via insert_pdf —
    lalu simpan menimpa fpath (garbage=4, deflate=True).
    Return (status, msg): status 'ok'|'bad'|'err' -> HTTP 200|400|500."""
    try:
        import fitz  # PyMuPDF, lazy agar hub tetap jalan tanpanya
        from PIL import Image
        import io
    except Exception as e:
        return "err", f"lib PDF tak tersedia: {e}"
    # Kurva gamma gray 2.2 (LUT 256 entri): out = round(255*(in/255)**2.2).
    # Hitam murni (0) tetap 0 = perilaku preserve-black: teks/garis hitam pekat
    # tidak jadi abu-abu; midtone digelapkan agar hasil cetak BW bersih.
    try:
        lut = [min(255, max(0, int(round(255 * ((i / 255.0) ** 2.2))))) for i in range(256)]
    except Exception as e:
        return "err", f"gagal hitung kurva: {e}"
    src = None
    newdoc = None
    try:
        try:
            src = fitz.open(fpath)
        except Exception as e:
            return "err", f"gagal baca PDF: {e}"
        try:
            total = len(src)
        except Exception:
            total = 0
        if total <= 0:
            return "bad", "PDF kosong (0 halaman)"
        ok, res = _validate_output_pages_arg(pages_raw, total)
        if not ok:
            return "bad", res
        targets = set(res)
        try:
            newdoc = fitz.open()
            for i in range(total):
                if (i + 1) in targets:
                    pg = src[i]
                    w, h = pg.rect.width, pg.rect.height
                    pix = pg.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72),
                                        colorspace=fitz.csGRAY)
                    _done = False
                    try:
                        import numpy as _np
                    except Exception:
                        _np = None
                    if _np is not None:
                        try:
                            _lut_np = _np.array(lut, dtype=_np.uint8)
                            _raw = _np.frombuffer(pix.samples, dtype=_np.uint8).copy()
                            if pix.alpha:
                                _px = _raw.reshape(-1, pix.n)
                                _px[:, 0] = _lut_np[_px[:, 0]]
                                _gray = _px.tobytes()
                            else:
                                _gray = _lut_np[_raw].tobytes()
                            _gray_pix = fitz.Pixmap(fitz.csGRAY, pix.w, pix.h, _gray, pix.alpha)
                            np = newdoc.new_page(width=w, height=h)
                            np.insert_image(np.rect, pixmap=_gray_pix)
                            _done = True
                        except Exception:
                            _done = False
                    if not _done:
                        img = Image.open(io.BytesIO(pix.tobytes("png")))
                        if img.mode != "L":
                            img = img.convert("L")
                        img = img.point(lut)
                        # Buang profil ICC bawaan agar PNG tanpa chunk iCCP: Konica membaca objek ICCBased sebagai halaman berwarna, jadi harus DeviceGray murni.
                        img.info.pop("icc_profile", None)
                        buf = io.BytesIO()
                        img.save(buf, format="PNG")
                        np = newdoc.new_page(width=w, height=h)
                        np.insert_image(np.rect, stream=buf.getvalue())
                    # Paksa DeviceGray murni: insert_image PyMuPDF menyisipkan ICCBased (Alternate DeviceGray) yang dibaca Konica sebagai Full Color, jadi ganti ColorSpace xref jadi /DeviceGray langsung.
                    try:
                        for _im in np.get_images(full=True):
                            try:
                                if _im[5] != "DeviceGray":
                                    newdoc.xref_set_key(_im[0], "ColorSpace", "/DeviceGray")
                            except Exception:
                                pass
                    except Exception:
                        pass
                else:
                    newdoc.insert_pdf(src, from_page=i, to_page=i)
        except Exception as e:
            return "err", f"gagal konversi BW: {e}"
        ok, bmsg = _ensure_output_backup(fpath)
        if not ok:
            return "err", bmsg
        try:
            src.close()
            src = None
        except Exception:
            src = None
        try:
            newdoc.save(fpath, garbage=4, deflate=True)
        except Exception as e:
            return "err", f"gagal simpan PDF: {e}"
        return "ok", f"BW halaman {', '.join(map(str, sorted(targets)))} ({len(targets)} hlm) dit timpa: {os.path.basename(fpath)} (tetap PDF, nama sama)"
    except Exception as e:
        return "err", f"gagal BW: {e}"
    finally:
        for d in (newdoc, src):
            try:
                if d is not None:
                    d.close()
            except Exception:
                pass


def _output_delete_pages(fpath, pages_raw):
    """Hapus halaman target (1-based). Menolak bila sisa 0 halaman.

    Simpan via file sementara + os.replace (aman di Windows: file sumber yang
    sedang terbuka tak bisa ditimpa langsung).
    Return (status, msg): status 'ok'|'bad'|'err' -> HTTP 200|400|500."""
    try:
        import fitz  # PyMuPDF, lazy agar hub tetap jalan tanpanya
    except Exception as e:
        return "err", f"lib PDF tak tersedia: {e}"
    doc = None
    tmp = fpath + ".tmp"
    try:
        try:
            doc = fitz.open(fpath)
        except Exception as e:
            return "err", f"gagal baca PDF: {e}"
        try:
            total = len(doc)
        except Exception:
            total = 0
        if total <= 0:
            return "bad", "PDF kosong (0 halaman)"
        ok, res = _validate_output_pages_arg(pages_raw, total)
        if not ok:
            return "bad", res
        targets = res  # sorted unik 1-based
        if len(targets) >= total:
            return "bad", "tidak bisa hapus semua halaman"
        zero = [p - 1 for p in targets]
        ok, bmsg = _ensure_output_backup(fpath)
        if not ok:
            return "err", bmsg
        try:
            doc.delete_pages(zero)
        except Exception:
            # fallback versi lama: hapus satu-satu dari belakang
            for pno in sorted(zero, reverse=True):
                doc.delete_page(pno)
        try:
            doc.save(tmp, garbage=4, deflate=True)
        except Exception as e:
            return "err", f"gagal simpan PDF: {e}"
        try:
            doc.close()
            doc = None
        except Exception:
            doc = None
        try:
            os.replace(tmp, fpath)
        except Exception as e:
            return "err", f"gagal simpan PDF: {e}"
        sisa = total - len(targets)
        return "ok", f"hapus {len(targets)} halaman, sisa {sisa} — dit timpa: {os.path.basename(fpath)} (tetap PDF, nama sama)"
    except Exception as e:
        return "err", f"gagal hapus halaman: {e}"
    finally:
        try:
            if doc is not None:
                doc.close()
        except Exception:
            pass
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════
# HTTP SERVER (dashboard only, separate thread)
# ══════════════════════════════════════════════════════════════
class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def _cors_headers(self):
        origin = self.headers.get("Origin")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        else:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Credentials", "true")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def _send_json(self, code, obj, set_cookie=None, clear_cookie=False):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors_headers()
        self.send_header("Content-Length", str(len(body)))
        if set_cookie:
            tok, max_age = set_cookie
            self.send_header("Set-Cookie", f"nsh_token={tok}; HttpOnly; Path=/; Max-Age={max_age}; SameSite=Lax")
        if clear_cookie:
            self.send_header("Set-Cookie", "nsh_token=; HttpOnly; Path=/; Max-Age=0; SameSite=Lax")
        self.end_headers()
        self.wfile.write(body)

    def _get_token(self):
        # Cookie, Authorization, ?token=
        tok = _auth_token_from_headers(self.headers)
        if tok:
            return tok
        try:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            t = (qs.get("token") or [""])[0]
            if t:
                return t.strip()
        except Exception:
            pass
        return ""

    def _require_auth(self, admin_only=False):
        tok = self._get_token()
        sess = _auth_lookup(tok)
        if not sess:
            return None, False
        if admin_only and sess["user"]["role"] != "admin":
            return sess, False
        return sess, True

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        # ── Auth endpoints ──────────────────────────────────────────
        if parsed.path == "/api/me":
            tok = self._get_token()
            sess = _auth_lookup(tok)
            if not sess:
                self._send_json(401, {"ok": False, "msg": "unauthorized"})
                return
            self._send_json(200, {"ok": True, "user": sess["user"], "expires_at": sess["expires_at"], "remember": sess["remember"]})
            return
        if parsed.path == "/api/users":
            sess, ok = self._require_auth(admin_only=True)
            if sess is None:
                self._send_json(401, {"ok": False, "msg": "unauthorized"})
                return
            if not ok:
                self._send_json(403, {"ok": False, "msg": "admin only"})
                return
            try:
                db = _hub_db()
                users = db.user_list() if db else []
                # sanitize: only username/role/created
                out = [{"username": u["username"], "role": u["role"], "created_at": u["created_at"], "created_by": u["created_by"]} for u in users]
                self._send_json(200, {"ok": True, "users": out})
                return
            except Exception as e:
                self._send_json(500, {"ok": False, "msg": str(e)})
                return
        # ── DB browse (read-only, like phpMyAdmin) ─────────────────
        if parsed.path == "/api/db/tables":
            tok = self._get_token()
            if not _auth_lookup(tok):
                self._send_json(401, {"ok": False, "msg": "unauthorized"})
                return
            try:
                tables = _db_list_tables()
                body = json.dumps({"ok": True, "tables": tables}, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._cors_headers()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            except Exception as e:
                body = json.dumps({"ok": False, "msg": str(e)}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self._cors_headers()
                self.end_headers()
                self.wfile.write(body)
                return
        if parsed.path == "/api/db/table":
            tok = self._get_token()
            if not _auth_lookup(tok):
                self._send_json(401, {"ok": False, "msg": "unauthorized"})
                return
            qs = urllib.parse.parse_qs(parsed.query)
            name = (qs.get("name", [""])[0] or "").strip()
            search = (qs.get("search", [""])[0] or "").strip()
            try:
                limit = int(qs.get("limit", ["20"])[0] or 20)
            except Exception:
                limit = 20
            try:
                offset = int(qs.get("offset", ["0"])[0] or 0)
            except Exception:
                offset = 0
            ok, payload = _db_query_table(name, limit, offset, search)
            body = json.dumps({"ok": ok, **(payload if ok else {"msg": payload})}, ensure_ascii=False).encode()
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._cors_headers()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/we/state":
            # WE state (user boleh): snapshot tanggal tsb dari DB + mode/override memori
            tok = self._get_token()
            if not _auth_lookup(tok):
                self._send_json(401, {"ok": False, "msg": "unauthorized"})
                return
            qs = urllib.parse.parse_qs(parsed.query)
            date_q = (qs.get("date", [""])[0] or "").strip()
            self._send_json(200, _we_http_state(date_q or datetime.date.today().isoformat()))
            return
        if parsed.path == "/api/we/files":
            # WE files paginasi (user boleh): ?state=seen|printed&date=&limit=&offset=&search=
            tok = self._get_token()
            if not _auth_lookup(tok):
                self._send_json(401, {"ok": False, "msg": "unauthorized"})
                return
            qs = urllib.parse.parse_qs(parsed.query)
            try:
                lim = int((qs.get("limit", ["20"])[0] or 20))
            except Exception:
                lim = 20
            try:
                off = int((qs.get("offset", ["0"])[0] or 0))
            except Exception:
                off = 0
            ok, payload = _we_query_files((qs.get("state", ["seen"])[0] or "seen"),
                                          (qs.get("date", [""])[0] or ""),
                                          lim, off,
                                          (qs.get("search", [""])[0] or ""))
            if ok:
                self._send_json(200, {"ok": True, **payload})
            else:
                self._send_json(400, {"ok": False, "msg": str(payload)})
            return
        if parsed.path == "/api/output-file":
            tok = self._get_token()
            if not _auth_lookup(tok):
                self._send_json(401, {"ok": False, "msg": "unauthorized"})
                return
            qs = urllib.parse.parse_qs(parsed.query)
            name = (qs.get("name", [""])[0] or "").strip()
            # validasi ketat: basename saja, .pdf
            if not name or name != os.path.basename(name) or not name.lower().endswith(".pdf"):
                self.send_response(400)
                self._cors_headers()
                self.end_headers()
                self.wfile.write(b"nama file tidak valid")
                return
            try:
                fpath = _resolve_output_file(name)
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Length", str(os.path.getsize(fpath)))
                self.send_header("Content-Disposition", f'inline; filename="{name}"')
                self._cors_headers()
                self.end_headers()
                with open(fpath, "rb") as fh:
                    shutil.copyfileobj(fh, self.wfile)
                return
            except FileNotFoundError:
                self.send_response(404)
                self._cors_headers()
                self.end_headers()
                self.wfile.write("file tidak ditemukan di OUTPUT (mungkin sudah dipindah ke DEVELOP/RICOH atau ganti hari)".encode())
                return
            except Exception as e:
                self.send_response(500)
                self._cors_headers()
                self.end_headers()
                self.wfile.write(f"error: {e}".encode()[:200])
                return
        if parsed.path == "/api/output-info":
            try:
                tok = self._get_token()
                if not _auth_lookup(tok):
                    self._send_json(401, {"ok": False, "msg": "unauthorized"})
                    return
                qs = urllib.parse.parse_qs(parsed.query)
                name = (qs.get("name", [""])[0] or "").strip()
                # validasi ketat: basename saja, .pdf (sama spt /api/output-file)
                if not name or name != os.path.basename(name) or not name.lower().endswith(".pdf"):
                    self._send_json(400, {"ok": False, "msg": "nama file tidak valid"})
                    return
                try:
                    fpath = _resolve_output_file(name)
                except FileNotFoundError:
                    self._send_json(404, {"ok": False, "msg": "file tidak ditemukan di OUTPUT (mungkin sudah dipindah ke DEVELOP/RICOH atau ganti hari)"})
                    return
                try:
                    size = os.path.getsize(fpath)
                except Exception:
                    size = 0
                pages, w, h = _read_output_pdf_info(fpath)
                self._send_json(200, {"ok": True, "pages": pages, "width_pt": w, "height_pt": h, "size": size})
                return
            except Exception as e:
                try:
                    self._send_json(500, {"ok": False, "msg": f"error: {e}"[:200]})
                except Exception:
                    pass
                return
        if parsed.path == "/api/output-page":
            # Render 1 halaman PDF jadi PNG (preview tanpa scroll: tanpa PDF viewer
            # browser, <img> tidak bisa di-scroll). page = 1-based.
            try:
                tok = self._get_token()
                if not _auth_lookup(tok):
                    self._send_json(401, {"ok": False, "msg": "unauthorized"})
                    return
                qs = urllib.parse.parse_qs(parsed.query)
                name = (qs.get("name", [""])[0] or "").strip()
                if not name or name != os.path.basename(name) or not name.lower().endswith(".pdf"):
                    self._send_json(400, {"ok": False, "msg": "nama file tidak valid"})
                    return
                try:
                    page = max(1, int((qs.get("page", ["1"])[0] or "1").strip() or "1"))
                except Exception:
                    page = 1
                try:
                    fpath = _resolve_output_file(name)
                except FileNotFoundError:
                    self._send_json(404, {"ok": False, "msg": "file tidak ditemukan di OUTPUT (mungkin sudah dipindah ke DEVELOP/RICOH atau ganti hari)"})
                    return
                try:
                    import fitz  # PyMuPDF, lazy agar hub tetap jalan tanpanya
                    doc = fitz.open(fpath)
                    try:
                        if page > len(doc):
                            page = len(doc)
                        pix = doc[page - 1].get_pixmap(matrix=fitz.Matrix(1.6, 1.6))
                        png = pix.tobytes("png")
                    finally:
                        doc.close()
                except Exception as e:
                    self._send_json(500, {"ok": False, "msg": f"gagal render: {e}"[:200]})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(png)))
                self.send_header("Cache-Control", "private, max-age=300")
                self._cors_headers()
                self.end_headers()
                self.wfile.write(png)
                return
            except Exception as e:
                try:
                    self._send_json(500, {"ok": False, "msg": f"error: {e}"[:200]})
                except Exception:
                    pass
                return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._cors_headers()
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(DASHBOARD_HTML.encode())

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        # ── Auth: login ───────────────────────────────────────────
        if path == "/api/login":
            ip = _auth_ip(self.client_address[0] if self.client_address else "")
            if not _auth_rate_ok(ip):
                self._send_json(429, {"ok": False, "msg": "terlalu banyak percobaan, coba 1 menit lagi"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length).decode("utf-8") if length else "{}"
                data = json.loads(raw) if raw.strip() else {}
            except Exception:
                self._send_json(400, {"ok": False, "msg": "json tidak valid"})
                return
            username = (data.get("username") or "").strip()
            password = (data.get("password") or "")
            remember = bool(data.get("remember"))
            try:
                # normalize + validate
                db_tmp = _hub_db()
                if db_tmp is None:
                    raise RuntimeError("DB tidak tersedia")
                # inline validate to give rate-limited msg before db
                n = db_tmp.normalize_username(username) if hasattr(db_tmp, "normalize_username") else username.strip().lower()
                if not username or not password:
                    raise ValueError("username & password wajib")
                if len(password) < 6:
                    raise ValueError("password min 6")
                u = db_tmp.user_get_by_username(username)
                if not u or not db_tmp.verify_password(password, u["pass_hash"]):
                    self._send_json(401, {"ok": False, "msg": "username / password salah"})
                    return
                token, exp, max_age = db_tmp.session_create(u["id"], remember)
                must_change = (u["username"] == "admin" and password == "admin123")
                self._send_json(200, {"ok": True, "token": token, "role": u["role"], "username": u["username"], "expires_at": exp, "must_change": must_change}, set_cookie=(token, max_age))
                return
            except ValueError as ve:
                self._send_json(400, {"ok": False, "msg": str(ve)})
                return
            except Exception as e:
                self._send_json(500, {"ok": False, "msg": f"error: {e}"})
                return
        if path == "/api/logout":
            tok = self._get_token()
            try:
                body_len = int(self.headers.get("Content-Length", 0) or 0)
                if body_len:
                    try:
                        braw = self.rfile.read(body_len).decode("utf-8")
                        j = json.loads(braw) if braw.strip() else {}
                        if j.get("token"):
                            tok = str(j.get("token")).strip() or tok
                    except Exception:
                        pass
            except Exception:
                pass
            if tok:
                try:
                    db = _hub_db()
                    if db:
                        db.session_delete(tok)
                except Exception:
                    pass
            self._send_json(200, {"ok": True, "msg": "logout"}, clear_cookie=True)
            return
        # users CRUD (admin only)
        if path == "/api/users":
            sess, ok = self._require_auth(admin_only=True)
            if sess is None:
                self._send_json(401, {"ok": False, "msg": "unauthorized"})
                return
            if not ok:
                self._send_json(403, {"ok": False, "msg": "admin only"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length).decode("utf-8") if length else "{}"
                data = json.loads(raw) if raw.strip() else {}
            except Exception:
                self._send_json(400, {"ok": False, "msg": "json tidak valid"})
                return
            try:
                username = (data.get("username") or "").strip()
                password = (data.get("password") or "")
                role = (data.get("role") or "user").strip().lower()
                db = _hub_db()
                res = db.user_create(username, password, role, created_by=sess["user"]["username"])
                self._send_json(200, {"ok": True, "user": res})
                return
            except ValueError as ve:
                self._send_json(400, {"ok": False, "msg": str(ve)})
                return
            except Exception as e:
                self._send_json(500, {"ok": False, "msg": str(e)})
                return
        if path == "/api/users/reset":
            sess, ok = self._require_auth(admin_only=True)
            if sess is None:
                self._send_json(401, {"ok": False, "msg": "unauthorized"})
                return
            if not ok:
                self._send_json(403, {"ok": False, "msg": "admin only"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length).decode("utf-8") if length else "{}"
                data = json.loads(raw) if raw.strip() else {}
            except Exception:
                self._send_json(400, {"ok": False, "msg": "json tidak valid"})
                return
            username = (data.get("username") or "").strip()
            new_password = (data.get("password") or data.get("new_password") or "")
            if not username or not new_password:
                self._send_json(400, {"ok": False, "msg": "username & password wajib"})
                return
            try:
                db = _hub_db()
                if not db.user_get_by_username(username):
                    self._send_json(404, {"ok": False, "msg": "user tidak ada"})
                    return
                db.user_set_password(username, new_password)
                self._send_json(200, {"ok": True, "msg": "password direset"})
                return
            except ValueError as ve:
                self._send_json(400, {"ok": False, "msg": str(ve)})
                return
            except Exception as e:
                self._send_json(500, {"ok": False, "msg": str(e)})
                return
        if path == "/api/users/delete":
            sess, ok = self._require_auth(admin_only=True)
            if sess is None:
                self._send_json(401, {"ok": False, "msg": "unauthorized"})
                return
            if not ok:
                self._send_json(403, {"ok": False, "msg": "admin only"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length).decode("utf-8") if length else "{}"
                data = json.loads(raw) if raw.strip() else {}
            except Exception:
                self._send_json(400, {"ok": False, "msg": "json tidak valid"})
                return
            username = (data.get("username") or "").strip()
            if not username:
                self._send_json(400, {"ok": False, "msg": "username wajib"})
                return
            if username.strip().lower() == sess["user"]["username"].lower():
                self._send_json(400, {"ok": False, "msg": "tidak bisa hapus diri sendiri"})
                return
            try:
                db = _hub_db()
                if not db.user_get_by_username(username):
                    self._send_json(404, {"ok": False, "msg": "user tidak ada"})
                    return
                db.user_delete(username)
                self._send_json(200, {"ok": True, "msg": "user dihapus"})
                return
            except Exception as e:
                self._send_json(500, {"ok": False, "msg": str(e)})
                return
        if path.startswith("/api/users/") and len(path) > len("/api/users/"):
            pass
        if path == "/api/db/exec":
            sess, ok = self._require_auth(admin_only=True)
            if sess is None:
                self._send_json(401, {"ok": False, "msg": "unauthorized"})
                return
            if not ok:
                self._send_json(403, {"ok": False, "msg": "admin only"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(length).decode("utf-8") if length else "{}"
                data = json.loads(raw) if raw.strip() else {}
            except Exception:
                self._send_json(400, {"ok": False, "msg": "json tidak valid"})
                return
            sql = (data.get("sql") or "").strip()
            if not sql:
                self._send_json(400, {"ok": False, "msg": "sql kosong"})
                return
            ok2, payload = _db_exec_sql(sql)
            if ok2:
                self._send_json(200, {"ok": True, **payload})
            else:
                self._send_json(400, {"ok": False, "msg": str(payload)})
            return
        if path == "/api/we/rescan":
            # WE rescan paksa (ADMIN ONLY, gaya gate spt /api/db/exec)
            sess, ok = self._require_auth(admin_only=True)
            if sess is None:
                self._send_json(401, {"ok": False, "msg": "unauthorized"})
                return
            if not ok:
                self._send_json(403, {"ok": False, "msg": "admin only"})
                return
            try:
                res = _we_scan_once()
            except Exception as e:
                self._send_json(500, {"ok": False, "msg": f"error: {e}"[:200]})
                return
            self._send_json(200, {"ok": True, **res})
            return
        if path == "/api/we/auto":
            # WE auto/manual (ADMIN ONLY): {"mode":"auto"|"manual"} — hapus/set override
            sess, ok = self._require_auth(admin_only=True)
            if sess is None:
                self._send_json(401, {"ok": False, "msg": "unauthorized"})
                return
            if not ok:
                self._send_json(403, {"ok": False, "msg": "admin only"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(length).decode("utf-8") if length else "{}"
                data = json.loads(raw) if raw.strip() else {}
            except Exception:
                self._send_json(400, {"ok": False, "msg": "json tidak valid"})
                return
            mode = str((data.get("mode") or "")).strip().lower()
            if mode not in ("auto", "manual"):
                self._send_json(400, {"ok": False, "msg": 'mode harus "auto"|"manual"'})
                return
            _we_set_override(mode == "manual", reason="we_auto admin")
            self._send_json(200, {"ok": True, "mode": we_auto.get("mode") or "wait",
                                  "override": bool(we_auto.get("override"))})
            return
        if path == "/api/we/mark-printed":
            # WE tandai manual Printed (USER boleh — gate spt /api/output-bw).
            # JSON {filename}: basename .pdf yg SUDAH terlihat hari ini (we_seen,
            # cegah ghost). Tulis via db.we_printed_mark + segar/siar ulang.
            try:
                tok = self._get_token()
                if not _auth_lookup(tok):
                    self._send_json(401, {"ok": False, "msg": "unauthorized"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", 0) or 0)
                    raw = self.rfile.read(length).decode("utf-8") if length else "{}"
                    data = json.loads(raw) if raw.strip() else {}
                except Exception:
                    self._send_json(400, {"ok": False, "msg": "json tidak valid"})
                    return
                fn = (data.get("filename") or "") if isinstance(data, dict) else ""
                fn = str(fn).strip()
                if not fn or fn != os.path.basename(fn) or not fn.lower().endswith(".pdf"):
                    self._send_json(400, {"ok": False, "msg": "filename harus basename .pdf"})
                    return
                # Opsional (batch WE): operator/mesin perekam; absen -> default lama
                # ("" / "Manual") agar kompatibel mundur; potong 40 karakter.
                try:
                    op = str(data.get("operator") or "").strip()[:40]
                except Exception:
                    op = ""
                try:
                    mc = str(data.get("machine") or "").strip()[:40] or "Manual"
                except Exception:
                    mc = "Manual"
                try:
                    _d, iso = _we_norm_date(datetime.date.today())
                except Exception:
                    iso = datetime.date.today().isoformat()
                db = None
                try:
                    db = _hub_db()
                    if db is None:
                        self._send_json(500, {"ok": False, "msg": "DB tidak tersedia"})
                        return
                    try:
                        seen = db.we_seen_day(iso) or []
                    except Exception:
                        seen = []
                    if not any(isinstance(r, dict) and r.get("filename") == fn for r in seen):
                        self._send_json(404, {"ok": False, "msg": "file belum terlihat hari ini (we_seen)"})
                        return
                    pr = _we_parse_filename(fn)
                    now_s = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    db.we_printed_mark(iso, [{
                        "filename": fn, "nota": pr.get("nota") or "",
                        "bahan": pr.get("bahan") or "", "sides": pr.get("sides") or "",
                        "laminating": pr.get("laminating") or "",
                        "test": pr.get("test") or False,
                        "process_type": pr.get("process_type") or "",
                        "output_name": fn, "printed_ts": now_s,
                        "operator": op, "machine": mc}])
                except Exception as e:
                    self._send_json(500, {"ok": False, "msg": f"error: {e}"[:200]})
                    return
                try:
                    _we_refresh_announce(iso, db)
                except Exception:
                    pass
                try:
                    add_log("WE", f"manual printed: {fn}")
                except Exception:
                    pass
                self._send_json(200, {"ok": True, "msg": f"{fn} ditandai printed"})
                return
            except Exception as e:
                try:
                    self._send_json(500, {"ok": False, "msg": f"error: {e}"[:200]})
                except Exception:
                    pass
                return
        if path == "/api/output-bw" or path == "/api/output-delpages":
            # Edit ringan halaman PDF OUTPUT (dipakai modal VIEW NSH Lite).
            try:
                tok = self._get_token()
                if not _auth_lookup(tok):
                    self._send_json(401, {"ok": False, "msg": "unauthorized"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", 0) or 0)
                    raw = self.rfile.read(length).decode("utf-8") if length else "{}"
                    data = json.loads(raw) if raw.strip() else {}
                except Exception:
                    self._send_json(400, {"ok": False, "msg": "json tidak valid"})
                    return
                name = (data.get("name") or "").strip() if isinstance(data, dict) else ""
                pages_arg = data.get("pages") if isinstance(data, dict) else None
                # validasi nama persis seperti /api/output-page
                if not name or name != os.path.basename(name) or not name.lower().endswith(".pdf"):
                    self._send_json(400, {"ok": False, "msg": "nama file tidak valid"})
                    return
                try:
                    fpath = _resolve_output_file(name)
                except FileNotFoundError:
                    self._send_json(404, {"ok": False, "msg": "file tidak ditemukan di OUTPUT (mungkin sudah dipindah ke DEVELOP/RICOH atau ganti hari)"})
                    return
                if path == "/api/output-bw":
                    st, msg = _output_apply_bw(fpath, pages_arg)
                else:
                    st, msg = _output_delete_pages(fpath, pages_arg)
                code = 200 if st == "ok" else (400 if st == "bad" else 500)
                self._send_json(code, {"ok": st == "ok", "msg": msg})
                return
            except Exception as e:
                try:
                    self._send_json(500, {"ok": False, "msg": f"error: {e}"[:200]})
                except Exception:
                    pass
                return
        if path == "/ingest" or path.startswith("/ingest"):
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                data = None
            ok = ingest_kma(data)
            body = json.dumps({"ok": ok}).encode()
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/users"):
            sess, ok = self._require_auth(admin_only=True)
            if sess is None:
                self._send_json(401, {"ok": False, "msg": "unauthorized"})
                return
            if not ok:
                self._send_json(403, {"ok": False, "msg": "admin only"})
                return
            # username from path /api/users/<name> or query ?username=
            username = ""
            if len(path) > len("/api/users/"):
                username = urllib.parse.unquote(path[len("/api/users/"):]).strip()
            if not username:
                qs = urllib.parse.parse_qs(parsed.query)
                username = (qs.get("username") or [""])[0].strip()
            if not username:
                try:
                    length = int(self.headers.get("Content-Length", 0) or 0)
                    if length:
                        raw = self.rfile.read(length).decode("utf-8")
                        j = json.loads(raw) if raw.strip() else {}
                        username = (j.get("username") or "").strip()
                except Exception:
                    pass
            if not username:
                self._send_json(400, {"ok": False, "msg": "username wajib"})
                return
            if username.strip().lower() == sess["user"]["username"].lower():
                self._send_json(400, {"ok": False, "msg": "tidak bisa hapus diri sendiri"})
                return
            try:
                db = _hub_db()
                if not db.user_get_by_username(username):
                    self._send_json(404, {"ok": False, "msg": "user tidak ada"})
                    return
                db.user_delete(username)
                self._send_json(200, {"ok": True, "msg": "user dihapus"})
                return
            except Exception as e:
                self._send_json(500, {"ok": False, "msg": str(e)})
                return
        if path == "/api/we/file":
            # WE hapus file MONITORING + baris DB hari ini (ADMIN ONLY — gate spt
            # /api/we/rescan). Permanen (tanpa trash); tolak subpath & non-PDF;
            # tak pernah sentuh luar MONITORING; tak pernah raise (500 JSON).
            try:
                sess, ok = self._require_auth(admin_only=True)
                if sess is None:
                    self._send_json(401, {"ok": False, "msg": "unauthorized"})
                    return
                if not ok:
                    self._send_json(403, {"ok": False, "msg": "admin only"})
                    return
                qs = urllib.parse.parse_qs(parsed.query)
                name = ((qs.get("name") or [""])[0] or "").strip()
                if not name or name != os.path.basename(name) or not name.lower().endswith(".pdf"):
                    self._send_json(400, {"ok": False, "msg": "name harus basename .pdf"})
                    return
                mon = (we_auto.get("monitoring") or "").strip()
                if not mon:
                    self._send_json(400, {"ok": False, "msg": "folder MONITORING belum tersedia"})
                    return
                try:
                    mon_real = os.path.realpath(mon)
                    rp = os.path.realpath(os.path.join(mon, name))
                except Exception:
                    self._send_json(400, {"ok": False, "msg": "path tidak valid"})
                    return
                # level-atas saja: dirname hasil resolve harus persis monitoring
                # (menolak ../x.pdf, sub/y.pdf, symlink yg keluar).
                if os.path.dirname(rp) != mon_real:
                    self._send_json(400, {"ok": False, "msg": "hanya file level atas MONITORING"})
                    return
                if not os.path.isfile(rp):
                    self._send_json(404, {"ok": False, "msg": "file tidak ada di MONITORING"})
                    return
                try:
                    os.remove(rp)
                except Exception as e:
                    self._send_json(500, {"ok": False, "msg": f"gagal hapus file: {e}"[:200]})
                    return
                try:
                    _d, iso = _we_norm_date(datetime.date.today())
                except Exception:
                    iso = datetime.date.today().isoformat()
                db = None
                try:
                    db = _hub_db()
                    if db is not None:
                        con = db.get_conn()
                        try:
                            con.execute("DELETE FROM we_seen WHERE date=? AND filename=?", (iso, name))
                            con.execute("DELETE FROM we_printed WHERE date=? AND filename=?", (iso, name))
                            con.commit()
                        finally:
                            try:
                                con.close()
                            except Exception:
                                pass
                except Exception:
                    pass
                try:
                    _we_refresh_announce(iso, db)
                except Exception:
                    pass
                try:
                    add_log("WE", f"hapus file: {name}")
                except Exception:
                    pass
                self._send_json(200, {"ok": True, "msg": f"{name} dihapus"})
                return
            except Exception as e:
                try:
                    self._send_json(500, {"ok": False, "msg": f"error: {e}"[:200]})
                except Exception:
                    pass
                return
        if path == "/api/we/seen":
            # WE cabut tanda SEEN hari ini (batch Hapus frontend; USER boleh —
            # gate spt mark-printed, BUKAN admin spt /api/we/file). HANYA hapus
            # baris DB we_seen (+we_printed bila ada) utk tanggal hari ini;
            # file di DISK tetap ada — rescan memunculkan lagi bila berkas
            # masih di MONITORING. Tak pernah raise (500 JSON).
            try:
                tok = self._get_token()
                if not _auth_lookup(tok):
                    self._send_json(401, {"ok": False, "msg": "unauthorized"})
                    return
                qs = urllib.parse.parse_qs(parsed.query)
                name = ((qs.get("name") or [""])[0] or "").strip()
                if not name or name != os.path.basename(name) or not name.lower().endswith(".pdf"):
                    self._send_json(400, {"ok": False, "msg": "name harus basename .pdf"})
                    return
                try:
                    _d, iso = _we_norm_date(datetime.date.today())
                except Exception:
                    iso = datetime.date.today().isoformat()
                db = None
                try:
                    db = _hub_db()
                    if db is None:
                        self._send_json(500, {"ok": False, "msg": "DB tidak tersedia"})
                        return
                    con = db.get_conn()
                    try:
                        hits = list(con.execute(
                            "SELECT filename FROM we_seen WHERE date=? AND filename=?", (iso, name)))
                    finally:
                        try:
                            con.close()
                        except Exception:
                            pass
                    if not hits:
                        self._send_json(404, {"ok": False, "msg": "file belum terlihat hari ini (we_seen)"})
                        return
                    con = db.get_conn()
                    try:
                        con.execute("DELETE FROM we_seen WHERE date=? AND filename=?", (iso, name))
                        con.execute("DELETE FROM we_printed WHERE date=? AND filename=?", (iso, name))
                        con.commit()
                    finally:
                        try:
                            con.close()
                        except Exception:
                            pass
                except Exception as e:
                    self._send_json(500, {"ok": False, "msg": f"error: {e}"[:200]})
                    return
                try:
                    _we_refresh_announce(iso, db)
                except Exception:
                    pass
                try:
                    add_log("WE", f"cabut seen: {name}")
                except Exception:
                    pass
                self._send_json(200, {"ok": True, "msg": f"{name} dihapus dari daftar (file disk tetap ada)"})
                return
            except Exception as e:
                try:
                    self._send_json(500, {"ok": False, "msg": f"error: {e}"[:200]})
                except Exception:
                    pass
                return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        pass  # suppress HTTP logs


def start_http_server(port):
    srv = http.server.HTTPServer(("0.0.0.0", port), DashboardHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


# ══════════════════════════════════════════════════════════════
# WEBSOCKET HANDLER
# ══════════════════════════════════════════════════════════════
async def handler(connection):
    global kmma_timer_cache
    role = "dashboard"
    client_id = ""
    ws = connection

    try:
        async for raw in ws:
            msg = json.loads(raw)
            msg_type = msg.get("type", "")

            if msg_type == "subscribe":
                role = msg.get("role", "dashboard")
                client_id = msg.get("id", "")
                # auth: dashboard wajib token; wma/kmma/kma dikecualikan (trusted internal)
                sess = None
                tok = ""
                if role == "dashboard":
                    tok = str(msg.get("token") or "").strip()
                    if not tok:
                        tok = _auth_token_from_ws(ws, None)
                    sess = _auth_lookup(tok) if tok else None
                    if not sess:
                        try:
                            await ws.close(code=4401, reason="unauthorized")
                        except Exception:
                            pass
                        return
                    clients[ws] = {"role": role, "id": client_id, "user": sess["user"], "token": tok}
                else:
                    # allow internal modules without auth
                    clients[ws] = {"role": role, "id": client_id}

                # dashboard: kirim init dulu (riwayat TANPA baris connect sendiri → tak dobel)
                if role == "dashboard":
                    init = {
                        "type": "init",
                        "logs": list(log_history),
                        "entries": entries_cache,
                        "entries_all": list(entries_all_cache.values()),
                        "verified": sorted(verified_cache),
                        "kmma_timer": kmma_timer_cache,
                        "output_files": list(output_cache),
                        "we": _we_snapshot(),
                        "status": status_cache,
                        "modules": module_status,
                        "steps": step_cache,
                        "kma": kma_cache,
                    }
                    await ws.send(json.dumps(init, ensure_ascii=False))
                elif role in ("wma", "kmma", "kma"):
                    add_log(role.upper(), f"{role} connected")
                    module_status[role] = {"connected": True, "ts": ts()}
                    broadcast(json.dumps({"type": "module_status", "module": role, "connected": True, "ts": ts()}))
                else:
                    add_log("HUB", f"{role} connected ({client_id or ''})")

            elif msg_type == "new_entries":
                entries_list = msg.get("entries", [])
                entries_cache.clear()
                entries_cache.extend(entries_list)
                add_log("WMA", f"{len(entries_list)} entries baru")
                broadcast(json.dumps({"type": "entries", "entries": entries_list}))
                # gabung ke tampilan seharian (tak ikut terhapus KMMA) + persist.
                # Varian basi per nota dibuang (mis. bahan terkoreksi saat re-parse),
                # kecuali nota berstatus final ok/error (riwayat dipertahankan).
                try:
                    # Kunci kanonis = DB PK (nota,bahan,sides,laminating,test,process_type);
                    # bahan dinormalkan "" (bukan None) agar cocok dgn baris DB.
                    batch_keys = {(e.get("nota"), e.get("bahan") or "",
                                   e.get("sides") or "",
                                   e.get("laminating") or "",
                                   1 if e.get("test") else 0,
                                   e.get("process_type") or "") for e in entries_list}
                    batch_notas = {k[0] for k in batch_keys}
                    for k in list(entries_all_cache.keys()):
                        if k[0] in batch_notas and k not in batch_keys:
                            _s = status_cache.get(_rkey(*k), {}).get("status")
                            if _s not in ("ok", "error"):
                                del entries_all_cache[k]
                    for e in entries_list:
                        entries_all_cache[(e.get("nota"), e.get("bahan") or "",
                                           e.get("sides") or "",
                                           e.get("laminating") or "",
                                           1 if e.get("test") else 0,
                                           e.get("process_type") or "")] = e
                    db = _hub_db()
                    if db is not None:
                        db.wma_seen_upsert(datetime.date.today().isoformat(), entries_list)
                    broadcast(json.dumps({"type": "entries_all",
                                          "entries": list(entries_all_cache.values())},
                                         ensure_ascii=False))
                except Exception:
                    pass

            elif msg_type == "status":
                nota = msg.get("nota", "")
                st = msg.get("status", "")
                smsg = msg.get("msg", "")
                if any(msg.get(k) is not None for k in ("bahan", "sides", "laminating", "test", "process_type")):
                    _rk = _rkey(nota, msg.get("bahan"), msg.get("sides"),
                                msg.get("laminating"), msg.get("test"),
                                msg.get("process_type"))
                else:
                    _rk = nota  # pengirim lama: kunci legacy per-nota
                status_cache[_rk] = {"status": st, "msg": smsg, "ts": ts()}
                add_log("KMMA", f"{nota} -> {st} {smsg}")
                broadcast(json.dumps({
                    "type": "status_update",
                    "nota": nota,
                    "status": st,
                    "msg": smsg,
                    "ts": ts(),
                    "key": _rk,
                }))
                # antrean live: BARIS final (ok/error) keluar dari entries
                # (cermin filter WMA; entries_all/seen tak tersentuh utk kolom Tengah)
                if nota and st in ("ok", "error"):
                    _sk = _pkey(_rk)
                    kept = [e for e in entries_cache if not _key_match(_ekey_of(e), _sk)]
                    if len(kept) != len(entries_cache):
                        entries_cache[:] = kept
                        broadcast(json.dumps({"type": "entries", "entries": entries_cache}))

            # ── dashboard → modul commands ──
            elif msg_type == "command":
                action = msg.get("action", "")
                # ── auth / RBAC ──────────────────────────────────
                # resolve user from stored client + msg.token
                _info = clients.get(ws, {})
                _user = _info.get("user")
                _tok_msg = str(msg.get("token") or "").strip()
                if _tok_msg:
                    _sess_tmp = _auth_lookup(_tok_msg)
                    if _sess_tmp:
                        _user = _sess_tmp["user"]
                        _info["user"] = _user
                        _info["token"] = _tok_msg
                        clients[ws] = _info
                # untuk dashboard, validasi session masih hidup bila ada token tersimpan
                if _user and _info.get("token"):
                    _sess_check = _auth_lookup(_info.get("token"))
                    if not _sess_check:
                        _user = None
                        # hapus user expired
                        try:
                            del _info["user"]
                        except Exception:
                            pass
                user_role = (_user or {}).get("role", "") if _user else ""
                is_dashboard = (_info.get("role") == "dashboard")
                # internal modules bypass RBAC
                needs_auth = is_dashboard
                # helper to deny
                async def _deny(msg_text):
                    await _safe_send(ws, json.dumps({
                        "type": "command_result",
                        "id": msg.get("id", ""),
                        "action": action,
                        "ok": False,
                        "msg": msg_text,
                    }, ensure_ascii=False))

                if needs_auth and not _user:
                    await _deny("login dulu (token tidak valid/expired)")
                    continue
                if needs_auth and not _auth_required_role(action, user_role):
                    await _deny("admin only")
                    continue
                # ── per-action handling ─────────────────────────
                if action in FOLDER_ACTIONS:
                    # get_folders: user boleh, set_folders: admin only (sudah dicek RBAC)
                    ok, res, current = handle_folders_command(
                        action, msg.get("module", ""), msg.get("folders"))
                    await _safe_send(ws, json.dumps({
                        "type": "command_result",
                        "id": msg.get("id", ""),
                        "action": action,
                        "module": msg.get("module", ""),
                        "folders": current,
                        "ok": bool(ok),
                        "msg": res,
                    }, ensure_ascii=False))
                elif action in OUTPUT_ACTIONS:
                    # list_output & move_output: user boleh
                    ok, res, files = handle_output_command(action, msg)
                    await _safe_send(ws, json.dumps({
                        "type": "command_result",
                        "id": msg.get("id", ""),
                        "action": action,
                        "files": files,
                        "ok": bool(ok),
                        "msg": res,
                    }, ensure_ascii=False))
                elif action == "verify":
                    # user boleh
                    ok, res = _mark_verified(
                        msg.get("nota", ""), msg.get("bahan"), msg.get("sides"),
                        msg.get("laminating"), msg.get("test"), msg.get("process_type"))
                    add_log("HUB", f"verify {msg.get('nota', '')} -> {res}")
                    await _safe_send(ws, json.dumps({
                        "type": "command_result",
                        "id": msg.get("id", ""),
                        "action": action,
                        "nota": msg.get("nota", ""),
                        "ok": bool(ok),
                        "msg": res,
                    }, ensure_ascii=False))
                elif action == "hub_restart":
                    if role != "dashboard":
                        ok, res = False, "hanya dashboard boleh restart HUB"
                    else:
                        ok, res = handle_hub_restart()
                    add_log("HUB", f"hub_restart ({role}/{user_role}) -> {res}")
                    await _safe_send(ws, json.dumps({
                        "type": "command_result",
                        "id": msg.get("id", ""),
                        "action": action,
                        "ok": bool(ok),
                        "msg": res,
                    }, ensure_ascii=False))
                elif action == "db_tables":
                    tables = _db_list_tables()
                    await _safe_send(ws, json.dumps({
                        "type": "command_result",
                        "id": msg.get("id", ""),
                        "action": action,
                        "ok": True,
                        "msg": "ok",
                        "tables": tables,
                    }, ensure_ascii=False))
                elif action == "db_query":
                    _tbl = (msg.get("table") or msg.get("name") or "").strip()
                    ok, payload = _db_query_table(_tbl, msg.get("limit", 20), msg.get("offset", 0), msg.get("search", ""))
                    if ok:
                        await _safe_send(ws, json.dumps({
                            "type": "command_result",
                            "id": msg.get("id", ""),
                            "action": action,
                            "ok": True,
                            "msg": "ok",
                            **payload,
                        }, ensure_ascii=False))
                    else:
                        await _safe_send(ws, json.dumps({
                            "type": "command_result",
                            "id": msg.get("id", ""),
                            "action": action,
                            "ok": False,
                            "msg": str(payload),
                        }, ensure_ascii=False))
                elif action in MODULE_ACTIONS:
                    ok, res = handle_module_command(action, msg.get("module", ""))
                    changed = set(refresh_supervised_status())
                    _mod = msg.get("module", "")
                    if _mod in module_status:
                        changed.add(_mod)
                    for _n in changed:
                        broadcast(json.dumps({"type": "module_status", "module": _n,
                                              "connected": module_status[_n]["connected"],
                                              "ts": module_status[_n]["ts"]}))
                    add_log("HUB", f"{action} {msg.get('module', '')} -> {res}")
                    await _safe_send(ws, json.dumps({
                        "type": "command_result",
                        "id": msg.get("id", ""),
                        "action": action,
                        "module": msg.get("module", ""),
                        "ok": bool(ok),
                        "msg": res,
                    }, ensure_ascii=False))
                else:
                    if action in FORWARD_ACTIONS:
                        broadcast(json.dumps(msg), roles=["kmma", "kma"])
                    else:
                        add_log("HUB", f"aksi tak dikenal dari {role}: {action}")
                        await _safe_send(ws, json.dumps({
                            "type": "command_result",
                            "id": msg.get("id", ""),
                            "action": action,
                            "ok": False,
                            "msg": f"aksi tak dikenal: {action}",
                        }, ensure_ascii=False))

            # ── modul → dashboard command results ──
            elif msg_type == "command_result":
                broadcast(json.dumps(msg), roles=["dashboard"])

            # ── KMA: router state (WS; utama via POST /ingest) ──
            elif msg_type == "kma_state":
                broadcast(apply_kma_state(msg))

            elif msg_type == "log":
                add_log(msg.get("module", "HUB"), msg.get("msg", ""))

            # ── KMMA: per-step detail ──
            elif msg_type == "step_detail":
                nota = msg.get("nota", "")
                steps = msg.get("steps", [])
                if nota:
                    if any(msg.get(k) is not None for k in ("bahan", "sides", "laminating", "test", "process_type")):
                        _rk = _rkey(nota, msg.get("bahan"), msg.get("sides"),
                                     msg.get("laminating"), msg.get("test"),
                                     msg.get("process_type"))
                    else:
                        _rk = nota  # pengirim lama: kunci legacy per-nota
                    step_cache[_rk] = steps
                    if _rk != nota:
                        step_cache[nota] = steps  # legacy: dashboard lama tetap render
                    broadcast(json.dumps({
                        "type": "step_detail_update",
                        "nota": nota,
                        "key": _rk,
                        "steps": steps,
                    }))

            # ── KMMA: hitung mundur timer siklus ──
            elif msg_type == "kmma_timer":
                try:
                    kmma_timer_cache = {"next_in": max(0, int(msg.get("next_in", 0))),
                                        "interval": max(1, int(msg.get("interval", 180)))}
                except Exception:
                    kmma_timer_cache = {"next_in": 0, "interval": 180}
                broadcast(json.dumps({"type": "kmma_timer_update", **kmma_timer_cache}))

            elif msg_type == "heartbeat":
                pass

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        info = clients.pop(ws, {})
        disc_role = info.get("role", "?")
        # socket lama yg closes tak boleh menimpa "connected" milik socket
        # lain dgn role sama (restart overlap) → badge berkedip merah/ hijau
        still_live = any(c.get("role") == disc_role for c in clients.values())
        if disc_role in ("wma", "kmma", "kma") and not still_live:
            add_log(disc_role.upper(), f"{disc_role} disconnected")
            module_status[disc_role] = {"connected": False, "ts": ts()}
            broadcast(json.dumps({"type": "module_status", "module": disc_role, "connected": False, "ts": ts()}))
        else:
            add_log("HUB", f"{disc_role} disconnected")


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════
async def _supervisor_poller():
    """Poll liveness tiap 2 dtk; broadcast module_status bila berubah;
    nyalakan lagi modul yg mati mendadak (auto-restart ber-backoff)."""
    while True:
        await asyncio.sleep(2)
        try:
            if not SUPERVISE:
                continue
            if AUTO_RESTART:
                auto_restart_dead()
            for _n in refresh_supervised_status():
                broadcast(json.dumps({"type": "module_status", "module": _n,
                                      "connected": module_status[_n]["connected"],
                                      "ts": module_status[_n]["ts"]}))
        except Exception:
            pass


async def _kma_sweeper():
    """Tandai KMA offline bila laporannya basi."""
    while True:
        await asyncio.sleep(30)
        try:
            # Supervisi aktif + proses hidup → poll() yg berwenang, bukan sweeper.
            if SUPERVISE and module_alive("kma"):
                continue
            seen = kma_cache.get("seen", 0) or 0
            if module_status.get("kma", {}).get("connected") and time.time() - seen > KMA_STALE_S:
                module_status["kma"] = {"connected": False, "ts": ts()}
                broadcast(json.dumps({"type": "module_status", "module": "kma",
                                      "connected": False, "ts": ts()}))
        except Exception:
            pass


def _hub_db():
    """DB pusat (sekali init). Return modul db atau None."""
    global _DBH_READY
    try:
        sys.path.insert(0, str(BASE_DIR / "Database"))
        import db as nshdb
        if not _DBH_READY:
            nshdb.init_db()
            _DBH_READY = True
        return nshdb
    except Exception:
        return None


_DBH_READY = False


def _preload_wma_from_db():
    """Muat entri terlihat + verifikasi hari ini (restart hub tak hilang).
    entries_cache (antrean live KMMA) disusun ulang = terlihat hari ini
    minus nota yang sudah diproses KMMA. statuses dipulihkan best-effort
    dari kmma_state agar kolom Menunggu Aksi tetap terisi."""
    try:
        db = _hub_db()
        if db is None:
            return
        today = datetime.date.today().isoformat()
        for e in db.wma_seen_day(today):
            entries_all_cache[(e.get("nota"), e.get("bahan"), e.get("sides") or "",
                               e.get("laminating") or "",
                               1 if e.get("test") else 0,
                               e.get("process_type") or "")] = e
        try:
            # WMA simpan ke wma_entries walau hub sedang mati/offline —
            # gabung agar tak ada yang hilang dari kolom.
            for e in db.wma_all(today):
                entries_all_cache.setdefault(
                    (e.get("nota"), e.get("bahan"), e.get("sides") or "",
                     e.get("laminating") or "", 1 if e.get("test") else 0,
                     e.get("process_type") or ""), e)
        except Exception:
            pass
        for n in db.wma_verified_all():
            verified_cache.add(n)
        proc = set()
        st_map = {}
        for _st in ("completed", "skip_forever", "skipped", "manual"):
            try:
                rows = db.kmma_by_status(_st)
            except Exception:
                rows = {}
            # Antrean live HANYA keluarkan final (completed/skip_forever),
            # sama spt handler status (ok/error). Retry (skipped) + manual tetap di
            # antrean + berstatus masing-masing agar tak jadi hantu.
            if _st in ("completed", "skip_forever"):
                proc.update(rows.keys())
            for _k, _info in rows.items():
                st_map[_k] = _st
        entries_cache.clear()
        entries_cache.extend(
            e for e in entries_all_cache.values() if _ekey_of(e) not in proc)
        for _k, _st in st_map.items():
            _sk = _rkey(*_k)
            if _sk in status_cache:
                continue
            try:
                _info = db.kmma_get(*_k) or {}
            except Exception:
                _info = {}
            _reason = (_info.get("reason") or "").strip()
            if _st == "skipped":
                # retry menunggu timer: status sendiri agar tak jadi hantu Antrian
                status_cache[_sk] = {
                    "status": "retry",
                    "msg": "tunggu 3 mnt",
                    "ts": _info.get("updated_at") or "",
                }
                continue
            if _st == "manual":
                status_cache[_sk] = {
                    "status": "manual",
                    "msg": _reason or "manual",
                    "ts": _info.get("updated_at") or "",
                }
                continue
            status_cache[_sk] = {
                "status": "ok" if _st == "completed" else "error",
                "msg": _reason or ("selesai" if _st == "completed" else _st),
                "ts": _info.get("updated_at") or "",
            }
        if entries_all_cache or verified_cache:
            add_log("HUB", f"WMA hari ini dimuat dari DB: {len(entries_all_cache)} entri, "
                           f"{len(entries_cache)} antrean, {len(verified_cache)} verified")
    except Exception:
        pass


def _mark_verified(nota, bahan=None, sides=None, laminating=None, test=None, process_type=None):
    """Tandai terverifikasi per-baris 6-tuple → DB + siaran + watcher. Return (ok, msg).
    Jika hanya nota, semua varian nota tsb diverifikasi (loop per row)."""
    nota = (nota or "").strip()
    if not nota:
        return False, "nota kosong"
    # tentukan target rows
    explicit = any(v is not None for v in (bahan, sides, laminating, test, process_type))
    targets = []
    if explicit:
        b = bahan or ""
        s = sides or ""
        l = laminating or ""
        # test bisa bool/int/str
        if isinstance(test, str):
            t = 1 if test in ("1", "true", "True", "1") else 0
        elif test is None:
            t = 0
        else:
            t = 1 if test else 0
        p = process_type or ""
        targets = [(nota, b, s, l, t, p)]
    else:
        for k in list(entries_all_cache.keys()):
            if k[0] == nota:
                targets.append(k)
        if not targets:
            targets = [(nota, "", "", "", 0, "")]
    db = _hub_db()
    for tk in targets:
        n, b, s, l, t, p = tk
        try:
            if db is not None:
                _vts = ts()  # satu ts utk double-write WMA+WE (ts sama)
                if hasattr(db, "wma_verify_row"):
                    db.wma_verify_row(n, b, s, l, bool(t), p, _vts)
                else:
                    db.wma_verify(n, _vts, bahan=b, sides=s, laminating=l, test=bool(t), process_type=p)
                # WE hook: cermin verifikasi per-baris 6-tuple (guard gaya repo: hasattr)
                if hasattr(db, "we_verify_row"):
                    try:
                        db.we_verify_row(n, b, s, l, bool(t), p, _vts)
                    except Exception:
                        pass
        except Exception:
            pass
        verified_cache.add(_rkey(n, b, s, l, bool(t), p))
    broadcast(json.dumps({"type": "verified_update",
                          "verified": sorted(verified_cache)}, ensure_ascii=False))
    # WE ikut segar SEKEJAP (tanpa tunggu tick 5 dtk): pill VERIFIED di tab WE
    # bergantung weVerified dari we_update — tanpa ini ada jeda/stale.
    try:
        _we_refresh_announce()
    except Exception:
        pass
    upd_total = 0
    matched_total = 0
    err_last = ""
    for tk in targets:
        upd, total, err = _push_verified_to_watcher_for_key(tk)
        upd_total += upd
        matched_total += total
        if err:
            err_last = err
    if matched_total == 0:
        extra = "watcher: file tak ditemukan" if not err_last else f"watcher gagal: {err_last}"
        if len(targets) > 1:
            return True, f"{nota} terverifikasi {len(targets)} baris ({extra})"
        return True, f"{nota} terverifikasi ({extra})"
    if upd_total == matched_total:
        return True, f"{nota} terverifikasi ({upd_total} file watcher)"
    return False, f"{nota} watcher gagal ({upd_total}/{matched_total}): {err_last or 'POST gagal'}"


def _watcher_dash_url():
    """Base URL dashboard watcher (sumber: WMA/wma.json → default .54:5000)."""
    try:
        cfg = json.loads((BASE_DIR / "WMA" / "wma.json").read_text(encoding="utf-8"))
        u = (cfg.get("dash_url") or "").strip().rstrip("/")
        if u:
            return u
    except Exception:
        pass
    return "http://192.168.5.54:5000"


def _watcher_api(method, path, body=None, timeout=5):
    """Panggil API dashboard watcher. Return (status, data)."""
    url = _watcher_dash_url() + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = (r.read() or b"{}").decode("utf-8")
        try:
            return r.status, json.loads(raw)
        except Exception:
            return r.status, {}


def _push_verified_to_watcher_for_key(key):
    """Set verified=true di watcher untuk SATU baris 6-tuple. Return (updated,total,err)."""
    try:
        n, b, s, l, t, p = key
        today = datetime.date.today().isoformat()
        _, data = _watcher_api("GET", f"/api/files?date_from={today}&date_to={today}", timeout=5)
        rows = data if isinstance(data, list) else (data.get("value") or [])
        by_name = {r.get("filename"): r.get("id") for r in rows if r.get("filename") and r.get("id")}
        e = entries_all_cache.get(key)
        # fallback: cari berdasar nota+bahan jika key lengkap tak ada
        if e is None:
            for ek, ev in entries_all_cache.items():
                if ek[0] == n and ek[1] == b and ek[2] == s and ek[3] == l and ek[4] == t and ek[5] == p:
                    e = ev
                    break
        names = (e.get("files") or []) if e else []
        ids = [by_name[nm] for nm in dict.fromkeys(names) if nm in by_name]
        ok_n = 0
        err = ""
        for fid in ids:
            try:
                st, _ = _watcher_api("POST", f"/api/files/{fid}", {"verified": True}, timeout=4)
                if 200 <= st < 300:
                    ok_n += 1
                else:
                    err = f"HTTP {st}"
            except Exception as ex:
                err = str(ex)[:80]
        return ok_n, len(ids), err
    except Exception as ex:
        return 0, 0, str(ex)[:80]


def _push_verified_to_watcher(nota):
    """Legacy wrapper per-nota: gabung semua varian nota tsb."""
    try:
        keys = [k for k in entries_all_cache.keys() if k[0] == nota]
        if not keys:
            keys = [(nota, "", "", "", 0, "")]
        upd_total = 0
        tot_total = 0
        err_last = ""
        for k in keys:
            upd, tot, err = _push_verified_to_watcher_for_key(k)
            upd_total += upd
            tot_total += tot
            if err:
                err_last = err
        return upd_total, tot_total, err_last
    except Exception as ex:
        return 0, 0, str(ex)[:80]


def _preload_kma_from_db():
    """Muat routes+stats KMA hari ini dari DB (restart hub tak nol).
    seen dibiarkan 0 → status tetap offline sampai route.py melapor."""
    try:
        sys.path.insert(0, str(BASE_DIR / "Database"))
        import db as nshdb
        nshdb.init_db()
        today = datetime.date.today().isoformat()
        evs = nshdb.kma_events_day(today)
        st = nshdb.kma_stats_get(today)
        if evs or st["ok"] or st["ret"] or st["done"]:
            kma_cache["routes"] = evs[-500:]
            kma_cache["stats"] = st
            add_log("HUB", f"KMA hari ini dimuat dari DB: {len(evs)} events")
    except Exception:
        pass


async def main():
    global HUB_LOOP, SUPERVISE
    ws_port = WS_PORT
    http_port = HTTP_PORT
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            ws_port = int(sys.argv[idx + 1])
            http_port = ws_port + 1
    SUPERVISE = "--supervise" in sys.argv

    _preload_kma_from_db()
    _preload_wma_from_db()
    _preload_we_from_db()
    supervise_init(ws_port)

    # Bind port DULU (retry bila hub lama belum bebas), modul BARU dinyalakan
    # bila bind sukses — anti duplikat & anti start-yatim.
    server = None
    http_ok = False
    for _attempt in range(15):
        try:
            if not http_ok:
                start_http_server(http_port)
                http_ok = True
            # keepalive longgar: handler ini jalanin I/O blocking (shutil.move ke
            # L:\ + urllib ke watcher .54) yg bisa >20s → default 20/20 bunuh
            # WMA+KMMA+dashboard bareng (1011). Naikkan timeout, bukan interval.
            server = await websockets.serve(handler, HOST, ws_port,
                                             ping_interval=20, ping_timeout=180,
                                             close_timeout=10, max_queue=64)
            break
        except OSError as e:
            print(f"  [hub] port sibuk ({e}), coba lagi {2 * (_attempt + 1)}s...")
            await asyncio.sleep(2)
    if server is None or not http_ok:
        print("  [hub] FATAL: port tak bebas — hub lama masih jalan?")
        print("  [hub] berhenti tanpa menyalakan modul.")
        await asyncio.sleep(30)
        return

    if SUPERVISE:
        for _n in MANAGED_MODULES:
            _ok, _msg = start_module(_n)
            print(f"  [supervise] {_msg}")
        refresh_supervised_status()

    HUB_LOOP = asyncio.get_running_loop()
    asyncio.create_task(_kma_sweeper())
    asyncio.create_task(_supervisor_poller())
    threading.Thread(target=_output_watcher, daemon=True).start()
    threading.Thread(target=_we_watcher, daemon=True).start()
    threading.Thread(target=_we_event_worker, daemon=True).start()
    print(f"  NSH Hub running:")
    print(f"    Dashboard: http://localhost:{http_port}")
    print(f"    WebSocket: ws://0.0.0.0:{ws_port}")
    print(f"    Supervise: {'ON' if SUPERVISE else 'OFF'}")
    try:
        await server.wait_closed()
    finally:
        if SUPERVISE:
            stop_all_modules()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n  Hub stopped.")
