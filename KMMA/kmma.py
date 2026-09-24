"""KMMA — KonsepMedia Module Auto.
Alur: load wma_daily.json -> login KM -> fetch order -> validasi -> isi form -> simpan.
API: get_detail (JSON) -> get_detail_flow (JSON) -> save_flow (POST).

Pakai:
  python kmma.py              → proses sekali, exit
  python kmma.py --hub        → proses + WebSocket hub
  python kmma.py --watch      → event-driven: watch wma_daily.json
  python kmma.py --watch --hub → event-driven + WebSocket hub
"""
import datetime
import json
import queue
import re
import sys
import threading
import time
import urllib.parse
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
from ui import (cls, header, section, ok, err, warn, info, dim, bold,
                status_bar, waiting, progress, hline)
from ws_client import WSClient

# ── Mirror log console → hub (tab Log KMMA sinkron dgn jendela modul) ──
_WS = None

def _klog(msg):
    if _WS is not None:
        _WS.send_log("KMMA", str(msg))

_ok, _err, _warn, _info, _waiting = ok, err, warn, info, waiting

def ok(text=""):
    _klog(f"[OK] {text}")
    return _ok(text)

def err(text=""):
    _klog(f"[!!] {text}")
    return _err(text)

def warn(text=""):
    _klog(f"[!] {text}")
    return _warn(text)

def info(text=""):
    _klog(f"[i] {text}")
    return _info(text)

def waiting(text="menunggu..."):
    _klog(text)
    return _waiting(text)

BASE = Path(__file__).parent
ENV_PATH = BASE / "env" / "kmma" / ".env"
CFG_PATH = BASE / "kmma.json"
DAILY_PATH = BASE.parent / "WMA" / "wma_daily.json"

POLL_INTERVAL = 180
ACTION_DELAY = 1
API_DELAY = 5
NOTA_RETRY = 180  # jeda retry bila nota tak ada di KM
AUTO_INTERVAL = 180  # proses ulang otomatis tiap 3 menit (watch mode)

R_BAHAN = re.compile(
    r"(Ap\d+gr|Ac\d+gr|(?:Stiker|Sticker)\s+\w+|HVS|Concord|BC\s+\w+|Master|BNTU"
    r"|NewToop\s*\d+\s*mic|Toop\s*\d+\s*mic|Art\s*Paper|Art\s*Carton"
    r"|(?:Kertas\s+)?[KC]rom[oe]"
    r"|Vinyl|Vyinil|Rajawali|Pindo|Jasmine|Ivory|Transparant|Hologram|Gold|Silver)",
    re.I,
)
R_NOTA = re.compile(r"(ON\d{12})")
R_TOTAL = re.compile(r"TOTAL\s*=\s*(\d+)\s*(BESAR|KECIL)", re.I)
R_KISSCUT = re.compile(r"kiss\s*cut", re.I)
R_DIECUT = re.compile(r"die\s*cut", re.I)
R_TEST = re.compile(r"tes", re.I)  # TEST / tes / test1 (nama pelanggan atau file)
R_KTN = re.compile(r"ktn|kartu\s*nama", re.I)  # KTN / KartuNama / Kartu Nama


def _is_test_entry(entry):
    """True bila customer atau salah satu file mengandung TEST."""
    if R_TEST.search(entry.get("customer") or ""):
        return True
    for fn in entry.get("files", []) or []:
        if R_TEST.search(fn or ""):
            return True
    return False


def _is_ktn_entry(entry):
    """True bila salah satu file mengandung KTN/KartuNama."""
    for fn in entry.get("files", []) or []:
        if R_KTN.search(fn or ""):
            return True
    return False


def load_env():
    env = {}
    try:
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    except FileNotFoundError:
        print(f"  [KMMA-E01] file {ENV_PATH} tidak ditemukan")
    except Exception as e:
        print(f"  [KMMA-E01] gagal baca .env: {e}")
    return env


def _load_cfg_file():
    """Baca JSON cadangan (format lama)."""
    try:
        cfg = json.loads(CFG_PATH.read_text(encoding="utf-8"))
        # tolerate list → dict untuk semua dict-type fields
        for key in ("skipped_nota", "skip_forever", "completed"):
            if isinstance(cfg.get(key), list):
                cfg[key] = {}
        return cfg
    except Exception:
        return {}


def _save_cfg_file(cfg):
    """Tulis JSON cadangan (format lama)."""
    tmp = CFG_PATH.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(CFG_PATH)
    except Exception:
        try:
            CFG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


def load_cfg():
    """Sumber utama: Database. Fallback: JSON cadangan."""
    if HAVE_DB:
        try:
            return {
                "completed": {n: v["updated_at"]
                              for n, v in nshdb.kmma_by_status("completed").items()},
                "skip_forever": {n: v["reason"]
                                 for n, v in nshdb.kmma_by_status("skip_forever").items()},
                "skipped_nota": {n: {"reason": v["reason"], "last_try": v["updated_at"]}
                                 for n, v in nshdb.kmma_by_status("skipped").items()},
            }
        except Exception:
            pass
    return _load_cfg_file()


def save_cfg(cfg):
    """Tulis Database (utama) + JSON cadangan."""
    if HAVE_DB:
        try:
            for nota, t in (cfg.get("completed") or {}).items():
                nshdb.kmma_set(nota, "completed", "", t if isinstance(t, str) else "")
            for nota, reason in (cfg.get("skip_forever") or {}).items():
                nshdb.kmma_set(nota, "skip_forever", str(reason))
            for nota, v in (cfg.get("skipped_nota") or {}).items():
                v = v or {}
                nshdb.kmma_set(nota, "skipped", str(v.get("reason", "")),
                               str(v.get("last_try", "")))
        except Exception:
            pass
    _save_cfg_file(cfg)


