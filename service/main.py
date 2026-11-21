"""Foreground-service process: owns neonize, the throttle queue and the plugin loader.
Runs separately from the Kivy UI, so it survives the Activity being swiped away."""
import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oscpy.client import OSCClient
from oscpy.server import OSCThreadServer

from common import HOST, SVC_PORT, UI_PORT, plugin_dir, private_dir
from core.wa_client import WhatsAppCore


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    ui = OSCClient(HOST, UI_PORT, encoding="utf8")

    def emit(addr, *vals):
        try:
            ui.send_message(addr.encode(), list(vals) or [""])
        except Exception:
            pass                                    # UI not running: fine

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
        core.snapshot()

    async def _delete_plugin(name):
        if isinstance(name, bytes):
            name = name.decode()
        name = str(name).strip()
        if not name.endswith(".py"):
            name += ".py"
        path = os.path.join(plugin_dir(), name)
        if os.path.exists(path):
            try:
                os.remove(path)
                emit("/log", f"Deleted plugin {name}")
            except Exception as e:
                emit("/log", f"Failed to delete {name}: {e}")
        await core.reload_plugins()

    server.bind(b"/hello", lambda *a: run(_snapshot()))
    server.bind(b"/pair", lambda phone: run(core.pair(phone)))
    server.bind(b"/send", lambda recipient, text: run(core.send_direct(recipient, text)))
    server.bind(b"/reload", lambda *a: run(core.reload_plugins()))
    server.bind(b"/delete_plugin", lambda name: run(_delete_plugin(name)))
    server.bind(b"/clear_session", lambda *a: run(core.clear_session()))

    loop.create_task(core.start())
    loop.run_forever()


if __name__ == "__main__":
    main()
