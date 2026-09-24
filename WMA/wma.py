"""WMA v2 — Pusat Database KMMA
Ambil data selesai dari Watcher Dashboard, parse PDF filename,
agregasi per (nota + bahan), output wma_daily.json untuk KMMA.

Alur:
  1. Fetch semua file dari Dashboard API
  2. Filter: status=done + verified=true + filename=.pdf
  3. Parse filename → nota, bahan, qty, process_type
  4. Agregasi per (nota, bahan) — gabung hanya jika nota DAN bahan sama
  5. Output wma_daily.json

Pakai:
  python wma.py              → sync sekali, exit
  python wma.py --loop       → sync tiap 60 detik
  python wma.py --watch      → event-driven: poll API tiap 10s
  python wma.py --date DATE  → sync tanggal tertentu
  python wma.py --show       → tampilkan isi wma_daily.json
"""
import datetime
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # NSH Tools root: ui, ws_client
sys.path.insert(0, str(Path(__file__).parent.parent / "Database"))  # db.py pusat
try:
    import db as nshdb
    HAVE_DB = True
except Exception:
    nshdb = None
    HAVE_DB = False

from ui import (
    header, section, ok, err, warn, info, dim,
    BOLD, DIM, RESET, GREEN, RED, YELLOW, CYAN, GRAY,
)
from ws_client import WSClient

# ════════════════════════════════════════════════════════════════
# SECTION 1: CONFIG & CONSTANTS
# ════════════════════════════════════════════════════════════════

BASE = Path(__file__).parent
CFG_PATH = BASE / "wma.json"
DAILY_PATH = BASE / "wma_daily.json"
KMMA_PATH = BASE.parent / "KMMA" / "kmma.json"

WMA_VERSION = "2"
DASH_TIMEOUT_S = 15
SYNC_INTERVAL_S = 60

# ════════════════════════════════════════════════════════════════
# SECTION 2: REGEX — parse PDF filename
# ════════════════════════════════════════════════════════════════

R_NOTA = re.compile(r"(ON\d{12})")
R_BAHAN = re.compile(
    r"(Ap\d+gr|Ac\d+gr|(?:Stiker|Sticker)\s+\w+|HVS|Concord|BC\s+\w+|Master|BNTU"
    r"|NewToop\s*\d+\s*mic|Toop\s*\d+\s*mic|Art\s*Paper|Art\s*Carton|ArtPaper|ArtCarton"
    r"|(?:Kertas\s+)?[KC]rom[oe]"
    r"|Vinyl|Vyinil|Rajawali|Pindo|Jasmine|Ivory|Transparant|Hologram|Gold|Silver)",
    re.I,
)
R_TOTAL = re.compile(r"TOTAL\s*=\s*(\d+)\s*(BESAR|KECIL)", re.I)
R_KISSCUT = re.compile(r"kiss\s*cut", re.I)
R_DIECUT = re.compile(r"die\s*cut", re.I)
R_PDF = re.compile(r"\.pdf$", re.I)
R_JOB_PREFIX = re.compile(r"^[A-Z]{2,}-\d+\s*-\s*")
R_SIDES = re.compile(r"(?<![a-z0-9])([12])\s*s(?![a-z])", re.I)  # 1s / 2s
R_LAM = re.compile(r"(?<![a-z])(doff?|glossy?)(?![a-z])", re.I)  # doff / glossy


def extract_customer(filename, bahan=""):
    """Judul job/pelanggan dari nama file: 'KNT-10030 - <CUSTOMER> _<bahan> ...'."""
    name = re.sub(r"\.pdf$", "", filename or "", flags=re.I).strip()
    name = R_JOB_PREFIX.sub("", name)
    if bahan:
        i = name.lower().find("_" + bahan.lower())
        if i > 0:
            name = name[:i]
    for pat in (r"\s+-\s*\(.*$", r"\s+TOTAL\s*=.*$",
                r"\s*\([^()]*ON\d{12}[^()]*\)\s*$"):
        name = re.sub(pat, "", name, flags=re.I).strip()
    return name.strip()[:60]


# ════════════════════════════════════════════════════════════════
# SECTION 3: CONFIG I/O
# ════════════════════════════════════════════════════════════════