def load_daily():
    try:
        data = json.loads(DAILY_PATH.read_text(encoding="utf-8"))
        today = datetime.date.today().isoformat()
        if data.get("date") == today and data.get("entries"):
            return data["entries"]
    except Exception:
        pass
    return []


def extract_bahan(filename):
    m = R_BAHAN.search(filename)
    return m.group(0) if m else None


def extract_nota(filename):
    m = R_NOTA.search(filename)
    return m.group(1) if m else None


def extract_total(filename):
    m = R_TOTAL.search(filename)
    if m:
        return int(m.group(1)), m.group(2).upper()
    return None, None


def match_bahan(filename_bahan, km_material_name):
    """Cek apakah bahan dari filename match bahan KM."""
    fb = filename_bahan.lower().replace(" ", "")
    km = km_material_name.lower().replace(" ", "")
    if fb in km:
        return True
    m_fn = re.match(r'(ap|ac)(\d+)', fb)
    if m_fn:
        prefix, num = m_fn.groups()
        if f"{num}gr" in km:
            return True
    for name in ["stiker", "sticker", "concord", "hvs", "idcard", "id card",
                  "newtoop", "toop", "artpaper", "artpaper", "kromo", "cromo",
                  "vinyl", "vyinil", "rajawali", "pindo", "jasmine", "ivory",
                  "transparant", "hologram", "gold", "silver"]:
        if name in fb:
            variants = {"stiker": "sticker", "sticker": "stiker",
                        "newtoop": "toop", "toop": "newtoop",
                        "kromo": "cromo", "cromo": "kromo",
                        "vyinil": "vinyl"}
            km_check = variants.get(name, name)
            if name in km or km_check in km:
                return True
    return False


def detect_process_type(filename):
    """Deteksi tipe proses dari filename: 'kisscut', 'diecut', atau None (Digital A3+)."""
    if R_DIECUT.search(filename):
        return "diecut"
    if R_KISSCUT.search(filename):
        return "kisscut"
    return None


class KMClient:
    UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

    def __init__(self):
        self.base = "https://www.konsepmedia.com/administrator"
        self.cookie = None
        self._last_ts = 0
        self._csrf_token = ""
        self._username = ""
        self._password = ""

    def _throttle(self):
        elapsed = time.time() - self._last_ts
        if elapsed < API_DELAY:
            time.sleep(API_DELAY - elapsed)
        self._last_ts = time.time()

    def _request(self, method, path, data=None, check_login=False):
        self._throttle()
        url = f"{self.base}/{path}"
        body = None
        headers = {
            "User-Agent": self.UA,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self.base}/workflow_production",
            "Origin": self.base,
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }
        if data:
            body = urllib.parse.urlencode(data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if self.cookie:
            headers["Cookie"] = self.cookie
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                if not self.cookie:
                    for h in r.headers.get_all("Set-Cookie") or []:
                        if "ci_session" in h:
                            self.cookie = h.split(";")[0]
                            break
                resp = r.read().decode("utf-8", errors="replace")
                if check_login and self._is_login_page(resp):
                    self.cookie = None
                    return None
                return resp
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")[:300]
            print(f"  [DEBUG HTTP] {e.code} {method} {path}: {err_body}")
            return None
        except Exception:
            return None

    def _is_login_page(self, html):
        if not html:
            return False
        low = html.lower()
        # login page punya form action="login/process" DAN input name="username"
        has_login_form = "login/process" in low and 'name="username"' in low
        # atau form dengan class form-login
        has_form_login = 'form-login' in low and 'name="password"' in low
        return has_login_form or has_form_login

    def preflight(self):
        html = self._request("GET", "login")
        if html:
            m = re.search(r'name="ci_csrf_token"\s+value="([^"]*)"', html)
            if m:
                self._csrf_token = m.group(1)
            else:
                self._csrf_token = ""

    def login(self, username, password):
        self._username = username
        self._password = password
        self.cookie = None
        self.preflight()
        print(f"  [DEBUG login] cookie={self.cookie[:30] if self.cookie else 'None'} csrf={self._csrf_token[:20] if self._csrf_token else 'None'}")
        html = self._request("POST", "login/process", {
            "username": username, "password": password,
            "ci_csrf_token": self._csrf_token,
        }, check_login=True)
        if html and ("logout" in html.lower() or "home" in html.lower()):
            if self._is_login_page(html):
                print(f"  [DEBUG login] response is login page despite having logout/home")
                self.cookie = None
                return False
            self._fetch_csrf_from_workflow()
            return True
        print(f"  [DEBUG login] login failed, html={'login_page' if html and self._is_login_page(html) else 'None' if not html else 'no_logout_found'} len={len(html) if html else 0}")
        self.cookie = None
        return False

    def _fetch_csrf_from_workflow(self):
        html = self._request("GET", "workflow_production?status=PESANAN+SUDAH+DIVERIFIKASI")
        if html:
            m = re.search(r'name="ci_csrf_token"\s+value="([^"]*)"', html)
            if m and m.group(1):
                self._csrf_token = m.group(1)
                print(f"  [DEBUG] CSRF token updated from workflow page: {self._csrf_token[:20]}...")

    def fetch_orders_page(self):
        orders = []
        seen = set()
        page = 1
        while True:
            url = f"workflow_production?status=PESANAN+SUDAH+DIVERIFIKASI&limited_show=200"
            if page > 1:
                url += f"&per_page=200&data-ci-pagination-page={page}"
            html = self._request("GET", url)
            if not html:
                break
            if self._is_login_page(html):
                self.cookie = None
                break
            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S)
            found = 0
            for row in rows:
                m = re.search(r'data-id="(\d+)"', row)
                if not m:
                    continue
                data_id = m.group(1)
                if data_id in seen:
                    continue
                seen.add(data_id)
                found += 1
                cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
                clean = [re.sub(r'<[^>]+>', ' ', c).strip() for c in cells]
                nota = ""
                for c in clean:
                    nm = re.search(r'(ON\d{12})', c)
                    if nm:
                        nota = nm.group(1)
                        break
                bahan = ""
                qty = ""
                for c in clean:
                    if re.search(r'(Art Carton|Sticker|Stiker|Concord|ID Card|HVS|Art Paper)', c, re.I):
                        bahan = re.sub(r'\s+', ' ', c).strip()
                        break
                for ci, c in enumerate(clean):
                    if c.isdigit() and ci > 0:
                        prev = clean[ci - 1] if ci > 0 else ""
                        if not prev.isdigit():
                            qty = c
                            break
                if nota:
                    orders.append({
                        "data_id": data_id, "nota": nota,
                        "bahan_km": bahan, "qty_km": qty,
                    })
            if found == 0:
                break
            if f'data-ci-pagination-page="{page + 1}"' not in html:
                break
            page += 1
            time.sleep(ACTION_DELAY)
        return orders

    def get_detail(self, data_id):
        time.sleep(ACTION_DELAY)
        resp = self._request("POST", "workflow_production/get_detail", {
            "id": data_id, "tt": "",
        })
        if not resp:
            return None
        try:
            data = json.loads(resp)
            # debug: print flow steps
            if data.get("flow"):
                for i, s in enumerate(data["flow"]):
                    print(f"  [DEBUG detail] step[{i}]: id={s.get('idproduct_step')} name={s.get('step_name') or s.get('name')} id_td={s.get('idtransaction_detail')}")
            return data
        except Exception:
            return None

    def get_detail_flow(self, idproduct_step, idtransaction_detail):
        time.sleep(ACTION_DELAY)
        resp = self._request("POST", "workflow_production/get_detail_flow", {
            "id": idproduct_step, "id_td": idtransaction_detail,
        })
        if not resp:
            return None
        try:
            data = json.loads(resp)
            if data.get("err_code") == 0:
                # debug: print keys untuk lihat semua field yang tersedia
                print(f"  [DEBUG detail_flow] keys: {list(data.keys())}")
                # print flow jika ada
                if "flow" in data:
                    print(f"  [DEBUG detail_flow] flow: {json.dumps(data['flow'], ensure_ascii=False)[:500]}")
                if "step" in data:
                    print(f"  [DEBUG detail_flow] step: {json.dumps(data['step'], ensure_ascii=False)[:500]}")
                return data
        except Exception:
            pass
        return None

    def save_flow(self, payload):
        time.sleep(ACTION_DELAY)
        resp = self._request("POST", "workflow_production/save_flow", payload)
        if not resp:
            # retry: re-login then try once more
            print("  [DEBUG save_flow] no response, re-login & retry...")
            self.cookie = None
            if self._username and self._password:
                self.login(self._username, self._password)
            time.sleep(ACTION_DELAY)
            resp = self._request("POST", "workflow_production/save_flow", payload)
        if not resp:
            return False, "network error"
        # debug: print full response
        print(f"  [DEBUG save_flow] response ({len(resp)} bytes): {resp[:500]}")
        try:
            data = json.loads(resp)
            print(f"  [DEBUG save_flow] json: {json.dumps(data, ensure_ascii=False)[:500]}")
            if data.get("err_code") == 0:
                return True, "ok"
            return False, data.get("err_message", "unknown")
        except Exception:
            if "berhasil" in resp.lower():
                return True, "ok"
        return False, "save failed"


