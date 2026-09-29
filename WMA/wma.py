"""WMA v2 — Pusat Database KMMA
Ambil data selesai dari Watcher Dashboard, parse PDF filename,
agregasi per (nota + bahan), output wma_daily.json untuk KMMA.

Alur:
  1. Fetch semua file dari Dashboard API
  2. Filter: status=done + verified=true + filename=.pdf
  3. Parse filename → nota, bahan, qty, process_type
  4. Agregasi per (nota, bahan) — gabung hanya jika nota DAN bahan sama
  5. Output wma_daily.json

Sumber data (Eye Of Wisdom FASE 3 — hub primer, .54 cadangan):
  1. hub-sendiri (PRIMER): baca langsung Database/nsh.db → we_printed_day
     (hub HTTP butuh Bearer yg tak dimiliki WMA — lihat fetch_printed_from_hub)
  2. .54-cadangan (FALLBACK): HTTP dashboard .54 → fetch_completed
  GAGAL semua sumber = skip siklus (jangan hapus antrean!)

Pakai:
  python wma.py              → sync sekali, exit
  python wma.py --loop       → sync tiap 60 detik
  python wma.py --watch      → event-driven: poll API tiap 10s
  python wma.py --date DATE  → sync tanggal tertentu
  python wma.py --show       → tampilkan isi wma_daily.json
  python wma.py --compare [--date DATE] [--hub-base URL|auto]
                             → bandingkan kedua sumber, read-only, exit
"""
import datetime
import json
import os
import re
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # NSH Tools root: ui, ws_client
sys.path.insert(0, str(Path(__file__).parent.parent / "Database"))  # db.py pusat
sys.path.insert(0, str(Path(__file__).parent.parent / "NSH Lite"))  # bahan_dict kanonis
try:
    import db as nshdb
    HAVE_DB = True
except Exception:
    nshdb = None
    HAVE_DB = False
try:
    from bahan_dict import guess_bahan as _guess_bahan
except Exception:
    _guess_bahan = None
try:
    from konica_printer import resolve_bahan as _resolve_bahan
except Exception:
    _resolve_bahan = None

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
WE_DEBOUNCE_S = 5  # jeda min antar sync yg dipicu event WE (anti badai fetch)

# Pemicu realtime: hub menyiarkan we_update tiap WE berubah -> WS thread
# memanggil balik -> loop utama bangun & sync seketika (bukan nunggu 60 dtk).
_WE_TRIGGER = threading.Event()
_WE_LAST_POLL = 0.0  # ts poll terakhir (untuk debounce)

# Override diagnostik (khusus tes/paralel-run, TANPA ubah file config):
#   WMA_DASH_URL → ganti base .54 yg dipakai fetch_completed bila set
#   WMA_HUB_BASE → paksa fetch hub via HTTP bila set ("auto" = turunkan dari --hub-url/hub.json)
DASH_URL_ENV = "WMA_DASH_URL"
HUB_BASE_ENV = "WMA_HUB_BASE"

# Nilai --hub-url CLI (diisi main(), dipakai turunan base HTTP hub bila "auto")
_HUB_URL_CLI = None

CRASH_LOG = BASE / "wma_crash.log"  # pythonw tak ada console -> traceback wajib ditulis file


def _we_changed(msg=None):
    """Callback WS thread saat hub siarkan we_update: bangunkan loop utama
    agar sync seketika. Cepat + tak pernah raise (dipanggil dari thread WS)."""
    try:
        _WE_TRIGGER.set()
    except Exception:
        pass


def log_crash(tag: str):
    """Catat traceback ke console + file. Modul HARUS tetap hidup: dulu exception
    di loop utama mematikan proses -> WS ke hub putus -> reconnect terus-menerus."""
    tb = traceback.format_exc()
    try:
        print(f"  {err(f'[ERROR] {tag} — modul tetap jalan, lihat {CRASH_LOG.name}')}")
        print(tb)
    except Exception:
        pass
    try:
        with open(CRASH_LOG, "a", encoding="utf-8", errors="replace") as f:
            f.write(f"\n=== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} {tag} ===\n{tb}")
    except Exception:
        pass

