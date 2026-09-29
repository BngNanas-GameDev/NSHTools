"""ws_client.py — WebSocket client untuk WMA & KMMA.
Hubungkan ke NSH Hub, kirim/receive messages.

Pakai:
    from ws_client import WSClient

    ws = WSClient("wma")
    ws.connect()         # thread terpisah, auto-reconnect
    ws.send_entries([...])
    ws.send_status(nota, "ok", "msg")
    ws.on_entries = lambda entries: ...  # callback
"""
import asyncio
import json
import threading
import time
import urllib.request
from pathlib import Path

# ══════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════
BASE = Path(__file__).parent
CFG_PATH = BASE / "hub.json"


def load_hub_url():
    """Load hub URL from hub.json or default."""
    try:
        cfg = json.loads(CFG_PATH.read_text(encoding="utf-8"))
        return cfg.get("url", "ws://localhost:8765")
    except Exception:
        return "ws://localhost:8765"


class WSClient:
    """WebSocket client — runs in background thread."""

    def __init__(self, role="wma", hub_url=None):
        self.role = role
        self.hub_url = hub_url or load_hub_url()
        self._ws = None
        self._loop = None
        self._thread = None
        self._connected = False
        self._send_queue = []

        # callbacks
        self.on_entries = None  # lambda entries: ...
        self.on_status = None   # lambda nota, status, msg: ...
        self.on_log = None      # lambda module, msg: ...
        self.on_command = None  # lambda cmd_dict: ... (KMMA side)
        self.on_command_result = None  # lambda result_dict: ... (dashboard side)
        self.on_we_update = None  # lambda msg_dict: ... (WE berubah -> WMA sync realtime)

    def connect(self):
        """Start WebSocket client in background thread."""
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._connect_forever())

    async def _connect_forever(self):
        import websockets
        backoff = 1  # 1→2→4→…→60s; reset hanya bila koneksi bertahan >30s
        while True:
            connected_at = 0.0
            try:
                async with websockets.connect(self.hub_url, ping_interval=20, ping_timeout=180) as ws:
                    connected_at = time.time()
                    self._ws = ws
                    self._send_queue.clear()  # jangan kirim antrean basi ke koneksi baru
                    print(f"  [WS] {self.role} connected to {self.hub_url}")

                    # subscribe
                    await ws.send(json.dumps({
                        "type": "subscribe",
                        "role": self.role,
                        "id": self.role,
                    }))
                    self._connected = True

                    # flush queue
                    while self._send_queue:
                        msg = self._send_queue.pop(0)
                        await ws.send(msg)

                    # listen
                    async for raw in ws:
                        msg = json.loads(raw)
                        await self._handle(msg)

            except Exception as e:
                self._connected = False
                self._ws = None
                print(f"  [WS] {self.role} disconnected: {e}")
            finally:
                self._connected = False
                self._ws = None
                if connected_at and (time.time() - connected_at) > 30:
                    backoff = 1  # koneksi stabil → mulai lagi dari 1s
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _handle(self, msg):
        msg_type = msg.get("type", "")
        if msg_type == "entries" and self.on_entries:
            try:
                self.on_entries(msg.get("entries", []))
            except Exception:
                pass
        elif msg_type == "status_update" and self.on_status:
            try:
                self.on_status(msg.get("nota", ""), msg.get("status", ""), msg.get("msg", ""))
            except Exception:
                pass
        elif msg_type == "log" and self.on_log:
            try:
                self.on_log(msg.get("module", ""), msg.get("msg", ""))
            except Exception:
                pass
        elif msg_type == "command" and self.on_command:
            try:
                self.on_command(msg)
            except Exception:
                pass
        elif msg_type == "command_result" and self.on_command_result:
            try:
                self.on_command_result(msg)
            except Exception:
                pass
        elif msg_type == "we_update" and self.on_we_update:
            try:
                self.on_we_update(msg)
            except Exception:
                pass

    def send(self, msg_dict):
        """Thread-safe send."""
        raw = json.dumps(msg_dict, ensure_ascii=False)
        if self._connected and self._ws:
            try:
                asyncio.run_coroutine_threadsafe(self._ws.send(raw), self._loop)
            except Exception:
                self._send_queue.append(raw)
        else:
            self._send_queue.append(raw)

    def send_entries(self, entries):
        """Send new entries from WMA."""
        self.send({"type": "new_entries", "entries": entries})

    def send_status(self, nota, status, msg="", key=None):
        """Send status update from KMMA. key = (nota,bahan,sides,laminating,test[,process_type])
        untuk status per-baris (None = kompatibel lama per-nota)."""
        body = {"type": "status", "nota": nota, "status": status, "msg": msg}
        if key is not None:
            try:
                n, b, s, l, t = key[0], key[1], key[2], key[3], key[4]
                body["bahan"] = b or ""
                body["sides"] = s or ""
                body["laminating"] = l or ""
                body["test"] = bool(t)
                body["process_type"] = key[5] if len(key) > 5 else ""
            except Exception:
                pass
        self.send(body)

    def send_step_detail(self, nota, steps, key=None):
        """Send per-step detail from KMMA.

        steps: list of {name, machine, material, sdm, qty, status, msg}
        status: 'pending' | 'ok' | 'error'
        key = (nota,bahan,sides,laminating,test[,process_type]) untuk
        step_detail per-baris (None = kompatibel lama per-nota).
        """
        body = {"type": "step_detail", "nota": nota, "steps": steps}
        if key is not None:
            try:
                n, b, s, l, t = key[0], key[1], key[2], key[3], key[4]
                body["bahan"] = b or ""
                body["sides"] = s or ""
                body["laminating"] = l or ""
                body["test"] = bool(t)
                body["process_type"] = key[5] if len(key) > 5 else ""
            except Exception:
                pass
        self.send(body)

    def send_timer(self, next_in, interval):
        """Lapor hitung mundur siklus KMMA (detik)."""
        self.send({"type": "kmma_timer", "next_in": int(next_in), "interval": int(interval)})

    def send_log(self, module, msg):
        """Send a log message."""
        self.send({"type": "log", "module": module, "msg": msg})

    def send_command_result(self, cid, action, nota, ok, msg=""):
        """Reply to a dashboard command (KMMA side)."""
        self.send({
            "type": "command_result",
            "id": cid,
            "action": action,
            "nota": nota,
            "ok": bool(ok),
            "msg": msg,
        })

    def send_kma_state(self, state):
        """Push KMA router state (mirror hub kma_state):
        {routes, printers, rules, stats}."""
        printers = state.get("printers", state.get("machines", []))
        self.send({"type": "kma_state", "routes": state.get("routes", []),
                   "printers": printers, "rules": state.get("rules", []),
                   "stats": state.get("stats", {})})

    @property
    def connected(self):
        return self._connected


# Quick test
if __name__ == "__main__":
    c = WSClient("test")
    c.connect()
    for i in range(5):
        print(f"connected={c.connected}")
        time.sleep(1)