def send_manual(nota, msg, ws_client=None):
    """Tandai butuh aksi manual: status hub 'manual', TANPA skip permanen
    (entri tetap di antrean, bisa diperbaiki via dashboard lalu retry)."""
    print(f"    {warn(f'{nota} MANUAL: {msg}')}")
    if ws_client and ws_client.connected:
        ws_client.send_status(nota, "manual", msg)


def log_skip(reason, nota):
    if HAVE_DB:
        try:
            nshdb.kmma_set(nota, "skip_forever", str(reason))
        except Exception:
            pass
    cfg = _load_cfg_file()
    skip = cfg.get("skip_forever", {})
    skip[nota] = reason
    cfg["skip_forever"] = skip
    _save_cfg_file(cfg)
    print(f"    {warn(f'{nota} SKIP: {reason}')}")


def should_skip(nota):
    if HAVE_DB:
        try:
            r = nshdb.kmma_get(nota)
            if r and r["status"] == "skip_forever":
                return True
            if r and r["status"] == "skipped":
                try:
                    last_dt = datetime.datetime.fromisoformat(r["updated_at"])
                    if (datetime.datetime.now() - last_dt).total_seconds() < NOTA_RETRY:
                        return True
                except Exception:
                    pass
                return False
        except Exception:
            pass
    cfg = _load_cfg_file()
    if nota in cfg.get("skip_forever", {}):
        return True
    retry = cfg.get("skipped_nota", {})
    if nota in retry:
        try:
            last_dt = datetime.datetime.fromisoformat(retry[nota]["last_try"])
            if (datetime.datetime.now() - last_dt).total_seconds() < NOTA_RETRY:
                return True
        except Exception:
            pass
    return False


def mark_nota_retry(nota, ws_client=None):
    """Catat retry bila nota tak ada di KM. Retry tiap 3 mnt TANPA batas
    (sampai berhasil / di-skip manual). Kirim status 'retry' agar dashboard
    tak menampilkan baris hantu di Antrian. Return jumlah percobaan."""
    now = datetime.datetime.now().isoformat()
    count = 1
    if HAVE_DB:
        try:
            r = nshdb.kmma_get(nota)
            if r and r["status"] == "skipped":
                count = int(r.get("retries") or 0) + 1
        except Exception:
            pass
    else:
        try:
            old = _load_cfg_file().get("skipped_nota", {}).get(nota, {})
            count = int(old.get("count") or 0) + 1
        except Exception:
            count = 1
    if HAVE_DB:
        try:
            nshdb.kmma_set(nota, "skipped", "kode tidak ada di list KM", now, retries=count)
        except Exception:
            pass
    cfg = _load_cfg_file()
    retry = cfg.get("skipped_nota", {})
    retry[nota] = {
        "reason": "kode tidak ada di list KM",
        "last_try": now,
        "count": count,
    }
    cfg["skipped_nota"] = retry
    _save_cfg_file(cfg)
    if ws_client and ws_client.connected:
        ws_client.send_status(nota, "retry", f"tunggu 3 mnt (percobaan {count})")
    return count


