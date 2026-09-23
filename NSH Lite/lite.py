"""NSH Lite: seperti NSH utama, TANPA Q-learning table.

Alur per file: cek skip -> parse guru -> impose -> pindah ke uda.
Preset 100% dari aturan guru (tidak belajar, tidak bertanya).
Pakai: python lite.py "<nama file>" [file ...] | --dir "<folder>"
"""
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
from llm_zen import llm_teacher_preset, skip_reason, ringkas

_SKIPPED = set()       # sudah di-skip permanen (skip_reason 1x atau gagal 3x)
_IMPOSED = set()       # nama file yang sudah diimpose di sesi ini (untuk deteksi duplikat)
_COOLDOWN = {}  # nama file -> timestamp gagal terakhir
_FAIL_COUNT = {}  # nama file -> jumlah gagal berturut (diluar cooldown)
_SEEN = {}  # path.lower() -> timestamp pertama kali terlihat (untuk delay impose)
COOLDOWN_S = 5
COOLDOWN_BEFORE = 5  # delay HANYA di impose, deteksi langsung
MAX_FAIL = 3
# E030=locked, E031=tidak bisa ditarik(3x locked), E032=gagal impose(3x), E034=duplikat
# E035=ukuran halaman beda


def normalize_dx(v):
    m = re.search(r"1d\s*(\d+)\s*(?:[=:@]\s*)?@?\s*(\d[\d.]*)?\s*(kecil|besar)?",
                  str(v), re.I)
    if not m:
        return str(v).strip()
    s = f"1d{m.group(1)}"
    if m.group(2):
        s += f" = {m.group(2).replace('.', '')}"
    if m.group(3):
        s += f" {m.group(3).upper()}"
    return s


def preset_for(filename):
    p = llm_teacher_preset(filename) or {}
    if p.get("dx"):
        p["dx"] = normalize_dx(p["dx"])
    return p


def to_bridge(preset, filename):
    """Preset guru -> format imposition_bridge."""
    b = dict(preset)
    b["repeat_mode"] = preset.get("repeat", "repeat")
    if preset.get("finishing") == "bleed":
        b.update({"mode": "crop", "bleed_mm": 2, "inner_crop": False,
                  "mark_len_mm": 5, "bleed_on": True, "line_color": "gray"})
    else:
        b.update({"mode": "crop", "bleed_mm": 0, "inner_crop": True,
                  "mark_len_mm": 5, "bleed_on": True, "line_color": "gray"})
    b["label"] = filename
    return b


G = "\033[92m"; Y = "\033[93m"; R = "\033[91m"; C = "\033[96m"; D = "\033[90m"; W = "\033[97m"; B = "\033[1m"; RST = "\033[0m"
USE_COLOR = sys.stdout.isatty()
_STATS = {"ok": 0, "skip": 0, "err": 0}


def _now():
    import time as _t
    return _t.strftime("%H:%M:%S")


def _log(color, tag, msg, detail=""):
    c = color if USE_COLOR else ""
    b = B if USE_COLOR else ""
    r = RST if USE_COLOR else ""
    d = f" {D}{detail}{R}" if detail else ""
    tag_w = f"{tag:<12}"
    try:
        print(f"{D}{_now()}{R} {c}{b}{tag_w}{R} {msg}{d}")
    except UnicodeEncodeError:
        # fallback cp1252 (tanpa box char)
        safe_detail = detail.replace("│","|").replace("→","->").replace("─","-")
        safe_msg = msg.replace("…","...")
        print(f"{_now()} {tag_w} {safe_msg} {safe_detail}")


def _banner():
    c = C if USE_COLOR else ""
    b = B if USE_COLOR else ""
    r = RST if USE_COLOR else ""
    print(f"{c}{b}======================================================{r}")
    print(f"{c}{b}  NSH Lite -- Guru Imposition  (tanpa Q-table){r}")
    print(f"{c}{b}======================================================{r}")


def _short(s, n=62):
    s = str(s)
    return s if len(s) <= n else s[:n-1] + "…"


def _extract_customer(name):
    """Ambil nama pelanggan dari filename: 'Pelanggan01_Bahan...' -> 'Pelanggan01'"""
    stem = Path(name).stem
    parts = stem.split("_", 1)
    return parts[0] if len(parts) > 1 else stem