def load_cfg():
    try:
        return json.loads(CFG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def save_cfg(cfg):
    tmp = CFG_PATH.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(CFG_PATH)
    except Exception as e:
        print(f"  {err(f'gagal simpan config: {e}')}")


def get_dash_url():
    cfg = load_cfg()
    return cfg.get("dash_url", "http://192.168.5.54:5000")


# ════════════════════════════════════════════════════════════════
# SECTION 4: DASHBOARD API
# ════════════════════════════════════════════════════════════════

_API_LAST_ERR = ""

def _api_get(path, retries=3):
    global _API_LAST_ERR
    url = get_dash_url() + path
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=DASH_TIMEOUT_S) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            _API_LAST_ERR = f"{type(e).__name__}: {str(e)[:100]}"
            if attempt < retries - 1:
                time.sleep(1 * (attempt + 1))
    return None


# ════════════════════════════════════════════════════════════════
# SECTION 5: PDF FILENAME PARSING
# ════════════════════════════════════════════════════════════════

def parse_pdf_filename(filename):
    """Parse PDF filename → dict(nota, bahan, qty, jenis, process_type, customer, sides, laminating)."""
    result = {
        "nota": None,
        "bahan": None,
        "qty": None,
        "jenis": None,
        "process_type": None,
        "customer": "",
        "sides": "",
        "laminating": "",
    }

    # nota
    m = R_NOTA.search(filename)
    if m:
        result["nota"] = m.group(1)

    # bahan
    m = R_BAHAN.search(filename)
    if m:
        result["bahan"] = m.group(1).strip()

    # customer (butuh bahan dulu untuk marker potong)
    result["customer"] = extract_customer(filename, result["bahan"] or "")

    # qty + jenis
    m = R_TOTAL.search(filename)
    if m:
        result["qty"] = int(m.group(1))
        result["jenis"] = m.group(2).upper()

    # process type
    if R_KISSCUT.search(filename):
        result["process_type"] = "kisscut"
    elif R_DIECUT.search(filename):
        result["process_type"] = "diecut"

    # sisi cetak 1s/2s (beda = tidak gabung)
    m = R_SIDES.search(filename)
    result["sides"] = (m.group(1) + "s") if m else ""

    # laminating doff/glossy (beda = tidak gabung)
    m = R_LAM.search(filename)
    low_lam = (m.group(1) or "").lower() if m else ""
    result["laminating"] = ("doff" if low_lam.startswith("do") else "glossy") if low_lam else ""

    return result


# ════════════════════════════════════════════════════════════════
# SECTION 6: FETCH & FILTER
# ════════════════════════════════════════════════════════════════

def fetch_completed(date_str):
    """Fetch file selesai dari dashboard: status=done + verified + .pdf only."""
    path = f"/api/files?date_from={date_str}&date_to={date_str}"
    data = _api_get(path)
    if not data:
        print(f"  {err(f'gagal fetch dashboard ({_API_LAST_ERR})')}")
        return []

    # API bisa return list langsung atau {"value": [...]}
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict) and "value" in data:
        rows = data["value"]
    else:
        print(f"  {err('format response tidak dikenal')}")
        return []

    completed = []
    for r in rows:
        # filter: status done + pdf only (bukan verified)
        if r.get("status") != "done":
            continue
        fname = r.get("filename", "")
        if not R_PDF.search(fname):
            continue
        completed.append(r)

    return completed


# ════════════════════════════════════════════════════════════════
# SECTION 7: AGGREGATE PER NOTA
# ════════════════════════════════════════════════════════════════

def aggregate_by_nota(rows):
    """Group rows by (nota, bahan, sides, laminating). Hanya gabung jika
    SEMUA sama: beda 1s/2s = tidak gabung, beda laminating Doff/Glossy = tidak gabung."""
    groups = {}

    for r in rows:
        fname = r.get("filename", "")
        parsed = parse_pdf_filename(fname)
        nota = parsed["nota"]
        bahan = parsed["bahan"]

        # skip file tanpa nota
        if not nota:
            print(f"  {warn(f'skip tanpa nota: {fname[:50]}')}")
            continue

        # key = (nota, bahan, sides, laminating)
        sides = parsed.get("sides") or ""
        lam = parsed.get("laminating") or ""
        key = (nota, bahan, sides, lam)

        if key not in groups:
            groups[key] = {
                "nota": nota,
                "bahan": bahan,
                "customer": parsed["customer"] or "",
                "sides": sides,
                "laminating": lam,
                "total_qty": 0,
                "jenis": parsed["jenis"],
                "process_type": parsed["process_type"],
                "operator": r.get("operator", ""),
                "machine": r.get("machine_name", ""),
                "files": [],
            }

        groups[key]["total_qty"] += parsed["qty"] or 0
        groups[key]["files"].append(fname)

        # update customer/jenis jika kosong
        if not groups[key]["customer"] and parsed["customer"]:
            groups[key]["customer"] = parsed["customer"]

        # update jenis jika kosong
        if not groups[key]["jenis"] and parsed["jenis"]:
            groups[key]["jenis"] = parsed["jenis"]

        # update process_type jika kosong
        if not groups[key]["process_type"] and parsed["process_type"]:
            groups[key]["process_type"] = parsed["process_type"]

    return list(groups.values())