def mark_completed(nota):
    now = datetime.datetime.now().isoformat()
    if HAVE_DB:
        try:
            nshdb.kmma_set(nota, "completed", "", now)
        except Exception:
            pass
    cfg = _load_cfg_file()
    completed = cfg.get("completed", {})
    completed[nota] = now
    cfg["completed"] = completed
    _save_cfg_file(cfg)


def is_completed(nota):
    if HAVE_DB:
        try:
            r = nshdb.kmma_get(nota)
            return bool(r and r["status"] == "completed")
        except Exception:
            pass
    cfg = _load_cfg_file()
    return nota in cfg.get("completed", {})


def process_entry(client, entry, orders_cache, ws_client=None, qty_override=None):
    # WMA v2 format: {nota, bahan, total_qty, jenis, process_type, operator, machine, files[]}
    nota = entry.get("nota")
    machine_wma = entry.get("machine", "")
    operator_wma = entry.get("operator", "")
    bahan_file = entry.get("bahan")
    qty_file = entry.get("total_qty")
    jenis_file = entry.get("jenis")
    proc_type = entry.get("process_type")
    files = entry.get("files", [])

    # override qty manual dari dashboard (command retry + qty)
    qty_manual = False
    if qty_override is not None:
        try:
            qty_file = int(qty_override)
            qty_manual = True
            print(f"    {warn(f'{nota} qty override manual: {qty_file}')}")
        except (ValueError, TypeError):
            print(f"    {err(f'{nota} qty override tidak valid: {qty_override}')}")
            return

    if not nota:
        print(f"    {warn('NOTA tidak ditemukan di entry')}")
        return

    # ATURAN: file TEST → skip permanen, jangan diproses ke KM
    if _is_test_entry(entry):
        log_skip("TEST", nota)
        print(f"    {err(f'{nota} SKIP: TEST (file test, jangan diproses ke KM)')}")
        if ws_client and ws_client.connected:
            ws_client.send_status(nota, "error", "SKIP: TEST")
        return

    # ATURAN: KartuNama → qty file wajib ÷ 4 (override manual tak ikut dibagi)
    if _is_ktn_entry(entry) and not qty_manual:
        try:
            _qty_before = int(qty_file)
            qty_file = _qty_before // 4
            print(f"    {warn(f'{nota} KartuNama: qty {_qty_before} → {qty_file} (÷4)')}")
        except (ValueError, TypeError):
            pass

    if should_skip(nota):
        return

    if is_completed(nota):
        print(f"    {dim(f'{nota} sudah diproses')}")
        return

    orders = orders_cache
    if not orders:
        print(f"    {err('gagal ambil daftar order')}")
        return

    matched = None
    for o in orders:
        if o["nota"] == nota:
            matched = o
            break

    if not matched:
        n = mark_nota_retry(nota, ws_client)
        print(f"    {warn(f'{nota} tidak ada di KM, retry 3 mnt (percobaan {n})')}")
        return

    if bahan_file and matched.get("bahan_km"):
        if not match_bahan(bahan_file, matched["bahan_km"]):
            send_manual(nota, f"bahan tidak sesuai: {bahan_file} vs {matched['bahan_km']}", ws_client)
            print(f"    {err(f'bahan: {bahan_file} vs {matched["bahan_km"]}')}")
            return

    # qty validation: WMA v2 sudah aggregate total_qty per nota
    # (dilewati bila qty override manual dari dashboard)
    if qty_file is not None and matched.get("qty_km") and not qty_manual:
        try:
            qty_km_int = int(matched["qty_km"])
            if qty_file != qty_km_int:
                send_manual(nota, f"jumlah tidak sesuai: {qty_file} vs {qty_km_int} (override qty manual / lewati)", ws_client)
                print(f"    {err(f'qty: {qty_file} vs {qty_km_int}')}")
                return
        except ValueError:
            pass

    detail = client.get_detail(matched["data_id"])
    if not detail or not detail.get("flow"):
        print(f"    {err(f'{nota} gagal ambil detail')}")
        return

    order_detail = detail.get("transaction", {})
    id_td_order = order_detail.get("idtransaction_detail", "")

    # ── cek step Laminating/Finishing sebelum Kisscut/Diecut → skip ──
    if proc_type and len(detail["flow"]) > 2:
        step_names = [(s.get("step_name") or s.get("name") or "").lower() for s in detail["flow"]]
        for sn in step_names[1:-1]:
            if "laminat" in sn or "finishing" in sn:
                log_skip(f"ada step '{sn}' sebelum {proc_type}", nota)
                print(f"    {warn(f'{nota} SKIP: ada step {sn} sebelum {proc_type.upper()} — harus diselesaikan divisi lain')}")
                if ws_client and ws_client.connected:
                    ws_client.send_status(nota, "error", f"SKIP: ada step {sn} sebelum {proc_type.upper()}")
                return

    # ── untuk kisscut/diecut: proses Digital A3+ dulu, lalu step-nya ──
    if proc_type and len(detail["flow"]) > 1:
        for si, sv in enumerate(detail.get("step", [])):
            print(f"  [DEBUG step_state] step[{si}] id={sv.get('idproduct_step')} name={sv.get('step_name', sv.get('name',''))} err_code={sv.get('err_code', '?')} qty_final={sv.get('qty_final', '?')}")
        digital_step = detail["flow"][0]
        finish_step = None
        for s in detail["flow"]:
            step_name = (s.get("step_name") or s.get("name") or "").lower()
            if proc_type == "kisscut" and "kisscut" in step_name:
                finish_step = s
                break
            elif proc_type == "diecut" and "diecut" in step_name:
                finish_step = s
                break
        if not finish_step:
            finish_step = detail["flow"][1]

        # fetch flow untuk Digital A3+ (try step-level id_td first, fallback order-level)
        d_td = digital_step.get("idtransaction_detail", "") or id_td_order
        digital_flow = client.get_detail_flow(digital_step["idproduct_step"], d_td)
        if not digital_flow and d_td != id_td_order:
            print(f"  [DEBUG] retry get_detail_flow Digital A3+ with order id_td={id_td_order}")
            digital_flow = client.get_detail_flow(digital_step["idproduct_step"], id_td_order)
        if not digital_flow:
            print(f"    {err(f'{nota} gagal ambil form Digital A3+')}")
            return

        # fetch flow untuk Kisscut/Diecut
        f_td = finish_step.get("idtransaction_detail", "") or id_td_order
        finish_flow = client.get_detail_flow(finish_step["idproduct_step"], f_td)
        if not finish_flow and f_td != id_td_order:
            print(f"  [DEBUG] retry get_detail_flow {proc_type.upper()} with order id_td={id_td_order}")
            finish_flow = client.get_detail_flow(finish_step["idproduct_step"], id_td_order)
        if not finish_flow:
            print(f"    {err(f'{nota} gagal ambil form {proc_type.upper()}')}")
            return

        id_td = id_td_order

        # cari mesin Digital A3+ (Develop/Ricoh)
        digital_mac = None
        for mc in digital_flow.get("machine", []):
            mn = mc["machine_name"].lower()
            if "develop" in mn or "ricoh" in mn:
                digital_mac = mc
                break
        if not digital_mac and digital_flow.get("machine"):
            digital_mac = digital_flow["machine"][0]

        # cari material Digital A3+
        digital_mat = None
        for m in digital_flow.get("material", []):
            if bahan_file and match_bahan(bahan_file, m["material_name"]):
                digital_mat = m
                break

        if not digital_mac:
            send_manual(nota, "mesin Digital A3+ tidak ditemukan", ws_client)
            print(f"    {err(f'{nota} mesin Digital A3+ tidak ditemukan')}")
            return

        # cari mesin Kisscut/Diecut (Vulcan)
        finish_mac = None
        for mc in finish_flow.get("machine", []):
            mc_lower = mc["machine_name"].lower()
            if "vulcan" in mc_lower and proc_type in mc_lower:
                finish_mac = mc
                break
        if not finish_mac:
            for mc in finish_flow.get("machine", []):
                if "vulcan" in mc["machine_name"].lower():
                    finish_mac = mc
                    break
        if not finish_mac:
            send_manual(nota, f"mesin {proc_type.upper()} tidak ditemukan", ws_client)
            print(f"    {err(f'{nota} mesin {proc_type.upper()} tidak ditemukan')}")
            return

        # cari SDM (dari flow Digital A3+, operator sama)
        sdm_id = None
        sdm_name = ""
        op_low = operator_wma.lower()
        for s in digital_flow.get("sdm", []):
            s_low = s["name"].lower()
            if op_low in s_low or s_low in op_low:
                sdm_id = s["id"]
                sdm_name = s["name"]
                break
        if not sdm_id:
            send_manual(nota, f"operator {operator_wma} tidak di dropdown", ws_client)
            print(f"    {err(f'{nota} operator {operator_wma} tidak di dropdown')}")
            return

        qty_str = str(qty_file) if qty_file is not None else ""
        bahan_km = matched.get("bahan_km", "?")

        def _push_steps(digital_state, finish_state):
            # state: (status, msg); status: pending | ok | error
            if not (ws_client and ws_client.connected):
                return
            ws_client.send_step_detail(nota, [
                {
                    "name": digital_step.get("step_name") or digital_step.get("name") or "Digital A3+",
                    "machine": digital_mac["machine_name"],
                    "material": digital_mat["material_name"] if digital_mat else "-",
                    "sdm": sdm_name,
                    "qty": qty_str,
                    "status": digital_state[0],
                    "msg": digital_state[1],
                },
                {
                    "name": finish_step.get("step_name") or finish_step.get("name") or proc_type.upper(),
                    "machine": finish_mac["machine_name"],
                    "material": "-",
                    "sdm": sdm_name,
                    "qty": qty_str,
                    "status": finish_state[0],
                    "msg": finish_state[1],
                },
            ])

        _push_steps(("pending", ""), ("pending", ""))

        def _save_step(step_obj, mac_idm, mat_idm, sdm, qty, label, flow_obj):
            payload = {
                "flow_list[]": "+",
                "idtransaction_step[]": "",
                "flow_material_qty_0[]": "",
                "flow_machine_0": mac_idm,
                "is_flow_machine_manual_0": "0",
                "flow_machine_qty_0": "",
                "flow_qty_0": str(qty),
                "flow_sdm_0[]": sdm,
                "id": step_obj["idproduct_step"],
                "idtds": step_obj.get("idtransaction_detail", ""),
                "idtransaction_detail": id_td,
                "idtransaction_transfer": "",
                "idtransaction_reproduction": "",
            }
            if mat_idm:
                payload["flow_material_0[]"] = mat_idm
                payload["is_flow_material_manual_0[]"] = "0"
            print(f"  [DEBUG payload] {json.dumps(payload, ensure_ascii=False)}")
            ok_flag, msg = client.save_flow(payload)
            if ok_flag:
                print(f"    {ok(f'{nota} [{label}] SIMPAN OK')}")
            else:
                print(f"    {err(f'{nota} [{label}] SIMPAN gagal: {msg}')}")
            return ok_flag, msg

        # cek apakah Digital A3+ sudah pernah disimpan (err_code == 0 berarti sudah diproses)
        digital_already_saved = False
        for s in detail.get("step", []):
            if str(s.get("idproduct_step")) == str(digital_step["idproduct_step"]):
                ec = str(s.get("err_code", "")).strip()
                if ec and ec != "0":
                    digital_already_saved = True
                break

        # simpan Digital A3+ dulu
        if not digital_already_saved and digital_mat:
            cat_label = "DIGITAL A3+"
            d_mat_name = digital_mat["material_name"]
            d_mac_name = digital_mac["machine_name"]
            print(f"    {ok(f'{nota}  [{cat_label}]  {bahan_km} x {qty_str}  {d_mat_name} | {d_mac_name} | {sdm_name}')}")
            ok1, msg1 = _save_step(
                digital_step,
                digital_mac["idmachine"],
                digital_mat["idmaterial"],
                sdm_id,
                qty_str,
                cat_label,
                digital_flow,
            )
            if not ok1:
                _push_steps(("error", msg1), ("pending", ""))
                if ws_client and ws_client.connected:
                    ws_client.send_status(nota, "error", "gagal simpan Digital A3+")
                return
            _push_steps(("ok", ""), ("pending", ""))
            time.sleep(ACTION_DELAY)
        else:
            # Digital A3+ sudah pernah disimpan
            _push_steps(("ok", ""), ("pending", ""))

        # simpan step Kisscut/Diecut
        cat_label = proc_type.upper()
        f_mac_name = finish_mac["machine_name"]
        print(f"    {ok(f'{nota}  [{cat_label}]  {bahan_km} x {qty_str}  {f_mac_name} | {sdm_name}')}")
        ok2, msg2 = _save_step(finish_step, finish_mac["idmachine"], "", sdm_id, qty_str, cat_label, finish_flow)
        if ok2:
            _push_steps(("ok", ""), ("ok", ""))
            mark_completed(nota)
            if ws_client and ws_client.connected:
                ws_client.send_status(nota, "ok", f"{cat_label} | {bahan_km} x {qty_str} | {f_mac_name} | {sdm_name}")
        else:
            _push_steps(("ok", ""), ("error", msg2))
            if ws_client and ws_client.connected:
                ws_client.send_status(nota, "error", msg2)

    else:
        # Digital A3+ biasa — single step
        step = detail["flow"][0] if not proc_type else detail["flow"][0]
        if proc_type:
            for s in detail["flow"]:
                step_name = (s.get("step_name") or s.get("name") or "").lower()
                if proc_type in step_name:
                    step = s
                    break

        id_step = step["idproduct_step"]
        id_td_step = step.get("idtransaction_detail", "")
        id_td = id_td_order if id_td_order else id_td_step

        print(f"  [DEBUG] id_td_order={id_td_order} id_td_step={id_td_step} -> using {id_td}")

        flow = client.get_detail_flow(id_step, id_td_step)
        if not flow:
            print(f"    {err(f'{nota} gagal ambil form options')}")
            return

        mat_id = None
        mat_idmaterial = None
        mat_name = ""
        for m in flow.get("material", []):
            if bahan_file and match_bahan(bahan_file, m["material_name"]):
                mat_id = m["idproduct_material"]
                mat_idmaterial = m["idmaterial"]
                mat_name = m["material_name"]
                break
        if not mat_id:
            send_manual(nota, f"bahan {bahan_file} tidak di dropdown", ws_client)
            print(f"    {err(f'{nota} bahan {bahan_file} tidak di dropdown')}")
            return

        mac_id = None
        mac_idmachine = None
        mac_name = ""
        for mc in flow.get("machine", []):
            if machine_wma.lower() in mc["machine_name"].lower():
                mac_id = mc["idproduct_machine"]
                mac_idmachine = mc["idmachine"]
                mac_name = mc["machine_name"]
                break
        if not mac_id:
            for mc in flow.get("machine", []):
                if "develop" in machine_wma.lower() and "develop" in mc["machine_name"].lower():
                    mac_id = mc["idproduct_machine"]
                    mac_idmachine = mc["idmachine"]
                    mac_name = mc["machine_name"]
                    break
                elif "ricoh" in machine_wma.lower() and "ricoh" in mc["machine_name"].lower():
                    mac_id = mc["idproduct_machine"]
                    mac_idmachine = mc["idmachine"]
                    mac_name = mc["machine_name"]
                    break
        if not mac_id:
            send_manual(nota, f"mesin {machine_wma} tidak di dropdown", ws_client)
            print(f"    {err(f'{nota} mesin {machine_wma} tidak di dropdown')}")
            return

        sdm_id = None
        sdm_name = ""
        op_low = operator_wma.lower()
        for s in flow.get("sdm", []):
            s_low = s["name"].lower()
            if op_low in s_low or s_low in op_low:
                sdm_id = s["id"]
                sdm_name = s["name"]
                break
        if not sdm_id:
            send_manual(nota, f"operator {operator_wma} tidak di dropdown", ws_client)
            print(f"    {err(f'{nota} operator {operator_wma} tidak di dropdown')}")
            return

        qty_str = str(qty_file) if qty_file is not None else ""
        bahan_km = matched.get("bahan_km", "?")
        cat_label = "DIGITAL A3+"
        print(f"    {ok(f'{nota}  [{cat_label}]  {bahan_km} x {qty_str}  {mat_name} | {mac_name} | {sdm_name}')}")

        def _push_single(state, m=""):
            if ws_client and ws_client.connected:
                ws_client.send_step_detail(nota, [{
                    "name": "Digital A3+",
                    "machine": mac_name,
                    "material": mat_name,
                    "sdm": sdm_name,
                    "qty": qty_str,
                    "status": state,
                    "msg": m,
                }])

        _push_single("pending")

        payload = {
            "flow_list[]": "+",
            "idtransaction_step[]": "",
            "flow_material_qty_0[]": "",
            "flow_machine_0": mac_idmachine,
            "is_flow_machine_manual_0": "0",
            "flow_machine_qty_0": "",
            "flow_qty_0": qty_str,
            "flow_sdm_0[]": sdm_id,
            "id": id_step,
            "idtds": id_td_step,
            "idtransaction_detail": id_td,
            "idtransaction_transfer": "",
            "idtransaction_reproduction": "",
            "flow_material_0[]": mat_idmaterial,
            "is_flow_material_manual_0[]": "0",
        }
        print(f"  [DEBUG payload] {json.dumps(payload, ensure_ascii=False)}")

        ok_flag, msg = client.save_flow(payload)
        if ok_flag:
            _push_single("ok")
            mark_completed(nota)
            print(f"    {ok(f'{nota} SIMPAN OK')}")
            if ws_client and ws_client.connected:
                ws_client.send_status(nota, "ok", f"{cat_label} | {bahan_km} x {qty_str} | {mat_name} | {mac_name} | {sdm_name}")
        else:
            _push_single("error", msg)
            print(f"    {err(f'{nota} SIMPAN gagal: {msg}')}")
            if ws_client and ws_client.connected:
                ws_client.send_status(nota, "error", msg)


