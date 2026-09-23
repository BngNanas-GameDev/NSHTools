"""NSH Hub — WebSocket Server + Real-time Dashboard
Menengahi komunikasi WMA ↔ KMMA ↔ KMA + serve web dashboard.

Jalankan:
  python hub_server.py           → WS ws://0.0.0.0:8765 + Dashboard http://localhost:8766
  python hub_server.py --port 9000  → custom WS port

KMA (route.py NSH Lite) melapor via POST /ingest (tanpa dependensi baru).
"""
import asyncio
import datetime
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
import http.server
import websockets

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


def start_module(name):
    """Start modul sebagai subprocess (console sendiri). Return (ok, msg)."""
    info = SUPERVISED.get(name)
    if not info:
        return False, f"modul tak dikenal: {name}"
    if module_alive(name):
        return True, f"{name} sudah berjalan (pid {info['proc'].pid})"
    try:
        proc = subprocess.Popen(
            info["command"],
            cwd=info["cwd"],
            stdin=subprocess.DEVNULL,  # non-interaktif: ask_folders() via EOFError
            creationflags=_new_console_flags(),
        )
    except Exception as e:
        return False, f"gagal start {name}: {e}"
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
        return start_module(module)
    if action == "module_off":
        return stop_module(module)
    return restart_module(module)


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
    "kma": ["input_folder", "return_folder", "uda_folder"],
}
FOLDER_ACTIONS = ("get_folders", "set_folders")


def _folders_path(module):
    return BASE_DIR / FOLDER_FILES[module]


def _read_folders_file(module):
    try:
        data = json.loads(_folders_path(module).read_text(encoding="utf-8"))
    except Exception:
        data = {}
    return {k: str(data.get(k, "")) for k in FOLDER_KEYS[module]}


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
    cleaned = {k: str(folders.get(k, "")).strip().strip('"').strip("'")
               for k in keys}
    if any(not v for v in cleaned.values()):
        return False, "semua path wajib diisi", _read_folders_file(module)
    if not os.path.isdir(cleaned["input_folder"]):
        return False, f"INPUT tidak ada: {cleaned['input_folder']}", _read_folders_file(module)
    for k in keys[1:]:
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
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        return False, f"gagal tulis config: {e}", _read_folders_file(module)
    try:
        sys.path.insert(0, str(BASE_DIR / "Database"))
        import db as nshdb
        nshdb.config_set(module, cleaned)
    except Exception:
        pass
    add_log("HUB", f"folders {module} disimpan — restart modul agar berlaku")
    return True, "tersimpan — restart modul agar berlaku", cleaned


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
status_cache = {}
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
    dead = []
    for ws, info in clients.items():
        if roles and info["role"] not in roles:
            continue
        asyncio.get_event_loop().create_task(_safe_send(ws, msg))
    for ws in dead:
        clients.pop(ws, None)


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
    kma_cache["routes"] = (data.get("routes") or [])[:50]
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


# ══════════════════════════════════════════════════════════════
# HTTP SERVER (dashboard only, separate thread)
# ══════════════════════════════════════════════════════════════
class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(DASHBOARD_HTML.encode())

    def do_POST(self):
        if self.path.split("?")[0] != "/ingest":
            self.send_response(404)
            self.end_headers()
            return
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
                clients[ws] = {"role": role, "id": client_id}

                # dashboard: kirim init dulu (riwayat TANPA baris connect sendiri → tak dobel)
                if role == "dashboard":
                    init = {
                        "type": "init",
                        "logs": list(log_history),
                        "entries": entries_cache,
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

            elif msg_type == "status":
                nota = msg.get("nota", "")
                st = msg.get("status", "")
                smsg = msg.get("msg", "")
                status_cache[nota] = {"status": st, "msg": smsg, "ts": ts()}
                add_log("KMMA", f"{nota} -> {st} {smsg}")
                broadcast(json.dumps({
                    "type": "status_update",
                    "nota": nota,
                    "status": st,
                    "msg": smsg,
                    "ts": ts(),
                }))

            # ── dashboard → modul commands ──
            elif msg_type == "command":
                action = msg.get("action", "")
                if action in FOLDER_ACTIONS:
                    # KONSUMSI di hub (baca/tulis lite.json/route.json).
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
                elif action in MODULE_ACTIONS:
                    # KONSUMSI di hub (supervisor proses), jangan teruskan.
                    ok, res = handle_module_command(action, msg.get("module", ""))
                    changed = set(refresh_supervised_status())
                    _mod = msg.get("module", "")
                    # Status terkini modul target selalu disiarkan (sinkronisasi
                    # dashboard; restart cepat tak selalu terlihat poller 2 dtk).
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
                    broadcast(json.dumps(msg), roles=["kmma", "kma"])

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
                    step_cache[nota] = steps
                    broadcast(json.dumps({
                        "type": "step_detail_update",
                        "nota": nota,
                        "steps": steps,
                    }))

            elif msg_type == "heartbeat":
                pass

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        info = clients.pop(ws, {})
        disc_role = info.get("role", "?")
        if disc_role in ("wma", "kmma", "kma"):
            add_log(disc_role.upper(), f"{disc_role} disconnected")
            module_status[disc_role] = {"connected": False, "ts": ts()}
            broadcast(json.dumps({"type": "module_status", "module": disc_role, "connected": False, "ts": ts()}))
        else:
            add_log("HUB", f"{disc_role} disconnected")


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════
async def _supervisor_poller():
    """Poll liveness proses tiap 2 dtk; broadcast module_status bila berubah."""
    while True:
        await asyncio.sleep(2)
        try:
            if not SUPERVISE:
                continue
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

    supervise_init(ws_port)
    if SUPERVISE:
        for _n in MANAGED_MODULES:
            _ok, _msg = start_module(_n)
            print(f"  [supervise] {_msg}")
        refresh_supervised_status()

    HUB_LOOP = asyncio.get_running_loop()
    asyncio.create_task(_kma_sweeper())
    asyncio.create_task(_supervisor_poller())
    start_http_server(http_port)

    server = await websockets.serve(handler, HOST, ws_port)
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