def _check_page_sizes(pdf_path):
    """
    Cek apakah semua halaman PDF punya ukuran sama (toleransi 1pt).
    Return (ok, sizes) di mana:
      ok = True jika semua sama (dalam toleransi), False jika beda
      sizes = set tuple (width_pt, height_pt) dari semua halaman
    """
    try:
        import PyPDF2
        reader = PyPDF2.PdfReader(str(pdf_path))
        sizes = set()
        for page in reader.pages:
            box = page.mediabox
            w = float(box.width)
            h = float(box.height)
            # Normalize: portrait -> (small, large)
            if w > h:
                w, h = h, w
            # Bulatkan ke integer (toleransi 1pt = 0.35mm)
            sizes.add((round(w), round(h)))
        return len(sizes) <= 1, sizes
    except Exception:
        return True, set()  # gagal baca -> jangan block


def _write_error_txt(folder, filename, code, reason, err_msg):
    """Tulis .txt error di folder. Return Path atau None."""
    try:
        p = Path(folder) / f"{code} - {reason} ({filename}).txt"
        p.write_text(f"{reason} ({filename})\n[{code}] {err_msg}\n", encoding="utf-8")
        return p
    except Exception:
        return None


def process_one(fn, bridge, outdir, udadir):
    """Proses satu file. Return True jika di-impose."""
    import time as _time
    name = Path(fn).name
    if name in _SKIPPED:
        return False
    # --- duplikat: file sama sudah diimpose di sesi ini -> tetap impose, tapi mark .txt ---
    if name in _IMPOSED:
        pel = _extract_customer(name)
        txt = _write_error_txt(Path(fn).parent, name, "E034",
                               f"Duplikat Terdeteksi ({pel})", "File sudah ada di output")
        _log(Y, "[E034]", _short(name), f"Duplikat Terdeteksi ({pel}) -> {txt.name if txt else 'txt gagal'} (di Input)")
        # lanjut impose, tapi tetap mark
    sk = skip_reason(name)
    if sk:
        if name not in _SKIPPED:
            _STATS["skip"] += 1
            _log(Y, "[SKIP]", _short(name), f"-> {sk}")
            _SKIPPED.add(name)
        return False
    # --- cek ukuran halaman beda ---
    ok_sizes, sizes = _check_page_sizes(fn)
    if not ok_sizes:
        pel = _extract_customer(name)
        txt = _write_error_txt(Path(fn).parent, name, "E035",
                               f"Ukuran halaman Beda ({pel})",
                               f"UKURAN HALAMAN BEDA ({name}): {sizes}")
        _log(R, "[E035]", _short(name), f"Ukuran halaman Beda ({pel}) -> {txt.name if txt else 'txt gagal'} (di Input)")
        _STATS["skip"] += 1
        _SKIPPED.add(name)
        return False
    # track file yang lolos skip (untuk log)
    p = preset_for(name)
    rk = ringkas(name)
    rec = {"file": name, "ringkas": rk, "preset": p}
    # file sudah hilang di disk (duplikat glob / dipindah manual) -> diam, bukan error
    if not Path(fn).exists():
        return False
    # delay 5 detik: file harus sudah terlihat >= COOLDOWN_BEFORE baru impose
    first = _SEEN.get(str(Path(fn)).lower())
    if first and (_time.time() - first) < COOLDOWN_BEFORE:
        return False  # masih cooldown, skip tapi jangan log
    try:
        dst = outdir / name
        import io as _io
        from contextlib import redirect_stdout
        _buf = _io.StringIO()
        with redirect_stdout(_buf):
            bridge(fn, str(dst), to_bridge(p, name))
        target = udadir / name
        if target.exists():
            target = udadir / f"{Path(name).stem}_{int(_time.time())}{Path(name).suffix}"
        try:
            Path(fn).rename(target)
        except FileNotFoundError:
            pass  # sudah dipindah di iterasi sebelumnya (duplikat)
        rec["impose"] = str(dst)
        rec["uda"] = str(target)
        _STATS["ok"] += 1
        _IMPOSED.add(name)
        _log(G, "[ OK ]", _short(name), f"| {rk} -> {dst.name} -> uda/")
        _COOLDOWN.pop(name, None)
        _FAIL_COUNT.pop(name, None)
        return True
    except Exception as e:
        now = _time.time()
        if now - _COOLDOWN.get(name, 0) < COOLDOWN_S:
            return False
        _COOLDOWN[name] = now
        cnt = _FAIL_COUNT.get(name, 0) + 1
        _FAIL_COUNT[name] = cnt
        ename = type(e).__name__
        emsg_low = str(e).lower()
        # file sudah dipindah (duplikat glob *.pdf + *.PDF di Windows) -> diam, bukan error
        if ename == "FileNotFoundError" or "no such file" in emsg_low or "tidak ditemukan" in emsg_low or getattr(e, "code", "") == "E010":
            _FAIL_COUNT.pop(name, None)
            return False
        code = "E008"
        err_msg = str(e)[:100].strip() or ename
        is_locked = ename == "PermissionError" or "denied" in emsg_low or "locked" in emsg_low or "being used by another" in emsg_low
        # --- E035: ukuran halaman beda dari ImpositionTool ---
        is_page_mismatch = (getattr(e, "code", "") == "E035"
                            or "ukuran halaman" in emsg_low or "halaman beda" in emsg_low
                            or "page size" in emsg_low or "page mismatch" in emsg_low
                            or "tidak seragam" in emsg_low)
        if is_page_mismatch:
            pel = _extract_customer(name)
            txt = _write_error_txt(Path(fn).parent, name, "E035",
                                   f"Ukuran halaman Beda ({pel})",
                                   f"UKURAN HALAMAN BEDA ({name}): {err_msg}")
            _log(R, "[E035]", _short(name), f"Ukuran halaman Beda ({pel}) -> {txt.name if txt else 'txt gagal'} (di Input)")
            _STATS["skip"] += 1
            _SKIPPED.add(name)
            _FAIL_COUNT.pop(name, None)
            return False
        if is_locked:
            code = "E030"
            err_msg = "File dibuka/locked oleh program lain"
        elif "corrupt" in emsg_low or "pdf" in emsg_low and "error" in emsg_low:
            err_msg = f"PDF error: {str(e)[:80]}"
            code = "E008"
        rec["impose_error"] = f"[{code}] {err_msg} ({cnt}/{MAX_FAIL})"
        if cnt >= MAX_FAIL:
            if code == "E030":
                final_code = "E031"
                reason = "tidak bisa ditarik"
            else:
                final_code = "E032"
                reason = "gagal impose"
            rec["impose_error"] = f"[{final_code}] {reason} ({name}) - {err_msg}"
            rec["skip"] = f"{reason} [{final_code}]"
            try:
                pel = _extract_customer(name)
                log_file = Path(fn).parent / f"E031 - Tidak Bisa Ditarik ({pel}) ({name}).txt"
                log_file.write_text(f"tidak bisa ditarik ({name})\n[{final_code}] {err_msg}\n", encoding="utf-8")
                rec["txt"] = str(log_file)
            except Exception:
                log_file = None
            _STATS["err"] += 1
            _log(R, f"[{final_code} SKIP]", _short(f"tidak bisa ditarik ({name})"), f"{err_msg} -> {log_file.name if log_file else 'txt gagal'} (di Input)")
            _SKIPPED.add(name)
            _FAIL_COUNT.pop(name, None)
            return False
        _log(Y if code == "E030" else R, f"[{code} {cnt}/{MAX_FAIL}]", _short(name), err_msg)
        return False