def run_entries(client, entries, orders_cache, username, password, ws_client=None, qty_override=None):
    """Proses daftar entries (dipakai process_now dan command handler)."""
    print(section("PROSES"))
    for i, entry in enumerate(entries, 1):
        if client.cookie is None:
            print(f"  {warn('session expired, re-login...')}")
            if not client.login(username, password):
                print(f"  {err('re-login gagal, skip sisa entries')}")
                break
        files_str = ", ".join(f[:30] for f in entry.get("files", [])[:2])
        print(f"  {progress(i, len(entries))} {dim(entry.get('nota', '?') + ' ' + files_str[:45])}")
        process_entry(client, entry, orders_cache, ws_client=ws_client, qty_override=qty_override)
        time.sleep(ACTION_DELAY)


def handle_kmma_command(cmd, client, username, password, ws_client=None):
    """Eksekusi perintah dari dashboard via hub. Actions: retry | skip | process_all."""
    if not isinstance(cmd, dict):
        return
    cid = cmd.get("id", "")
    action = cmd.get("action", "")
    nota = cmd.get("nota", "")
    reason = cmd.get("reason", "manual dari dashboard")

    def reply(is_ok, msg):
        if ws_client and ws_client.connected:
            ws_client.send_command_result(cid, action, nota, is_ok, msg)

    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"\n  {bold(f'[{ts}]')} {bold('CMD')} {action} {nota}")

    if action not in ("retry", "skip", "process_all"):
        reply(False, f"aksi tidak dikenal: {action}")
        return

    if action == "skip":
        if not nota:
            reply(False, "nota kosong")
            return
        log_skip(reason, nota)
        if ws_client and ws_client.connected:
            ws_client.send_status(nota, "error", f"SKIP: {reason}")
        reply(True, f"{nota} dilewati permanen")
        return

    if _process_lock.locked():
        reply(False, "KMMA sibuk memproses otomatis, coba lagi")
        return

    if not client.cookie:
        print(f"  {waiting('login KonsepMedia...')}")
        if not client.login(username, password):
            reply(False, "login KM gagal")
            return
        print(f"  {ok('login OK')}")

    print(f"  {waiting('ambil daftar order...')}")
    orders_cache = client.fetch_orders_page()
    if not orders_cache:
        reply(False, "gagal ambil daftar order")
        return

    if action == "process_all":
        entries = load_daily()
        if not entries:
            reply(False, "wma_daily.json kosong")
            return
        run_entries(client, entries, orders_cache, username, password, ws_client=ws_client)
        print(f"\n  {ok('selesai')}")
        reply(True, f"{len(entries)} entries diproses")
        return

    # retry: proses ulang satu nota (opsional qty override manual)
    qty_cmd = cmd.get("qty")
    qty_override = None
    if qty_cmd is not None and str(qty_cmd).strip() != "":
        try:
            qty_override = int(qty_cmd)
        except (ValueError, TypeError):
            reply(False, f"qty tidak valid: {qty_cmd}")
            return
    entries = [e for e in load_daily() if e.get("nota") == nota]
    if not entries:
        reply(False, f"{nota} tidak ada di wma_daily.json")
        return
    run_entries(client, entries, orders_cache, username, password, ws_client=ws_client, qty_override=qty_override)
    print(f"\n  {ok('selesai')}")
    cfg = load_cfg()
    if nota in cfg.get("completed", {}):
        suffix = f" (qty {qty_override})" if qty_override is not None else ""
        reply(True, f"{nota} SIMPAN OK{suffix}")
    elif nota in cfg.get("skip_forever", {}):
        reply(False, f"{nota} SKIP: {cfg['skip_forever'][nota]}")
    else:
        reply(False, f"{nota} gagal, cek log KMMA")


