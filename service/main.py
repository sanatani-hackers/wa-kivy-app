"""Foreground-service process: owns neonize, the throttle queue and the plugin loader.
Runs separately from the Kivy UI, so it survives the Activity being swiped away."""
import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load module shims before importing neonize
import core._shims

from oscpy.client import OSCClient
from oscpy.server import OSCThreadServer

from common import HOST, SVC_PORT, UI_PORT, plugin_dir, private_dir, send_android_notification
from core.wa_client import WhatsAppCore


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    ui = OSCClient(HOST, UI_PORT, encoding="utf8")

    def emit(addr, *vals):
        val_str = " ".join(str(v) for v in vals) if vals else ""
        print(f"[SVC] {addr} {val_str}", flush=True)
        try:
            ui.send_message(addr.encode(), list(vals) or [""])
        except Exception:
            pass                                    # UI not running: fine
        if addr in {"/status", "/engine_state"} and vals:
            try:
                st = str(vals[0]).lower()
                if "connected" in st:
                    send_android_notification("WA Core", "🟢 Connected & Active (24/7 Monitoring)", notif_id=1001)
                elif "stopped" in st:
                    send_android_notification("WA Core", "⏸️ Bot Paused (Offline)", notif_id=1001)
                elif "connecting" in st:
                    send_android_notification("WA Core", "🔄 Connecting to WhatsApp...", notif_id=1001)
                elif "ready_to_pair" in st:
                    send_android_notification("WA Core", "📱 Ready to Pair - Scan QR or Enter Code", notif_id=1001)
            except Exception:
                pass

    class UiLogHandler(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.INFO:
                emit("/log", self.format(record)[:300])
    logging.getLogger().addHandler(UiLogHandler())

    core = WhatsAppCore(private_dir(), plugin_dir(), emit)

    server = OSCThreadServer(encoding="utf8")
    server.listen(HOST, port=SVC_PORT, default=True)
    run = lambda coro: asyncio.run_coroutine_threadsafe(coro, loop)

    async def _snapshot():
        await core.snapshot()

    async def _delete_plugin(name):
        if isinstance(name, bytes):
            name = name.decode()
        base = str(name).strip().removesuffix(".py").removesuffix(".pyc")
        deleted = False
        for ext in (".py", ".pyc"):
            p = os.path.join(plugin_dir(), f"{base}{ext}")
            if os.path.exists(p):
                try:
                    os.remove(p)
                    deleted = True
                except Exception as e:
                    emit("/log", f"Failed to delete {base}{ext}: {e}")
        if deleted:
            emit("/log", f"Deleted plugin {base}")
        await core.reload_plugins()

    async def _toggle_plugin(name):
        if isinstance(name, bytes):
            name = name.decode()
        base = str(name).strip().removesuffix(".py").removesuffix(".pyc")
        active = core.plugins.toggle(base)
        st = "enabled" if active else "disabled"
        emit("/log", f"Plugin {base} {st}")
        emit("/plugins", core.plugins.formatted_list())

    server.bind(b"/hello", lambda *a: run(_snapshot()))
    server.bind(b"/start_bot", lambda *a: run(core.resume()))
    server.bind(b"/stop_bot", lambda *a: run(core.stop()))
    server.bind(b"/pair", lambda phone: run(core.pair(phone)))
    server.bind(b"/send", lambda recipient, text: run(core.send_direct(recipient, text)))
    server.bind(b"/reload", lambda *a: run(core.reload_plugins()))
    server.bind(b"/toggle_plugin", lambda name: run(_toggle_plugin(name)))
    server.bind(b"/delete_plugin", lambda name: run(_delete_plugin(name)))
    server.bind(b"/clear_session", lambda *a: run(core.clear_session()))

    loop.create_task(core.start())
    loop.run_forever()


if __name__ == "__main__":
    main()