# ════════════════════════════════════════════════════════════════
# SECTION 2: REGEX — parse PDF filename
# ════════════════════════════════════════════════════════════════

R_NOTA = re.compile(r"(ON\d{12})")
# R_BAHAN dibangun dari CANONICAL (sumber kebenaran) + pola tambahan; sinkron dengan KMMA
try:
    from bahan_dict import CANONICAL as _CANONICAL_WMA
except Exception:
    _CANONICAL_WMA = None

def _build_r_bahan_wma():
    extra = r"Ap\d+gr|Ac\d+gr|(?:Stiker|Sticker)\s+\w+|BC\s+\w+|Master|BNTU|NewToop\s*\d+\s*mic|Toop\s*\d+\s*mic|Art\s*Paper|Art\s*Carton|ArtPaper|ArtCarton|HVS|Concord"
    if _CANONICAL_WMA:
        canon_terms = sorted({k for k in _CANONICAL_WMA.keys()}, key=len, reverse=True)
        canon_pat = "|".join(re.escape(t) for t in canon_terms)
        typo_extra = r"Krom[oe]|Crom[oe]|Chromo|Vinyl|Vyinil|Vynil|Rajawali|Pindo|Jasmine|Ivory|Transparant|Hologram|Gold|Silver"
        return re.compile(rf"({extra}|(?:Kertas\s+)?(?:{typo_extra})|{canon_pat})", re.I)
    return re.compile(
        r"(Ap\d+gr|Ac\d+gr|(?:Stiker|Sticker)\s+\w+|HVS|Concord|BC\s+\w+|Master|BNTU"
        r"|NewToop\s*\d+\s*mic|Toop\s*\d+\s*mic|Art\s*Paper|Art\s*Carton|ArtPaper|ArtCarton"
        r"|(?:Kertas\s+)?[KC]rom[oe]"
        r"|Vinyl|Vyinil|Rajawali|Pindo|Jasmine|Ivory|Transparant|Hologram|Gold|Silver)",
        re.I,
    )

R_BAHAN = _build_r_bahan_wma()
R_TOTAL = re.compile(r"TOTAL\s*=\s*(\d+)\s*(BESAR|KECIL)", re.I)
R_KISSCUT = re.compile(r"kiss\s*cut", re.I)
R_DIECUT = re.compile(r"die\s*cut", re.I)
R_PDF = re.compile(r"\.pdf$", re.I)
R_TEST = re.compile(r"(?<![a-z])tes(t)?(?![a-z])", re.I)  # whole-word test/tes, case-insensitive (anti Contesa/PT Tesa)
R_JOB_PREFIX = re.compile(r"^[A-Z]{2,}-\d+\s*-\s*")
R_SIDES = re.compile(r"(?<![a-z0-9])([12])\s*s(?![a-z])", re.I)  # 1s / 2s
R_LAM = re.compile(r"(?<![a-z])(doff?|glossy?)(?![a-z])", re.I)  # doff / glossy


def extract_customer(filename, bahan=""):
    """Judul job/pelanggan dari nama file: 'KNT-10030 - <CUSTOMER> _<bahan> ...'.
    bahan = raw_bahan mentah (agar potong cocok); bila raw="" jangan potong,
    fallback kanonik boundary (\\b)."""
    name = re.sub(r"\.pdf$", "", filename or "", flags=re.I).strip()
    name = R_JOB_PREFIX.sub("", name)
    if bahan and bahan.strip():
        bl = bahan.strip().lower()
        low = name.lower()
        # 1) potong di marker mentah "_<bahan>"
        i = low.find("_" + bl)
        if i > 0:
            name = name[:i]
        else:
            # 2) fallback kanonik boundary: cari bahan sebagai kata terpisah tanpa underscore
            #    (mis. kanonik "kromo" vs raw "cromo" sudah tertangani di caller via raw,
            #     tapi bila raw="" kita coba boundary kata)
            try:
                m = re.search(r"\b" + re.escape(bl) + r"\b", low)
                if m and m.start() > 0:
                    # potong sebelum bahan, bersihkan trailing delimiter
                    name = name[:m.start()].rstrip(" _-")
            except Exception:
                pass
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
    # Hook override diagnostik (tes/paralel-run): env menang bila set,
    # produksi tak berubah (env normalnya kosong → wma.json → default .54)
    env = (os.environ.get(DASH_URL_ENV) or "").strip()
    if env:
        return env.rstrip("/")
    cfg = load_cfg()
    return cfg.get("dash_url", "http://192.168.5.54:5000")