_process_lock = threading.Lock()  # cegah proses tumpuk (file/hub/auto)
_last_auto = 0.0  # trigger otomatis terakhir


def _run_watch(username, password, ws_client=None):
    """Event-driven mode: watch wma_daily.json + proses ulang tiap 3 menit."""
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    cls()
    print(header("KMMA  KONSEPMEDIA MODULE AUTO", f"watch mode  |  jeda {ACTION_DELAY}s  |  API {API_DELAY}s", 60))
    print()

    client = KMClient()
    login_fail_count = 0
    MAX_LOGIN_FAIL = 10
    cmd_queue = queue.Queue()
    if ws_client:
        ws_client.on_command = cmd_queue.put

    def process_now():
        global _last_auto
        _last_auto = time.time()
        if not _process_lock.acquire(blocking=False):
            print(f"  {dim('proses masih jalan, lewati trigger')}")
            return
        try:
            _process_now_inner()
        finally:
            _process_lock.release()

    def _process_now_inner():
        nonlocal login_fail_count

        entries = load_daily()
        if not entries:
            return

        ts = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"\n  {bold(f'[{ts}]')} {bold(f'{len(entries)} entries')} dari WMA")

        if not client.cookie:
            print(f"  {waiting('login KonsepMedia...')}")
            if not client.login(username, password):
                login_fail_count += 1
                if login_fail_count >= MAX_LOGIN_FAIL:
                    print(f"  {err(f'login gagal {login_fail_count}x, stop.')}")
                    return
                backoff = min(60 * login_fail_count, 600)
                print(f"  {err(f'login gagal ({login_fail_count}/{MAX_LOGIN_FAIL}), retry {backoff}s...')}")
                time.sleep(backoff)
                return
            login_fail_count = 0
            print(f"  {ok('login OK')}")

        print(f"  {waiting('ambil daftar order...')}")
        orders_cache = client.fetch_orders_page()
        if not orders_cache:
            print(f"  {err('gagal ambil daftar order')}")
            return
        print(f"  {ok(f'{len(orders_cache)} order di KM')}")

        run_entries(client, entries, orders_cache, username, password, ws_client=ws_client)

        print(f"\n  {ok('selesai')}")

    class DailyHandler(FileSystemEventHandler):
        def __init__(self):
            self._debounce = 0

        def on_modified(self, event):
            if event.is_directory:
                return
            if Path(event.src_path).name != "wma_daily.json":
                return
            now = time.time()
            if now - self._debounce < 2:
                return
            self._debounce = now
            print(f"\n  {ok('wma_daily.json berubah!')}")
            process_now()

        def on_created(self, event):
            self.on_modified(event)

    daily_dir = str(DAILY_PATH.parent)
    handler = DailyHandler()
    observer = Observer()
    observer.schedule(handler, daily_dir, recursive=False)
    observer.start()

    print(f"  Watching {DAILY_PATH.name}...")
    print(f"  Tekan Ctrl+C untuk stop")

    # process existing entries on start
    if load_daily():
        process_now()

    try:
        tick = 0
        while True:
            try:
                cmd = cmd_queue.get_nowait()
            except queue.Empty:
                cmd = None
            if cmd is not None:
                handle_kmma_command(cmd, client, username, password, ws_client=ws_client)
            if time.time() - _last_auto >= AUTO_INTERVAL:
                print(f"\n  {dim('proses ulang otomatis (3 mnt)')}")
                process_now()
            tick += 1
            if ws_client and tick % 5 == 0:
                try:
                    if ws_client.connected:
                        remain = max(0, int(AUTO_INTERVAL - (time.time() - _last_auto)))
                        ws_client.send_timer(remain, AUTO_INTERVAL)
                except Exception:
                    pass
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    print(f"\n  {ok('stopped')}")


