"""NSH Router: tarik file dari INPUT ke Hot Folder printer yang sesuai.

Alur per file: cek skip -> tebak bahan (guru) -> duplex -> COPY ke
\\\\<printer>\\<HOTFOLDER> (nama asli) -> asli di INPUT dipindah ke UDA.
Tidak cocok -> pindah ke RETURN. Done printer -> UDA.
Pakai: route.bat (watcher) | python route.py --once (sekali jalan)
"""
import json
import os
import shutil
import sys
import threading
import time as _time
import urllib.request
from collections import deque
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE.parent))  # NSH Tools root: konica_printer
sys.path.insert(0, str(BASE.parent / "NSH Lite"))  # llm_zen, bahan_dict
from llm_zen import llm_teacher_preset, ringkas
from bahan_dict import guess_bahan

try:
    from konica_printer import resolve_bahan
except ImportError:
    def resolve_bahan(filename, bahan=""):
        low = (filename or "").lower()
        if any(k in low for k in ("kromo", "cromo", "chromo")):
            return "Stiker Kromo" if ("stiker" in low or "sticker" in low) else "Kromo"
        if "hvs" in low and ("stiker" in low or "sticker" in low):
            return "Stiker HVS"
        return bahan

HOT_SERVER = r"\\192.168.10.157"
PRINTERS = ("192.168.10.157", "192.168.10.158")

# bahan -> (folder_1S, folder_2S). None = tidak ada folder (-> RETURN).
ROUTE = {
    "Vinyl": ("ST_GLOSS_1S", None), "Stiker": ("ST_GLOSS_1S", None),
    "Hologram": ("ST_GLOSS_1S", None), "Gold": ("ST_GLOSS_1S", None),
    "Silver": ("ST_GLOSS_1S", None), "Stiker Kromo": ("ST_GLOSS_1S", None),
    "Transparant": ("ST_GLOSS_1S", None),
    "Stiker HVS": ("ST_UNGLOSS_1S", None),
    "Ap230gr": ("ART_230_1S", "ART_230_2S"), "Ap260gr": ("ART_230_1S", "ART_230_2S"),
    "Ac230gr": ("ART_230_1S", "ART_230_2S"), "Ac260gr": ("ART_230_1S", "ART_230_2S"),
    "Ap190gr": ("ART_190_1S", "ART_190_2S"), "Ac190gr": ("ART_190_1S", "ART_190_2S"),
    "Ap150gr": ("ART_150_1S", "ART_150_2S"),
    "Ap120gr": ("ART_120_1S", "ART_120_2S"),
    "Ap310gr": ("ART_310_1S", "ART_310_2S"), "Ac310gr": ("ART_310_1S", "ART_310_2S"),
    "Kromo": ("ART_COATED_1S", "ART_COATED_2S"),
    "Concord": ("FINE_UNGLOSS_1S", "FINE_UNGLOSS_2S"),
    "Rajawali": ("FINE_UNGLOSS_1S", "FINE_UNGLOSS_2S"),
    "Pindo": ("FINE_UNGLOSS_1S", "FINE_UNGLOSS_2S"),
    "Jasmine": ("FINE_UNGLOSS_1S", "FINE_UNGLOSS_2S"),
    "Hvs100gr": ("HVS_100GR_1S", "HVS_100GR_2S"),
    # KMA saja: Master F4 215x330 (satu folder untuk 1S+2S)
    "Master": ("MASTER_F4", "MASTER_F4"),
}

G = "\033[92m"; Y = "\033[93m"; R = "\033[91m"; C = "\033[96m"; D = "\033[90m"; W = "\033[97m"; B = "\033[1m"; RST = "\033[0m"
USE_COLOR = sys.stdout.isatty()
_STATS = {"ok": 0, "ret": 0, "done": 0}
# Kode error KMA (khusus router hot folder):
# KMA-E01 printer 10.157 & 10.158 mati (fatal, batal jalan)
# KMA-E02 folder INPUT hilang/tak terjangkau
# KMA-E03 folder RETURN tak bisa dibuat/ditulis
# KMA-E04 folder UDA tak bisa dibuat/ditulis
# KMA-E05 COPY ke hot folder gagal (OSError) - file tetap di INPUT
# KMA-E06 bahan tak dikenal/tanpa folder -> RETURN
# KMA-E07 tanpa folder sisi itu (mis. stiker 2S) -> RETURN
# KMA-E08 arsip UDA gagal setelah COPY sukses - file tetap di INPUT
# KMA-E09 Done->Uda gagal (OSError)
# [ LOCK ]/[ WAIT ] transien (auto-retry), tanpa kode.
_COOLDOWN = {}
COOLDOWN_S = 5
UDA_DELAY_S = 6  # jeda COPY -> arsip UDA (beri WMA + dashboard waktu mencatat)
_ERR_QUIET = {}  # (name, kode) -> timestamp log terakhir (anti-spam error berulang)
ERR_QUIET_S = 120