def get_hub_http_base(hub_url=None):
    """Turunkan base HTTP hub dari URL WS (--hub-url / hub.json):
    ws://host:port → http://host:port+1 (hub: WS 8765 + HTTP 8766,
    lihat hub_server.py WS_PORT/HTTP_PORT). WMA kenal URL hub via
    --hub-url (opsional) atau hub.json (default WSClient).
    Dipakai HANYA jalur HTTP opsional (--compare --hub-base auto);
    sync rutin TIDAK pakai ini (primer = baca DB langsung)."""
    import urllib.parse
    raw = ((hub_url if hub_url is not None else _HUB_URL_CLI) or "").strip()
    if not raw:
        try:
            raw = (json.loads((BASE.parent / "hub.json").read_text(encoding="utf-8")).get("url") or "").strip()
        except Exception:
            raw = ""
    if not raw:
        raw = "ws://localhost:8765"
    try:
        p = urllib.parse.urlsplit(raw if "://" in raw else "ws://" + raw)
        host = p.hostname or "localhost"
        port = p.port
        port = (port + 1) if port else 8766  # default WS 8765 → HTTP 8766
        scheme = "https" if (p.scheme or "").lower() in ("wss", "https") else "http"
        return f"{scheme}://{host}:{port}"
    except Exception:
        return "http://localhost:8766"


# ════════════════════════════════════════════════════════════════
# SECTION 4: DASHBOARD API
# ════════════════════════════════════════════════════════════════

_API_LAST_ERR = ""

def _api_get(path, retries=3, base=None):
    global _API_LAST_ERR
    url = (base or get_dash_url()) + path
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

    # bahan (regex lokal sbg basis) lalu kanonisasi via pipeline yang sama
    # dgn KMA/Lite (guess_bahan + resolve_bahan), mis. "Stiker Cromo"
    # (typo) -> "Stiker Kromo". Regex dipertahankan sbg fallback utk bahan
    # yg tak ada di kamus (Master/BNTU/Toop/Art Paper/BC).
    m = R_BAHAN.search(filename)
    raw_bahan = m.group(1).strip() if m else ""
    if m:
        result["bahan"] = raw_bahan
    try:
        if _guess_bahan is not None:
            _canon = _guess_bahan(filename)
            if _canon:
                result["bahan"] = (_resolve_bahan(filename, _canon)
                                   if _resolve_bahan is not None else _canon)
    except Exception:
        pass

    # customer: pakai bahan MENTAH (sesuai teks file) agar marker potong cocok;
    # bahan kanonis (mis. Cromo->Kromo) tak ada di teks file.
    # bila raw=="" jangan potong pakai kanonis langsung — biarkan fallback boundary di extract_customer
    if raw_bahan:
        result["customer"] = extract_customer(filename, raw_bahan)
    else:
        # raw kosong: jangan potong pakai kanonis mentah; panggil dengan "" (tak potong)
        # atau bila kanon ada, biarkan extract_customer coba fallback boundary kata
        # tapi hanya bila kanon berbeda dari raw (mis. deteksi typo tanpa capture regex)
        _canon_for_customer = result.get("bahan") or ""
        if _canon_for_customer and _canon_for_customer.lower() not in (filename or "").lower():
            result["customer"] = extract_customer(filename, "")
        else:
            result["customer"] = extract_customer(filename, _canon_for_customer if _canon_for_customer else "")

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