def run():
    env = load_env()
    username = env.get("KMMA_USERNAME")
    password = env.get("KMMA_PASSWORD")
    if not username or not password:
        print(f"  {err('credentials tidak ada di env/kmma/.env')}")
        return

    # --hub flag
    global _WS
    ws_client = None
    args = sys.argv[1:]
    if "--hub" in args:
        hub_url = None
        if "--hub-url" in args:
            idx = args.index("--hub-url")
            if idx + 1 < len(args):
                hub_url = args[idx + 1]
        ws_client = WSClient("kmma", hub_url=hub_url)
        _WS = ws_client
        ws_client.connect()
        print(f"  {ok('WebSocket client started')}")

    # ── --watch mode: event-driven via watchdog ──
    if "--watch" in args:
        _run_watch(username, password, ws_client)
        return

    cls()
    print(header("KMMA  KONSEPMEDIA MODULE AUTO", f"polling {POLL_INTERVAL//60}m  |  jeda {ACTION_DELAY}s  |  API {API_DELAY}s", 60))
    print()

    client = KMClient()
    login_fail_count = 0
    MAX_LOGIN_FAIL = 10

    while True:
        entries = load_daily()
        if not entries:
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            print(f"  {dim(f'[{ts}] wma_daily.json kosong, tunggu {POLL_INTERVAL//60} menit...')}")
            time.sleep(POLL_INTERVAL)
            continue

        ts = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"\n  {bold(f'[{ts}]')} {bold(f'{len(entries)} entries')} dari WMA")

        if not client.cookie:
            print(f"  {waiting('login KonsepMedia...')}")
            if not client.login(username, password):
                login_fail_count += 1
                if login_fail_count >= MAX_LOGIN_FAIL:
                    print(f"  {err(f'login gagal {login_fail_count}x, stop.')}")
                    return
                backoff = min(60 * login_fail_count, 600)
                print(f"  {err(f'login gagal ({login_fail_count}/{MAX_LOGIN_FAIL}), retry {backoff}s...')}")
                time.sleep(backoff)
                continue
            login_fail_count = 0
            print(f"  {ok('login OK')}")

        print(f"  {waiting('ambil daftar order...')}")
        orders_cache = client.fetch_orders_page()
        if not orders_cache:
            print(f"  {err('gagal ambil daftar order')}")
            time.sleep(POLL_INTERVAL)
            continue
        print(f"  {ok(f'{len(orders_cache)} order di KM')}")

        print(section("PROSES"))
        for i, entry in enumerate(entries, 1):
            if client.cookie is None:
                print(f"  {warn('session expired, re-login...')}")
                if not client.login(username, password):
                    print(f"  {err('re-login gagal, skip sisa entries')}")
                    break
            files_str = ", ".join(f[:30] for f in entry.get("files", [])[:2])
            print(f"  {progress(i, len(entries))} {dim(entry.get('nota', '?') + ' ' + files_str[:45])}")
            process_entry(client, entry, orders_cache, ws_client=ws_client)
            time.sleep(ACTION_DELAY)

        print(f"\n  {ok(f'selesai, tunggu {POLL_INTERVAL//60} menit...')}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("\n  stop.")