# ════════════════════════════════════════════════════════════════
# SECTION 8: SAVE / LOAD DAILY
# ════════════════════════════════════════════════════════════════

def save_daily(entries, date_str):
    """Simpan entries: Database (utama) + wma_daily.json (sinyal watchdog KMMA + cadangan)."""
    if HAVE_DB:
        try:
            nshdb.wma_replace(date_str, entries)
        except Exception as e:
            print(f"  {err(f'gagal simpan db: {e}')}")
    data = {
        "date": date_str,
        "entries": entries,
    }
    tmp = DAILY_PATH.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(DAILY_PATH)
    except Exception as e:
        print(f"  {err(f'gagal simpan daily: {e}')}")


def load_daily():
    """Baca wma_daily.json."""
    try:
        data = json.loads(DAILY_PATH.read_text(encoding="utf-8"))
        return data
    except FileNotFoundError:
        return None
    except Exception:
        return None


# ════════════════════════════════════════════════════════════════
# SECTION 9: DISPLAY
# ════════════════════════════════════════════════════════════════

def show_daily():
    """Tampilkan isi wma_daily.json."""
    data = load_daily()
    if not data:
        print(f"  {warn('wma_daily.json kosong atau tidak ditemukan')}")
        return

    date = data.get("date", "?")
    entries = data.get("entries", [])

    print()
    print(f"  {BOLD}WMA Daily Database{RESET}")
    print(f"  Date: {CYAN}{date}{RESET}")
    print(f"  Entries: {GREEN}{len(entries)}{RESET}")
    print()

    if not entries:
        print(f"  {dim('(tidak ada data)')}")
        return

    for i, e in enumerate(entries, 1):
        nota = e.get("nota", "?")
        bahan = e.get("bahan", "?")
        qty = e.get("total_qty", 0)
        jenis = e.get("jenis", "?")
        proc = e.get("process_type") or "digital"
        op = e.get("operator", "?")
        mac = e.get("machine", "?")
        cust = e.get("customer", "") or "-"
        files = e.get("files", [])
        sides = e.get("sides", "") or ""
        lam = e.get("laminating", "") or ""
        tag = f" [{sides.upper()}" + (f"/{lam.upper()}" if lam else "") + "]" if sides or lam else ""

        cat = proc.upper()
        print(f"  {i:>2}. {BOLD}{nota}{RESET}  [{CYAN}{cat}{RESET}]  {cust} | {bahan} x {qty} {jenis}  |  {op}  |  {mac}{tag}")
        for f in files:
            print(f"      {dim(f[:70])}")

    print()


# ════════════════════════════════════════════════════════════════
# SECTION 10: KMMA STATE (skip nota yang sudah diproses)
# ════════════════════════════════════════════════════════════════

def get_processed_notas():
    """Set nota yang sudah diproses/skip — sumber utama Database."""
    if HAVE_DB:
        try:
            with nshdb.get_conn() as con:
                return {r[0] for r in con.execute("SELECT nota FROM kmma_state")}
        except Exception:
            pass
    processed = set()
    try:
        data = json.loads(KMMA_PATH.read_text(encoding="utf-8"))
        # completed: nota → timestamp
        processed.update(data.get("completed", {}).keys())
        # skip_forever: nota → reason
        processed.update(data.get("skip_forever", {}).keys())
        # skipped_nota: nota → {reason, last_try}
        processed.update(data.get("skipped_nota", {}).keys())
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return processed


# ════════════════════════════════════════════════════════════════
# SECTION 11: SYNC
# ════════════════════════════════════════════════════════════════