def fetch_completed(date_str, base_url=None):
    """Fetch file selesai dari dashboard .54 (CADANGAN): status=done + .pdf only.
    BEDA error vs kosong (penjaga data-loss FASE 3):
      None = transport/parse GAGAL → panggil WAJIB skip siklus (jangan tulis/hapus apa pun!)
      []   = sumber positif lapor nol baris → perilaku kosong lama (save_daily([])) berlaku."""
    path = f"/api/files?date_from={date_str}&date_to={date_str}"
    data = _api_get(path, base=(base_url or None))
    if data is None:
        print(f"  {err(f'gagal fetch dashboard ({_API_LAST_ERR})')}")
        return None

    # API bisa return list langsung atau {"value": [...]}
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict) and isinstance(data.get("value"), list):
        rows = data["value"]
    else:
        _set_api_err("format response tidak dikenal")
        print(f"  {err('format response tidak dikenal')}")
        return None

    completed = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        # filter: status done + pdf only (bukan verified)
        if r.get("status") != "done":
            continue
        fname = r.get("filename", "")
        if not R_PDF.search(fname or ""):
            continue
        completed.append(r)

    return completed


def _set_api_err(msg):
    global _API_LAST_ERR
    _API_LAST_ERR = str(msg)[:100]


def _norm_hub_rows(printed):
    """Samakan bentuk baris hub (we_printed) dgn baris .54 pasca-filter
    fetch_completed: {filename, operator, machine_name, status} agar
    aggregate_by_nota/filter/save/push SAMA untuk kedua sumber."""
    out = []
    for r in printed or []:
        if not isinstance(r, dict):
            continue
        fn = (r.get("filename") or "").strip()
        if not fn or not R_PDF.search(fn):
            continue
        out.append({
            "filename": fn,
            "operator": r.get("operator") or "",
            # hub pakai kunci "machine", .54 pakai "machine_name"
            "machine_name": r.get("machine") or r.get("machine_name") or "",
            "status": "done",
        })
    return out


def fetch_printed_from_hub(date_str, hub_base=None):
    """Sumber PRIMER WMA: file tercetak dari sistem sendiri.
    KEPUTUSAN primer = baca langsung DB (BUKAN hub HTTP), bukti di hub_server.py:
      - GET /api/we/state WAJIB sesi Bearer: do_GET balas 401 bila _auth_lookup
        gagal; komentar kode "user boleh" = user TERAUTENTIKASI, bukan bebas-auth.
      - WS subscribe role=wma memang dikecualikan ("trusted internal"), tapi
        pengecualian itu HANYA di jalur WS — tak ada padanan di jalur HTTP.
      - WMA tak punya token/kredensial login → hub HTTP rutin tak bisa dipakai;
        melemahkan auth hub DILARANG (hub out of scope FASE 3).
      - Maka primer = Database/nsh.db → we_printed_day (DB yg sama dibaca hub
        via _hub_db); hub HTTP hanya bila hub_base eksplisit (hook diagnostik
        --compare --hub-base / env WMA_HUB_BASE, tanpa auth → 401 di produksi
        = None → jatuh ke .54).
    BEDA error vs kosong spt fetch_completed: None = GAGAL, [] = positif nol baris."""
    hb = (hub_base if hub_base is not None
          else (os.environ.get(HUB_BASE_ENV) or "").strip())
    if hb == "auto":
        hb = get_hub_http_base()
    if hb:
        # Jalur HTTP opsional (diagnostik/tes). Tanpa header auth: hub balas 401
        # bila butuh sesi → _api_get kembalikan None → None = gagal → fallback .54.
        data = _api_get(f"/api/we/state?date={date_str}", base=hb.rstrip("/"))
        if not isinstance(data, dict):
            print(f"  {err(f'gagal fetch hub HTTP ({_API_LAST_ERR})')}")
            return None
        printed = data.get("printed")
        if not isinstance(printed, list):
            _set_api_err("response hub tanpa daftar printed")
            print(f"  {err('response hub tanpa daftar printed')}")
            return None
        return _norm_hub_rows(printed)
    # Jalur PRIMER: baca langsung DB sendiri
    if not HAVE_DB or nshdb is None:
        _set_api_err("db pusat tak tersedia (HAVE_DB=False)")
        print(f"  {err('db pusat tak tersedia (HAVE_DB=False)')}")
        return None
    try:
        printed = nshdb.we_printed_day(date_str)
    except Exception as e:
        _set_api_err(f"{type(e).__name__}: {str(e)[:100]}")
        print(f"  {err(f'gagal baca we_printed ({_API_LAST_ERR})')}")
        return None
    if printed is None:
        _set_api_err("we_printed_day kembalikan None")
        print(f"  {err('we_printed_day kembalikan None')}")
        return None
    return _norm_hub_rows(printed)