# ── Hub reporting (opt-in via --hub; default mati, perilaku lama tak berubah) ──
_HUB = False
_HUB_URL = "http://localhost:8766"
_EVENTS = deque(maxlen=200)  # ring: {file, category, dest, status, msg, ts}
_EV_LAST = {}  # file -> (status, dest) terakhir (anti-duplikat)
_PRINTER_STATE = {}  # ip -> True/False (hasil cek terakhir)
_FOLDERS = {}  # input/return/uda (diisi watch_loop/--once)


def _hub_flags():
    global _HUB, _HUB_URL
    if "--hub" in sys.argv:
        _HUB = True
        if "--hub-url" in sys.argv:
            i = sys.argv.index("--hub-url")
            if i + 1 < len(sys.argv):
                _HUB_URL = sys.argv[i + 1]


def _machine():
    """Label mesin file router: DEVELOP 1 (.157) / DEVELOP 2 (.158)."""
    cur = HOT_SERVER.strip("\\")
    if cur == PRINTERS[0]:
        return "DEVELOP 1"
    if cur == PRINTERS[1]:
        return "DEVELOP 2"
    return cur or "?"


def _ev(fname, category, dest, status, msg="", machine=None):
    """Catat event routing (dedupe per file+status+dest) → memori + DB harian.
    DB-first: tetap dicatat walau tanpa --hub, agar restart tak nol.
    Return True bila baris BARU (nama sama tak menaikkan count)."""
    day = _ensure_day()
    key = (status, dest)
    if _EV_LAST.get(fname) == key:
        return False
    _EV_LAST[fname] = key
    mc = machine or _machine()
    ev = {"file": fname, "category": category or "?",
          "dest": dest, "status": status, "msg": msg, "ts": _now(), "machine": mc}
    _EVENTS.append(ev)
    db = _kma_db()
    if db is not None:
        try:
            db.kma_event_add(day, fname, category, dest, status, msg, ev["ts"], mc)
        except Exception:
            pass
    return True