def _nshdb():
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent / "Database"))
        import db as nshdb
        return nshdb
    except Exception:
        return None


def load_cfg():
    db = _nshdb()
    if db:
        try:
            cfg = db.config_get("lite")
            if cfg:
                return cfg
        except Exception:
            pass
    p = BASE / "lite.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cfg(cfg):
    db = _nshdb()
    if db:
        try:
            db.config_set("lite", cfg)
        except Exception:
            pass
    (BASE / "lite.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                                    encoding="utf-8")


def ask_folders():
    cfg = load_cfg()
    ind = Path(cfg.get("input_folder", str(BASE / "input")))
    outd = Path(cfg.get("output_folder", str(BASE / "impose")))
    udad = Path(cfg.get("uda_folder", str(BASE / "uda")))
    _banner()
    print()
    print(f"  {W}Setup Folder{RST}  {D}(Enter = pakai yang tersimpan){RST}")
    print(f"  ------------------------------------------------------")
    for label, val in [("INPUT ", ind), ("OUTPUT", outd), ("UDA   ", udad)]:
        print(f"    {label} : {C}{val}{RST}")
    print(f"  ------------------------------------------------------")
    try:
        a = input(f"  Folder INPUT?  Copas path (Enter=tetap): ").strip().strip('"').strip("'")
        if a:
            ind = Path(a)
            cfg["input_folder"] = str(ind)
        b = input(f"  Folder OUTPUT? Copas path (Enter=tetap): ").strip().strip('"').strip("'")
        if b:
            outd = Path(b)
            cfg["output_folder"] = str(outd)
        c = input(f"  Folder UDA?    Copas path (Enter=tetap): ").strip().strip('"').strip("'")
        if c:
            udad = Path(c)
            cfg["uda_folder"] = str(udad)
        save_cfg(cfg)
        print(f"  {D}tersimpan -> lite.json{RST}\n")
    except (EOFError, KeyboardInterrupt):
        print(f"\n{D}Setup dibatalkan, pakai tersimpan.{RST}\n")
    return ind, outd, udad


def watch_loop():
    """Mode watcher seperti run.bat NSH utama: pantau folder input selamanya."""
    import time as _time
    from imposition_bridge import impose_file
    indir, outdir, udadir = ask_folders()
    for d in (indir, outdir, udadir):
        d.mkdir(exist_ok=True)
    print(f"  Watcher aktif")
    print(f"    Input  : {C}{indir}{RST}")
    print(f"    Output : {C}{outdir}{RST}")
    print(f"    Uda    : {C}{udadir}{RST}")
    print(f"  Legenda: {G}[ OK ] berhasil{RST}  {Y}[SKIP] skip{RST}  {Y}[E030] locked{RST}  {R}[E031/E032] gagal->.txt{RST}  {D}| Ctrl+C stop{RST}")
    print(f"  ------------------------------------------------------")
    try:
        while True:
            seen = {}
            for f in list(indir.glob("*.pdf")) + list(indir.glob("*.PDF")):
                seen[str(f).lower()] = f
            for k in list(_SEEN.keys()):
                if k not in seen:
                    _SEEN.pop(k, None)
            n = 0
            for key, f in seen.items():
                # record waktu pertama kali terlihat
                if key not in _SEEN:
                    _SEEN[key] = _time.time()
                if process_one(str(f), impose_file, outdir, udadir):
                    n += 1
            if n:
                print(f"  -- {n} file selesai | total {G}{_STATS['ok']} OK{RST} {Y}{_STATS['skip']} SKIP{RST} {R}{_STATS['err']} ERR{RST}")
            _time.sleep(1)
    except KeyboardInterrupt:
        print(f"\n  ------------------------------------------------------")
        print(f"  Stop -- total {G}{_STATS['ok']} OK{RST}  {Y}{_STATS['skip']} SKIP{RST}  {R}{_STATS['err']} ERR{RST}")
        print(f"  ------------------------------------------------------")


def main():
    args = sys.argv[1:]
    do_impose = "--impose" in args
    args = [a for a in args if a != "--impose"]
    if not args:
        watch_loop()
        return
    bridge = None
    if do_impose:
        from imposition_bridge import impose_file
        bridge = impose_file
    names = []
    if args[0] == "--dir" and len(args) > 1:
        d = Path(args[1])
        names = [str(f) for f in list(d.glob("*.pdf")) + list(d.glob("*.PDF"))]
    else:
        names = args
    outdir = BASE / "impose"
    udadir = BASE / "uda"
    if do_impose:
        outdir.mkdir(exist_ok=True)
        udadir.mkdir(exist_ok=True)
    for fn in names:
        if do_impose and bridge:
            process_one(fn, bridge, outdir, udadir)
            continue
        name = Path(fn).name
        sk = skip_reason(name)
        if sk:
            _log(Y, "[SKIP]", _short(name), f"→ {sk}")
            continue
        p = preset_for(name)
        _log(C, "[PREVIEW]", _short(name), f"│ {ringkas(name)}  {D}│{RST} {p}")


if __name__ == "__main__":
    main()
