# NSHTools

NSH (Nanas Second Hand) — sistem imposition & routing terpusat.

## Modul

| Modul | Path | Peran |
|-------|------|--------|
| **Hub** | `hub_server.py` | WebSocket :8765 + HTTP :8766, orkestrasi & log |
| **NSH Lite** | `NSH Lite/` | Imposition watcher |
| **KMA** | `KMA/` | Router INPUT → hot folder printer |
| **WMA** | `WMA/` | Sinkron selesai → `wma_daily.json` |
| **KMMA** | `KMMA/` | Auto isi form KonsepMedia |
| **Database** | `Database/` | SQLite pusat (`nsh.db`) |
| **Dashboard** | `Dashboard/` | React UI (Vite, port 8443) |

## Jalankan

```bat
start_all.bat
```

Hub + semua modul (`--supervise`) + dashboard.

Atau manual:

```bat
hub.bat
```

Dashboard: http://localhost:8443  
Legacy panel: http://localhost:8766

## Konfigurasi

- Path folder (INPUT/OUTPUT/UDA): tab **NSH Lite** / **KMA** → *Simpan Path*
- URL hub dari PC lain: **Pengaturan** → `ws://<IP-PC-utama>:8765`
- Kredensial KMMA: `KMMA/env/kmma/.env` (**tidak ikut di-commit**)