def _hub_post(body, timeout=5):
    try:
        req = urllib.request.Request(
            _HUB_URL.rstrip("/") + "/ingest",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
    except Exception:
        pass


def _hub_log(msg):
    if not _HUB:
        return
    # non-blocking: thread per pesan agar loop routing tidak nunggu HTTP
    def _post():
        _hub_post({"source": "kma", "kind": "log", "msg": msg}, timeout=3)
    try:
        threading.Thread(target=_post, daemon=True).start()
    except Exception:
        pass


def push_kma():
    """Kirim state router ke hub (routes/printers/rules/stats)."""
    _ensure_day()
    if not _HUB:
        return
    rules = []
    for b, f in ROUTE.items():
        d = f[0] + (" / " + f[1] if f[1] and f[1] != f[0] else "")
        rules.append({"category": b, "dest": d})
    printers = []
    for i, ip in enumerate(PRINTERS):
        st = _PRINTER_STATE.get(ip)
        printers.append({"ip": ip, "role": "",
                         "machine": "DEVELOP 1" if i == 0 else "DEVELOP 2",
                         "status": "siap" if st else ("mati" if st is False else "?"),
                         "active": HOT_SERVER.strip("\\") == ip})
    # RICOH watcher: reader lokal — selalu ON selama router jalan
    # (mati hanya bila foldernya dikonfigurasi tapi hilang)
    _ricoh = (_FOLDERS.get("ricoh") or "").strip()
    printers.append({"ip": "RICOH", "role": "watcher", "machine": "RICOH",
                     "status": "siap" if (not _ricoh or os.path.isdir(_ricoh)) else "mati",
                     "active": False})
    _hub_post({"source": "kma", "kind": "kma_state",
               "routes": list(_EVENTS)[-50:], "printers": printers,
               "rules": rules, "stats": dict(_STATS), "folders": dict(_FOLDERS)})


def _now():
    return _time.strftime("%H:%M:%S")


def _cfg_mtime():
    try:
        return os.stat(str(BASE / "route.json")).st_mtime
    except OSError:
        return 0.0


def _restart_self(reason):
    """Restart proses ini (pakai config tersimpan, tanpa prompt)."""
    _log(Y, "[ RESTART ]", reason, "memuat path baru...")
    os.environ["NSH_NO_PROMPT"] = "1"
    try:
        script = os.path.abspath(sys.argv[0])
        os.execv(sys.executable, [sys.executable, script] + sys.argv[1:])
    except Exception as e:
        _log(R, "[ RESTART ]", "gagal", str(e)[:80])
        os.environ.pop("NSH_NO_PROMPT", None)


def _today():
    return _time.strftime("%Y-%m-%d")


_KMA_DAY = ""  # tanggal yang sedang dimuat di memori
_DB_MOD = None  # modul db pusat (sekali import)
_DB_READY = False  # init_db sudah jalan


def _kma_db():
    """Import + init DB pusat (sekali). Return modul db atau None."""
    global _DB_MOD, _DB_READY
    if _DB_MOD is None:
        _DB_MOD = _nshdb()
    if _DB_MOD is None:
        return None
    if not _DB_READY:
        try:
            _DB_MOD.init_db()
            _DB_READY = True
        except Exception:
            pass
    return _DB_MOD


def _load_day(date):
    """Muat stats + events hari tsb dari DB ke memori (restart lanjut, tak nol)."""
    global _KMA_DAY
    _EVENTS.clear()
    _EV_LAST.clear()
    _STATS.update({"ok": 0, "ret": 0, "done": 0})
    _KMA_DAY = date
    db = _kma_db()
    if db is None:
        return
    try:
        s = db.kma_stats_get(date)
        _STATS.update({"ok": s["ok"], "ret": s["ret"], "done": s["done"]})
    except Exception:
        pass
    try:
        for e in db.kma_events_day(date):
            _EVENTS.append({"file": e["file"], "category": e["category"] or "?",
                            "dest": e["dest"], "status": e["status"],
                            "msg": e["msg"], "ts": e["ts"],
                            "machine": e.get("machine") or ""})
            _EV_LAST[e["file"]] = (e["status"], e["dest"])
        db.kma_prune()
    except Exception:
        pass


def _ensure_day():
    """Ganti hari (lewat tengah malam) → reset + muat hari baru. Return tanggal aktif."""
    today = _today()
    if today != _KMA_DAY:
        _load_day(today)
    return today


def _save_stats():
    db = _kma_db()
    if db is None:
        return
    try:
        db.kma_stats_set(_KMA_DAY or _today(), _STATS["ok"], _STATS["ret"], _STATS["done"])
    except Exception:
        pass


def _bump(key):
    """Naikkan stat + tulis DB (write-through)."""
    _STATS[key] += 1
    _save_stats()


def _log(color, tag, msg, detail=""):
    c = color if USE_COLOR else ""
    b = B if USE_COLOR else ""
    r = RST if USE_COLOR else ""
    d = f" {D}{detail}{R}" if detail else ""
    try:
        print(f"{D}{_now()}{R} {c}{b}{tag:<12}{R} {msg}{d}")
    except UnicodeEncodeError:
        print(f"{_now()} {tag:<12} {msg} {detail}")
    plain = f"{tag} {msg}" + (f" | {detail}" if detail else "")
    _hub_log(plain.strip())


def _short(s, n=62):
    s = str(s)
    return s if len(s) <= n else s[:n - 1] + "..."


def _banner():
    c = C if USE_COLOR else ""
    b = B if USE_COLOR else ""
    r = RST if USE_COLOR else ""
    print(f"{c}{b}======================================================{r}")
    print(f"{c}{b}  NSH Router -- INPUT -> Hot Folder Printer{r}")
    print(f"{c}{b}======================================================{r}")


def _server_ready(ip):
    """Port 445 nyala DAN share hot folder ada (sentinel ST_GLOSS_1S).
    Share 'print' tidak dihitung — .158 punya print tapi tanpa hot folder."""
    import socket
    try:
        s = socket.create_connection((ip, 445), timeout=3)
        s.close()
    except OSError:
        return False
    for share in ("ST_GLOSS_1S", "ST_GLOSSY_1S"):
        try:
            os.listdir(f"\\\\{ip}\\{share}")
            return True
        except OSError:
            continue
    return False


# Nama folder yang beda di .158 (selebihnya sama persis)
FOLDER_ALIASES_158 = {
    "ST_GLOSS_1S": "ST_GLOSSY_1S",
    "ST_UNGLOSS_1S": "ST_UNGLOSSY_1S",
}


def _folder(name):
    """Nama folder aktual di server aktif (alias khusus .158)."""
    if HOT_SERVER.endswith(".158"):
        return FOLDER_ALIASES_158.get(name, name)
    return name


def check_server():
    """Cek printer .157 dulu, fallback .158 (harus ada share hot folder).
    Return True jika ada yang siap dipakai."""
    global HOT_SERVER
    for ip in PRINTERS:
        ready = _server_ready(ip)
        _PRINTER_STATE[ip] = ready
        if ready:
            HOT_SERVER = f"\\\\{ip}"
            _log(G, "[ NET ]", f"printer {ip} siap", f"server -> {HOT_SERVER}")
            return True
        _log(Y, "[ NET ]", f"printer {ip} tidak siap", "port/share tak terjangkau")
    _log(R, "[KMA-E01]", "10.157 dan 10.158 tak siap", "cek printer/jaringan, router batal jalan")
    return False


_SERVER_DOWN = False


def server_ok_quiet():
    """Cek tiap loop (tanpa spam). Aturan pindah otomatis:
    - server aktif mati -> pindah ke yang hidup (log 1x)
    - di cadangan (.158) dan utama (.157) pulih -> balik ke .157 (log 1x)
    - semua mati -> KMA-E01 1x, loop dilewati sampai pulih."""
    global _SERVER_DOWN, HOT_SERVER
    cur = HOT_SERVER.strip("\\")
    if _server_ready(cur):
        _PRINTER_STATE[cur] = True
        if cur != PRINTERS[0] and _server_ready(PRINTERS[0]):
            HOT_SERVER = f"\\\\{PRINTERS[0]}"
            _log(G, "[ NET ]", f"balik ke {PRINTERS[0]}", "server utama pulih")
        if _SERVER_DOWN:
            _SERVER_DOWN = False
            _log(G, "[ NET ]", f"printer {cur} pulih", "lanjut routing")
        return True
    _PRINTER_STATE[cur] = False
    for ip in PRINTERS:
        if ip != cur and _server_ready(ip):
            HOT_SERVER = f"\\\\{ip}"
            _SERVER_DOWN = False
            _PRINTER_STATE[ip] = True
            _log(Y, "[ NET ]", f"pindah ke {ip}", f"{cur} mati")
            return True
        elif ip != cur:
            _PRINTER_STATE[ip] = False
    if not _SERVER_DOWN:
        _SERVER_DOWN = True
        _log(R, "[KMA-E01]", "semua printer putus", "tunggu pulih, loop dilewati")
    return False


def _nshdb():
    try:
        sys.path.insert(0, str(BASE.parent / "Database"))
        import db as nshdb
        return nshdb
    except Exception:
        return None


def load_cfg():
    db = _nshdb()
    if db:
        try:
            cfg = db.config_get("kma")
            if cfg:
                return cfg
        except Exception:
            pass
    try:
        return json.loads((BASE / "route.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cfg(cfg):
    db = _nshdb()
    if db:
        try:
            db.config_set("kma", cfg)
        except Exception:
            pass
    (BASE / "route.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                                     encoding="utf-8")


def ask_folders():
    cfg = load_cfg()
    ind = Path(cfg.get("input_folder", str(BASE / "input")))
    retd = Path(cfg.get("return_folder", str(BASE / "return")))
    saved_ricoh = (cfg.get("ricoh_folder") or "").strip()
    if os.environ.get("NSH_NO_PROMPT") == "1":
        udad = Path(cfg.get("uda_folder", str(ind.parent / "Uda")))
        print(f"  {D}pakai tersimpan (auto-restart).{RST}")
        return ind, retd, udad, (Path(saved_ricoh) if saved_ricoh else None)
    _banner()
    print()
    print(f"  {W}Setup Folder{RST}  {D}(Enter = pakai yang tersimpan){RST}")
    print(f"  ------------------------------------------------------")
    for label, val in [("INPUT ", ind), ("RETURN", retd)]:
        print(f"    {label} : {C}{val}{RST}")
    print(f"  Hot Folder server : {C}{HOT_SERVER}{RST}")
    print(f"  Watcher RICOH     : {C}{saved_ricoh or '(mati)'}{RST}")
    print(f"  ------------------------------------------------------")
    try:
        a = input(f"  Folder INPUT?  Copas path (Enter=tetap): ").strip().strip('"').strip("'")
        if a:
            ind = Path(a)
            cfg["input_folder"] = str(ind)
        b = input(f"  Folder RETURN? Copas path (Enter=tetap): ").strip().strip('"').strip("'")
        if b:
            retd = Path(b)
            cfg["return_folder"] = str(retd)
        uda_default = Path(cfg.get("uda_folder", str(ind.parent / "Uda")))
        c = input(f"  Folder UDA?    Copas path (Enter={uda_default}): ").strip().strip('"').strip("'")
        udad = Path(c) if c else uda_default
        if c or "uda_folder" not in cfg:
            cfg["uda_folder"] = str(udad)
        d = input(f"  Folder RICOH watcher? Copas path (Enter=tetap, '-' = matikan): ").strip().strip('"').strip("'")
        if d == "-":
            cfg["ricoh_folder"] = ""
        elif d:
            cfg["ricoh_folder"] = d
        elif "ricoh_folder" not in cfg:
            cfg["ricoh_folder"] = ""
        save_cfg(cfg)
        print(f"  {D}tersimpan -> route.json{RST}\n")
    except (EOFError, KeyboardInterrupt):
        print(f"\n{D}Setup dibatalkan, pakai tersimpan.{RST}\n")
        udad = Path(cfg.get("uda_folder", str(ind.parent / "Uda")))
        if "ricoh_folder" not in cfg:
            cfg["ricoh_folder"] = ""
    ricoh = (cfg.get("ricoh_folder") or "").strip()
    return ind, retd, udad, (Path(ricoh) if ricoh else None)


def _is_unc(p: Path) -> bool:
    return str(p).startswith("\\\\")


def _exists(p: Path) -> bool:
    """Ada/tidaknya file. os.stat gagal (WinError 24) di file UNC server
    printer, fallback ke listdir parent (berfungsi)."""
    try:
        os.stat(str(p))
        return True
    except OSError:
        pass
    try:
        return p.name.lower() in (n.lower() for n in os.listdir(str(p.parent)))
    except OSError:
        return False


def _net_move(s_src: str, s_dst: str):
    """Pindah file UNC: copy streaming + hapus sumber. Tanpa stat
    (server menolak QUERY info, WinError 24), tanpa copystat."""
    with open(s_src, "rb") as fsrc, open(s_dst, "wb") as fdst:
        shutil.copyfileobj(fsrc, fdst, 1024 * 1024)
    os.remove(s_src)


def _list_pdfs(folder: Path):
    """List PDF di folder. Pakai os.listdir karena Path.glob/is_file
    error PermissionError di UNC path server printer (WinError 24)."""
    try:
        names = os.listdir(str(folder))
    except OSError:
        return []
    return sorted([folder / n for n in names if n.lower().endswith(".pdf")],
                  key=lambda p: p.name.lower())


_COPIED = set()  # nama file yang COPY-nya oleh sesi ini (boleh lanjut arsip)


def _overwrite(src: Path, dst: Path):
    """Timpa isi file tujuan dengan versi terbaru (streaming, tanpa stat)."""
    with open(str(src), "rb") as fsrc, open(str(dst), "wb") as fdst:
        shutil.copyfileobj(fsrc, fdst, 1024 * 1024)


def _copy_exact(src: Path, dst_dir: Path):
    """COPY file ke folder (nama asli dipertahankan).
    Tabrakan -> FileExistsError. Return dst Path."""
    dst = dst_dir / src.name
    if _exists(dst):
        raise FileExistsError(str(dst))
    if _is_unc(dst):
        with open(str(src), "rb") as fsrc, open(str(dst), "wb") as fdst:
            shutil.copyfileobj(fsrc, fdst, 1024 * 1024)
    else:
        shutil.copyfile(str(src), str(dst))
    return dst


def _safe_move(src: Path, dst_dir: Path, make_dir=True, exact=False):
    """Pindah file (antar-drive pakai copy+delete).
    exact=True: nama asli dipertahankan, tabrakan -> FileExistsError.
    exact=False: tabrakan -> suffix timestamp. Return dst Path."""
    if make_dir:
        dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    if _exists(dst):
        if exact:
            raise FileExistsError(str(dst))
        dst = dst_dir / f"{src.stem}_{int(_time.time())}{src.suffix}"
    if _is_unc(src) or _is_unc(dst):
        _net_move(str(src), str(dst))
    else:
        shutil.move(str(src), str(dst))
    return dst


def _log_quiet(key, color, tag, msg, detail=""):
    """Log error berulang max 1x per ERR_QUIET_S (anti-spam). Return True jika dilog."""
    now = _time.time()
    if now - _ERR_QUIET.get(key, 0) < ERR_QUIET_S:
        return False
    _ERR_QUIET[key] = now
    _log(color, tag, msg, detail)
    return True


def route_one(fn: Path, indir: Path, retdir: Path, udadir: Path):
    name = fn.name
    preset = llm_teacher_preset(name) or {}
    bahan = resolve_bahan(name, preset.get("bahan") or guess_bahan(name) or "")
    if not bahan and "master" in name.lower():
        bahan = "Master"  # KMA saja: keyword Master (kalkir/master tetap skip di Lite)
    pair = ROUTE.get(bahan)
    if not pair:
        dst = _safe_move(fn, retdir)
        _log(Y, "[KMA-E06]", _short(f"{name} -> RETURN"), "bahan tidak ada folder")
        if _ev(name, bahan, "RETURN", "return", "bahan tidak ada folder"):
            _bump("ret")
        return False
    is_booklet = str(preset.get("repeat", "")).startswith("booklet")
    duplex = preset.get("duplex", "1s")
    side = "2S" if (is_booklet or duplex in ("2s", "dr")) else "1S"
    folder = _folder(pair[1] if side == "2S" else pair[0])
    if not folder:
        dst = _safe_move(fn, retdir)
        _log(Y, "[KMA-E07]", _short(f"{name} -> RETURN"), f"{bahan} tidak ada folder {side}")
        if _ev(name, bahan, "RETURN", "return", f"{bahan} tidak ada folder {side}"):
            _bump("ret")
        return False
    try:
        # 1. Pastikan file ADA di hot folder (nama asli):
        #    - habis dimakan mesin -> COPY ulang (cetak ulang jalan)
        #    - copy-an sesi ini masih ada -> TIMPA versi terbaru (koreksi jalan)
        #    - milik sesi lain/job lain -> WAIT (jangan timpa job orang)
        dst = Path(HOT_SERVER) / folder / fn.name
        if name in _COPIED and _exists(dst):
            _overwrite(fn, dst)
        elif _exists(dst):
            _log(Y, "[ WAIT ]", _short(name), f"{folder} masih ada nama sama, coba lagi")
            _ev(name, bahan, folder, "wait", "nama sama di hot folder")
            return False
        else:
            _copy_exact(fn, Path(HOT_SERVER) / folder)
            _COPIED.add(name)
    except FileExistsError:
        _log(Y, "[ WAIT ]", _short(name), f"{folder} masih ada nama sama, coba lagi")
        _ev(name, bahan, folder, "wait", "nama sama di hot folder")
        return False
    except PermissionError:
        last = _COOLDOWN.get(name, 0)
        _COOLDOWN[name] = _time.time()
        if _time.time() - last >= COOLDOWN_S:
            _log(Y, "[ LOCK ]", _short(name), "terkunci, coba lagi")
        return False
    except OSError as e:
        if _log_quiet((name, "KMA-E05"), R, "[KMA-E05]", _short(name), f"COPY ke {folder} gagal: {e}"):
            _ev(name, bahan, folder, "error", f"COPY gagal: {str(e)[:60]}")
        return False
    try:
        # 2. COPY sukses -> tunggu 6 dtk -> asli di INPUT dipindah ke UDA (=DONE)
        _time.sleep(UDA_DELAY_S)
        if not _exists(fn):
            return False  # file keburu dipindah manual
        _safe_move(fn, udadir)
    except PermissionError:
        last = _COOLDOWN.get(name, 0)
        _COOLDOWN[name] = _time.time()
        if _time.time() - last >= COOLDOWN_S:
            _log(Y, "[ LOCK ]", _short(name), "terkunci, coba lagi")
        return False
    except OSError as e:
        if _log_quiet((name, "KMA-E08"), R, "[KMA-E08]", _short(name), f"arsip ke Uda gagal: {e}"):
            _ev(name, bahan, folder, "error", f"arsip gagal: {str(e)[:60]}")
        return False
    _log(G, "[ OK ]", _short(f"{name} -> {folder} (asli->Uda)"), ringkas(name))
    if _ev(name, bahan, folder, "ok", "asli->Uda"):
        _bump("ok")
    return True


def scan_once(indir: Path, retdir: Path, udadir: Path):
    if not indir.exists():
        _log(R, "[KMA-E02]", str(indir), "folder INPUT hilang/tak terjangkau")
        return
    files = _list_pdfs(indir)
    seen = set()
    for fn in files:
        key = str(fn).lower()
        if key in seen or not _exists(fn):
            continue
        seen.add(key)
        if _time.time() - _COOLDOWN.get(fn.name, 0) < COOLDOWN_S:
            continue
        try:
            route_one(fn, indir, retdir, udadir)
        except PermissionError:
            last = _COOLDOWN.get(fn.name, 0)
            _COOLDOWN[fn.name] = _time.time()
            if _time.time() - last >= COOLDOWN_S:
                _log(Y, "[ LOCK ]", _short(fn.name), "terkunci, coba lagi")
        except FileNotFoundError:
            pass
        except Exception as e:
            _log(R, "[ ERR ]", _short(fn.name), str(e)[:80])


def sweep_done(udadir: Path):
    """Pindah file selesai dari <hotfolder>/Done -> UDA (nama asli dipertahankan)."""
    folders = sorted({_folder(f) for pair in ROUTE.values() for f in pair if f})
    for folder in folders:
        done = Path(HOT_SERVER) / folder / "Done"
        files = _list_pdfs(done)
        seen = set()
        for fn in files:
            key = str(fn).lower()
            if key in seen or not _exists(fn):
                continue
            seen.add(key)
            try:
                _safe_move(fn, udadir)
                _log(C, "[ DONE ]", _short(f"{folder}/Done -> Uda"), _short(fn.name))
                if _ev(fn.name, "?", folder, "done", "Done->Uda"):
                    _bump("done")
            except PermissionError:
                continue
            except OSError as e:
                _log(R, "[KMA-E09]", _short(fn.name), f"Done->{folder} gagal: {e}")
                _ev(fn.name, "?", folder, "error", f"Done gagal: {str(e)[:60]}")


RICOH_POLL_S = 10  # fallback poll; utama = event watcher + tiap bangun loop
_RICOH_SEEN = set()  # nama file ricoh tercatat hari ini (dibagi thread + loop)
_RICOH_WAKE = threading.Event()  # dinyalakan watcher saat RICOH berubah


def _ricoh_scan(ricohdir: Path, seen: set):
    """Sekali scan folder RICOH: catat PDF baru ke DB (tanpa dipindah/diapa-apakan)."""
    try:
        files = _list_pdfs(ricohdir)
    except Exception:
        return seen
    for fn in files:
        if fn.name in seen:
            continue
        seen.add(fn.name)
        _log(C, "[RICOH]", _short(fn.name), "file ricoh tercatat")
        _ev(fn.name, guess_bahan(fn.name) or "Ricoh", "RICOH", "ricoh",
            "watcher", machine="RICOH")
    return seen


def _ricoh_seen_init():
    """Isi set terlihat dari events hari ini (anti-catat-ulang pasca-restart)."""
    _RICOH_SEEN.clear()
    _RICOH_SEEN.update(e["file"] for e in _EVENTS if e.get("status") == "ricoh")
    return _RICOH_SEEN


def _ricoh_loop(ricohdir: Path):
    """Thread watcher RICOH: hanya baca + hitung, tak ada kaitan fungsi lain."""
    _ricoh_seen_init()
    _log(C, "[RICOH]", f"watcher aktif: {ricohdir}", f"{len(_RICOH_SEEN)} tercatat hari ini")
    while True:
        _RICOH_WAKE.wait(RICOH_POLL_S)  # bangun saat ada aksi ATAU tiap 10 dtk
        _RICOH_WAKE.clear()
        try:
            _ricoh_scan(ricohdir, _RICOH_SEEN)
            push_kma()
        except Exception as e:
            _log(Y, "[RICOH]", "scan gagal", str(e)[:60])


_WAKE = threading.Event()  # dinyalakan watcher saat ada file masuk/berubah
_WATCH_OK = False


def _dir_watcher(path: Path, wake=None):
    """Thread: blokir di ReadDirectoryChangesW, set event saat ada aksi file.
    Tanpa polling — bangun hanya kalau ada yang masuk/berubah/dihapus."""
    global _WATCH_OK
    ev = wake if wake is not None else _WAKE
    import ctypes
    try:
        k32 = ctypes.windll.kernel32
        h = k32.CreateFileW(str(path), 0x0001, 0x00000001 | 0x00000002 | 0x00000004,
                            None, 3, 0x02000000, None)
        if h == -1:
            return
        _WATCH_OK = True
        from ctypes import wintypes
        buf = ctypes.create_string_buffer(4096)
        nbytes = wintypes.DWORD()
        while True:
            ok = k32.ReadDirectoryChangesW(h, buf, 4096, False, 0x00000003,
                                           ctypes.byref(nbytes), None, None)
            if ok:
                ev.set()
            else:
                break
        k32.CloseHandle(h)
    except Exception:
        return
    finally:
        ev.set()


def watch_loop():
    _hub_flags()
    _ensure_day()
    indir, retdir, udadir, ricohdir = ask_folders()
    _FOLDERS.update({"input": str(indir), "return": str(retdir), "uda": str(udadir),
                     "ricoh": str(ricohdir) if ricohdir else ""})
    for d, code in ((indir, "KMA-E02"), (retdir, "KMA-E03"), (udadir, "KMA-E04")):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            _log(R, f"[{code}]", str(d), f"folder tak bisa dibuat/ditulis: {e}")
            return
    if not check_server():
        return
    print(f"  Router aktif")
    print(f"    Input  : {C}{indir}{RST}")
    print(f"    Return : {C}{retdir}{RST}")
    print(f"    Uda    : {C}{udadir}{RST}")
    print(f"    Server : {C}{HOT_SERVER}{RST}")
    print(f"    RICOH  : {C}{ricohdir if ricohdir else '(watcher mati)'}{RST}")
    print(f"  Legenda: {G}[ OK ] hot folder{RST}  {Y}[ RET ] RETURN[KMA-E06/E07]{RST}  {C}[ DONE ] Done->Uda{RST}  {R}[KMA-E01/E02/E03/E04/E05/E08/E09]{RST}  {D}| Ctrl+C stop{RST}")
    print(f"  Pemicu: aksi file di INPUT (event, bukan polling) + cek printer")
    print(f"  ------------------------------------------------------")
    threading.Thread(target=_dir_watcher, args=(indir,), daemon=True).start()
    if ricohdir:
        _ricoh_seen_init()
        threading.Thread(target=_dir_watcher, args=(ricohdir, _RICOH_WAKE), daemon=True).start()
        threading.Thread(target=_ricoh_loop, args=(ricohdir,), daemon=True).start()
    _time.sleep(1)
    if not _WATCH_OK:
        _log(Y, "[ WARN ]", "event watcher gagal", "fallback polling 30 detik")
    scan_once(indir, retdir, udadir)
    sweep_done(udadir)
    cfg_mtime = _cfg_mtime()
    try:
        while True:
            _WAKE.wait(30)  # bangun saat ada aksi file; jaring pengaman 30 dtk
            _WAKE.clear()
            if _cfg_mtime() != cfg_mtime:
                _restart_self("route.json berubah")
                return  # tak tercapai bila exec sukses
            if not server_ok_quiet():
                _WAKE.wait(15)  # printer mati: cek pulih tiap 15 dtk
                _WAKE.clear()
                if ricohdir:
                    _ricoh_scan(ricohdir, _RICOH_SEEN)
                push_kma()
                continue
            scan_once(indir, retdir, udadir)
            sweep_done(udadir)
            if ricohdir:
                _ricoh_scan(ricohdir, _RICOH_SEEN)
            push_kma()
    except KeyboardInterrupt:
        print(f"\n  {D}stop. OK={_STATS['ok']} RET={_STATS['ret']} DONE={_STATS['done']}{RST}")


if __name__ == "__main__":
    if "--once" in sys.argv:
        _hub_flags()
        _ensure_day()
        _banner()
        cfg = load_cfg()
        indir = Path(cfg.get("input_folder", str(BASE / "input")))
        retdir = Path(cfg.get("return_folder", str(BASE / "return")))
        udadir = Path(cfg.get("uda_folder", str(indir.parent / "Uda")))
        _FOLDERS.update({"input": str(indir), "return": str(retdir), "uda": str(udadir)})
        _FOLDERS["ricoh"] = (cfg.get("ricoh_folder") or "").strip()
        for d, code in ((indir, "KMA-E02"), (retdir, "KMA-E03"), (udadir, "KMA-E04")):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                _log(R, f"[{code}]", str(d), f"folder tak bisa dibuat/ditulis: {e}")
                raise SystemExit(1)
        if not check_server():
            raise SystemExit(1)
        scan_once(indir, retdir, udadir)
        sweep_done(udadir)
        _rpath = (_FOLDERS.get("ricoh") or "").strip()
        if _rpath:
            _ricoh_scan(Path(_rpath), _ricoh_seen_init())
        push_kma()
        print(f"  {D}sekali jalan. OK={_STATS['ok']} RET={_STATS['ret']} DONE={_STATS['done']}{RST}")
    else:
        watch_loop()