def _fetch_best(date_str):
    """Urutan sumber FASE 3: (1) hub-sendiri, (2) .54-cadangan.
    Return (rows|None, sumber) dgn sumber = hub-sendiri / .54-cadangan / GAGAL-skip.
    None = SEMUA sumber gagal → panggil WAJIB skip siklus (tanpa tulis/push).
    Anti-wipe: primer [] + cadangan non-kosong → pakai cadangan. Primer []
    bisa berarti "WE belum scan / mode TUNGGU", bukan "hari ini nol file";
    cadangan yg masih berisi baris adalah sinyal lebih kuat. Baris basi
    cadangan tetap aman: yg sudah completed/skip_forever terfilter di sync()."""
    rows = fetch_printed_from_hub(date_str)
    if rows is not None and len(rows) > 0:
        return rows, "hub-sendiri"
    dash = fetch_completed(date_str)
    if dash is not None and len(dash) > 0:
        if rows is not None:
            print(f"  {warn('primer kosong tapi cadangan berisi — pakai .54-cadangan (anti-wipe)')}")
        return dash, ".54-cadangan"
    if rows is not None:
        return rows, "hub-sendiri"  # [] asli: kedua sumber positif nol
    if dash is not None:
        return dash, ".54-cadangan"  # [] asli dari cadangan
    return None, "GAGAL-skip"


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

        # file TEST: grup sendiri + flag (tampil dgn Tag TEST di Menunggu
        # Aksi, tak diproses KMMA) — tak boleh menyeret se-nota ikut skip
        is_test = bool(R_TEST.search(fname))
        # key = (nota, bahan, sides, laminating, test, process_type):
        # DIGITAL vs KISSCUT/DIECUT se-nota = baris beda, jangan gabung
        sides = parsed.get("sides") or ""
        lam = parsed.get("laminating") or ""
        proc = parsed.get("process_type") or ""
        key = (nota, bahan, sides, lam, is_test, proc)

        if key not in groups:
            groups[key] = {
                "nota": nota,
                "bahan": bahan,
                "customer": parsed["customer"] or "",
                "test": is_test,
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
        tag += " [TEST]" if e.get("test") else ""

        cat = proc.upper()
        print(f"  {i:>2}. {BOLD}{nota}{RESET}  [{CYAN}{cat}{RESET}]  {cust} | {bahan} x {qty} {jenis}  |  {op}  |  {mac}{tag}")
        for f in files:
            print(f"      {dim(f[:70])}")

    print()


# ════════════════════════════════════════════════════════════════
# SECTION 10: KMMA STATE (skip nota yang sudah diproses)
# ════════════════════════════════════════════════════════════════

def _ekey(e):
    return (e.get("nota") or "", e.get("bahan") or "", e.get("sides") or "",
            e.get("laminating") or "", 1 if e.get("test") else 0,
            e.get("process_type") or "")


def get_processed_notas():
    """Set KUNCI BARIS yang sudah diproses — hanya completed+skip_forever.
    Retry (skipped) tetap antre; per-baris agar varian se-nota yg belum diproses tetap antre."""
    if HAVE_DB:
        try:
            with nshdb.get_conn() as con:
                return {(r[0], r[1] or "", r[2] or "", r[3] or "", r[4] or 0, r[5] or "")
                        for r in con.execute(
                            "SELECT nota,bahan,sides,laminating,test,process_type FROM kmma_state"
                            " WHERE status IN ('completed','skip_forever')")}
        except Exception:
            pass
    processed = set()
    try:
        data = json.loads(KMMA_PATH.read_text(encoding="utf-8"))
        # kunci komposit "nota||bahan||sides||lam||1/0[||proc]"; legacy nota-only = semua varian
        for section in ("completed", "skip_forever"):
            for k in (data.get(section, {}) or {}).keys():
                parts = str(k).split("||")
                if len(parts) >= 6:
                    processed.add((parts[0], parts[1], parts[2], parts[3], int(parts[4] or 0), parts[5]))
                elif len(parts) == 5:
                    processed.add((parts[0], parts[1], parts[2], parts[3], int(parts[4] or 0), ""))
                else:
                    processed.add((str(k), None, None, None, None, None))  # legacy: cocok semua
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return processed


def _row_done(row, processed):
    k = _ekey(row)
    if k in processed:
        return True
    return any(p[0] == k[0] and p[1] is None for p in processed)  # legacy nota-only


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

    # fetch: hub-sendiri dulu, .54 cadangan; GAGAL semua = skip siklus
    # (JANGAN save_daily([])/DELETE/send_entries([]) saat gagal — itu wipe antrean!)
    print(f"  {dim('fetching files tercetak...')}")
    rows, sumber = _fetch_best(date_str)
    if rows is None:
        print(f"  {err(f'sumber: GAGAL-skip — semua sumber gagal ({_API_LAST_ERR}); siklus dilewat, antrean dipertahankan')}")
        print(f"  {dim(f'sumber=GAGAL-skip, printed=?, antrean=? (skip)')}")
        print()
        return
    print(f"  {ok(f'{len(rows)} files selesai (PDF) [sumber: {sumber}]')}")
    print()

    if not rows:
        # Genuine-empty: sumber positif lapor nol baris (BUKAN error) → perilaku lama
        print(f"  {warn('tidak ada file selesai hari ini')}")
        print(f"  {dim(f'sumber={sumber}, printed=0, antrean=0')}")
        save_daily([], date_str)
        # antrean live hub ikut dikosongkan agar tak jadi hantu
        if ws_client and ws_client.connected:
            ws_client.send_entries([])
        return

    # aggregate
    entries = aggregate_by_nota(rows)
    print(f"  {ok(f'{len(entries)} nota (setelah agregasi)')}")
    print()

    # filter: hapus BARIS yang sudah diproses KMMA (per-baris, bukan per-nota)
    processed = get_processed_notas()
    before = len(entries)
    entries = [e for e in entries if not _row_done(e, processed)]
    skipped = before - len(entries)
    if skipped:
        print(f"  {dim(f'hapus {skipped} nota sudah diproses KMMA')}")
        print()

    # display
    if not entries:
        print(f"  {warn('semua nota sudah diproses KMMA')}")
        # Genuine-state: sumber positif beri baris + semua terfilter (BUKAN error)
        print(f"  {dim(f'sumber={sumber}, printed={len(rows)}, antrean=0')}")
        save_daily([], date_str)
        # antrean live hub ikut dikosongkan agar tak jadi hantu
        if ws_client and ws_client.connected:
            ws_client.send_entries([])
        return

    # Simpan + dorong ke hub DULUAN, baru display.
    # Dulu display dulu lalu save: satu print yg gagal (stdout tak valid saat
    # pythonw dapat console baru) berarti wma_daily.json tak pernah ditulis
    # dan antrean tak pernah sampai ke hub. Output cosmetik tak boleh membatalkan
    # persistence.
    save_daily(entries, date_str)
    if ws_client and ws_client.connected:
        ws_client.send_entries(entries)

    for i, e in enumerate(entries, 1):
        try:
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
            tag += " [TEST]" if e.get("test") else ""

            cat = proc.upper()
            print(f"  {i:>2}. {BOLD}{nota}{RESET}  [{CYAN}{cat}{RESET}]  {cust} | {bahan} x {qty} {jenis}  |  {op}  |  {mac}{tag}")
        except Exception:
            continue  # 1 baris display gagal? lanjut baris berikutnya.

    print()
    print(f"  {ok(f'saved -> {DAILY_PATH.name}')}")
    print(f"  {dim(f'sumber={sumber}, printed={len(rows)}, antrean={len(entries)}')}")
    print()

    if ws_client and ws_client.connected:
        print(f"  {ok(f'pushed ke hub ({len(entries)} entries)')}")
        print()


# ════════════════════════════════════════════════════════════════
# SECTION 12: COMPARE (diagnostik paralel-run, read-only)
# ════════════════════════════════════════════════════════════════

COMPARE_DIFF_CAP = 30


def compare_sources(date_str=None, hub_base=None):
    """Bandingkan kedua sumber utk 1 tanggal: hitungan berdampingan + daftar
    filename yg ada di satu sumber tapi hilang di sumber lain (cap 30 baris).
    Murni read-only: TANPA save_daily / TANPA push hub. Selalu exit code 0
    (dipakai operator saat paralel-run 1-2 hari hub vs .54)."""
    if not date_str:
        date_str = datetime.date.today().isoformat()

    print()
    print(f"  {BOLD}WMA v{WMA_VERSION} — Compare{RESET}")
    print(f"  Date: {CYAN}{date_str}{RESET}")
    print()

    hub_rows = fetch_printed_from_hub(date_str, hub_base=hub_base)
    dash_rows = fetch_completed(date_str)

    hub_n = len(hub_rows) if hub_rows is not None else None
    dash_n = len(dash_rows) if dash_rows is not None else None
    print(f"  hub-sendiri : {GREEN}{hub_n}{RESET}" if hub_n is not None
          else f"  hub-sendiri : {RED}GAGAL ({_API_LAST_ERR}){RESET}")
    print(f"  .54-cadangan: {GREEN}{dash_n}{RESET}" if dash_n is not None
          else f"  .54-cadangan: {RED}GAGAL ({_API_LAST_ERR}){RESET}")
    print()

    if hub_rows is None or dash_rows is None:
        print(f"  {warn('satu sumber gagal — diff tak bisa dihitung')}")
        print()
        return 0

    hub_set = {r.get("filename", "") for r in hub_rows}
    dash_set = {r.get("filename", "") for r in dash_rows}
    only_hub = sorted(hub_set - dash_set)
    only_dash = sorted(dash_set - hub_set)

    if not only_hub and not only_dash:
        print(f"  {ok('identik — tak ada selisih filename')}")
        print()
        return 0

    print(f"  hanya di hub-sendiri ({len(only_hub)}):")
    print(f"  hanya di .54-cadangan ({len(only_dash)}):")
    n = 0
    for fn in only_hub:
        if n >= COMPARE_DIFF_CAP:
            break
        print(f"    + {fn[:100]}")
        n += 1
    for fn in only_dash:
        if n >= COMPARE_DIFF_CAP:
            break
        print(f"    - {fn[:100]}")
        n += 1
    total = len(only_hub) + len(only_dash)
    if total > COMPARE_DIFF_CAP:
        print(f"  {dim(f'... +{total - COMPARE_DIFF_CAP} baris selisih lain (cap {COMPARE_DIFF_CAP})')}")
    print()
    return 0


# ════════════════════════════════════════════════════════════════
# SECTION 13: MAIN
# ════════════════════════════════════════════════════════════════

def _wait_ws(ws_client, timeout=8.0):
    """Tunggu WS tersambung sebentar (single-run --hub agar push tak hilang
    karena thread connect belum selesai). Return True bila tersambung."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if ws_client is not None and ws_client.connected:
            return True
        time.sleep(0.5)
    return bool(ws_client is not None and ws_client.connected)


def _wait_trigger_or_timeout():
    """Tunggu pemicu WE (realtime) atau timeout interval (jaring pengaman).

    Return True = poll sekarang (event segar ATAU timeout 60 dtk tercapai),
    False = event terlalu rapat setelah poll terakhir (lewati; event/timeout
    berikut mencakupnya karena last_hash tak berubah). Dipakai loop agar sync
    seketika saat WE berubah, tetap sync berkala bila sepi."""
    global _WE_LAST_POLL
    try:
        hit = _WE_TRIGGER.wait(timeout=SYNC_INTERVAL_S)
        _WE_TRIGGER.clear()
        if hit and (time.time() - _WE_LAST_POLL) < WE_DEBOUNCE_S:
            return False
        _WE_LAST_POLL = time.time()
        return True
    except Exception:
        return True


def main():
    global _HUB_URL_CLI
    args = sys.argv[1:]

    # --compare (read-only, exit; tangani DULUAN agar tanpa konek WS / tanpa tulis)
    if "--compare" in args:
        date_c = None
        if "--date" in args:
            idx = args.index("--date")
            date_c = args[idx + 1] if idx + 1 < len(args) else None
        hub_base = None
        if "--hub-base" in args:
            idx = args.index("--hub-base")
            hub_base = args[idx + 1] if idx + 1 < len(args) else "auto"
        if "--hub-url" in args:
            idx = args.index("--hub-url")
            if idx + 1 < len(args):
                _HUB_URL_CLI = args[idx + 1]
        sys.exit(compare_sources(date_c, hub_base=hub_base))

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
        _HUB_URL_CLI = hub_url
        ws_client = WSClient("wma", hub_url=hub_url)
        ws_client.on_we_update = _we_changed  # realtime: WE berubah -> sync seketika
        ws_client.connect()
        print(f"  {ok('WebSocket client started')}")
        _wait_ws(ws_client)

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
                print(f"  {dim(f'next sync in {SYNC_INTERVAL_S}s (atau saat WE berubah)...')}")
                _WE_TRIGGER.clear()
                _wait_trigger_or_timeout()
            except KeyboardInterrupt:
                print(f"\n  {ok('stopped')}")
                break
            except Exception:
                # barrier: 1 error sync tak boleh mematikan proses (WS jadi putus)
                log_crash("loop-mode")
                time.sleep(SYNC_INTERVAL_S)
        return

    # --watch: event-driven, sync seketika saat WE berubah (WS we_update),
    # timeout 60 dtk sbg jaring pengaman bila event terlewat/hub sempat putus.
    if "--watch" in args:
        print(f"  {BOLD}WMA v{WMA_VERSION} — Watch Mode{RESET}")
        print(f"  Sync realtime saat WE berubah (poll 60s cadangan)")
        print(f"  Tekan Ctrl+C untuk stop")
        last_hash = None
        while True:
            try:
                if not _wait_trigger_or_timeout():
                    continue  # debounce: poll berikut mencakupnya
                date_str_w = date_str or datetime.date.today().isoformat()
                rows, sumber_w = _fetch_best(date_str_w)
                if rows is None:
                    # Fetch GAGAL (bukan kosong): jangan sync, jangan tulis,
                    # jangan ubah last_hash (agar pulih langsung sync)
                    ts_now = datetime.datetime.now().strftime("%H:%M:%S")
                    print(f"  {err(f'[{ts_now}] sumber: GAGAL-skip ({_API_LAST_ERR}) — tunggu pemicu/timeout...')}")
                    continue
                # hash mencakup status (perubahan status tanpa file baru WAJIB sync)
                current_hash = json.dumps([(r.get("filename", ""), r.get("status", "")) for r in rows], sort_keys=True)

                if current_hash != last_hash:
                    last_hash = current_hash
                    sync(date_str, ws_client=ws_client)
                else:
                    ts_now = datetime.datetime.now().strftime("%H:%M:%S")
                    print(f"  {dim(f'[{ts_now}] no changes...')}")

            except KeyboardInterrupt:
                print(f"\n  {ok('stopped')}")
                break
            except Exception:
                # barrier: 1 error fetch/sync tak boleh mematikan proses (WS jadi putus)
                log_crash("watch-mode")
                time.sleep(SYNC_INTERVAL_S)
        return

    # default: sync sekali
    sync(date_str, ws_client=ws_client)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  stop.")
    except Exception:
        log_crash("__main__")