def sync(date_str=None, ws_client=None):
    """Fetch → filter → parse → aggregate → save."""
    if not date_str:
        date_str = datetime.date.today().isoformat()

    print()
    print(f"  {BOLD}WMA v{WMA_VERSION} — Sync{RESET}")
    print(f"  Dashboard: {dim(get_dash_url())}")
    print(f"  Date: {CYAN}{date_str}{RESET}")
    if ws_client and ws_client.connected:
        print(f"  Hub: {dim('connected')}")
    print()

    # fetch
    print(f"  {dim('fetching completed files...')}")
    rows = fetch_completed(date_str)
    print(f"  {ok(f'{len(rows)} files selesai (PDF)')}")
    print()

    if not rows:
        print(f"  {warn('tidak ada file selesai hari ini')}")
        save_daily([], date_str)
        return

    # aggregate
    entries = aggregate_by_nota(rows)
    print(f"  {ok(f'{len(entries)} nota (setelah agregasi)')}")
    print()

    # filter: hapus nota yang sudah diproses KMMA
    processed = get_processed_notas()
    before = len(entries)
    entries = [e for e in entries if e["nota"] not in processed]
    skipped = before - len(entries)
    if skipped:
        print(f"  {dim(f'hapus {skipped} nota sudah diproses KMMA')}")
        print()

    # display
    if not entries:
        print(f"  {warn('semua nota sudah diproses KMMA')}")
        save_daily([], date_str)
        return

    for i, e in enumerate(entries, 1):
        nota = e.get("nota", "?")
        bahan = e.get("bahan", "?")
        qty = e.get("total_qty", 0)
        jenis = e.get("jenis", "?")
        proc = e.get("process_type") or "digital"
        op = e.get("operator", "?")
        mac = e.get("machine", "?")
        cust = e.get("customer", "") or "-"
        sides = e.get("sides", "") or ""
        lam = e.get("laminating", "") or ""
        tag = f" [{sides.upper()}" + (f"/{lam.upper()}" if lam else "") + "]" if sides or lam else ""

        cat = proc.upper()
        print(f"  {i:>2}. {BOLD}{nota}{RESET}  [{CYAN}{cat}{RESET}]  {cust} | {bahan} x {qty} {jenis}  |  {op}  |  {mac}{tag}")

    # save
    save_daily(entries, date_str)
    print()
    print(f"  {ok(f'saved -> {DAILY_PATH.name}')}")
    print()

    # push to hub via WebSocket
    if ws_client and ws_client.connected:
        ws_client.send_entries(entries)
        print(f"  {ok(f'pushed ke hub ({len(entries)} entries)')}")
        print()


# ════════════════════════════════════════════════════════════════
# SECTION 12: MAIN
# ════════════════════════════════════════════════════════════════

def main():
    args = sys.argv[1:]

    # --show
    if "--show" in args:
        show_daily()
        return

    # --hub
    ws_client = None
    if "--hub" in args:
        hub_url = None
        if "--hub-url" in args:
            idx = args.index("--hub-url")
            if idx + 1 < len(args):
                hub_url = args[idx + 1]
        ws_client = WSClient("wma", hub_url=hub_url)
        ws_client.connect()
        print(f"  {ok('WebSocket client started')}")

    # --date DATE
    date_str = None
    if "--date" in args:
        idx = args.index("--date")
        if idx + 1 < len(args):
            date_str = args[idx + 1]
        else:
            print(f"  {err('--date butuh tanggal (YYYY-MM-DD)')}")
            return

    # --loop
    if "--loop" in args:
        print(f"  {BOLD}WMA v{WMA_VERSION} — Loop Mode{RESET}")
        print(f"  Interval: {SYNC_INTERVAL_S}s")
        print(f"  Tekan Ctrl+C untuk stop")
        while True:
            try:
                sync(date_str, ws_client=ws_client)
                print(f"  {dim(f'next sync in {SYNC_INTERVAL_S}s...')}")
                time.sleep(SYNC_INTERVAL_S)
            except KeyboardInterrupt:
                print(f"\n  {ok('stopped')}")
                break
        return

    # --watch: event-driven, sync hanya saat ada file baru
    if "--watch" in args:
        print(f"  {BOLD}WMA v{WMA_VERSION} — Watch Mode{RESET}")
        print(f"  Sync saat ada file baru (60s poll)")
        print(f"  Tekan Ctrl+C untuk stop")
        last_hash = None
        while True:
            try:
                date_str_w = date_str or datetime.date.today().isoformat()
                rows = fetch_completed(date_str_w)
                current_hash = json.dumps([r.get("filename", "") for r in rows], sort_keys=True)

                if current_hash != last_hash:
                    last_hash = current_hash
                    sync(date_str, ws_client=ws_client)
                else:
                    ts_now = datetime.datetime.now().strftime("%H:%M:%S")
                    print(f"  {dim(f'[{ts_now}] no changes, 60s...')}")

                time.sleep(SYNC_INTERVAL_S)
            except KeyboardInterrupt:
                print(f"\n  {ok('stopped')}")
                break
        return

    # default: sync sekali
    sync(date_str, ws_client=ws_client)


if __name__ == "__main__":
    main()
