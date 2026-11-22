"""Foreground-service process: owns neonize, the throttle queue and the plugin loader.
Runs separately from the Kivy UI, so it survives the Activity being swiped away."""
import asyncio
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load module shims before importing neonize
import core._shims

from oscpy.client import OSCClient
from oscpy.server import OSCThreadServer

from common import (
    HOST,
    SVC_PORT,
    UI_PORT,
    activity_log_path,
    get_ram_usage_mb,
    plugin_dir,
    private_dir,
    send_android_notification,
    stop_android_service,
    update_service_notification,
)
from core.wa_client import WhatsAppCore


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    ui = OSCClient(HOST, UI_PORT, encoding="utf8")

    def _update_notif():
        try:
            ram = get_ram_usage_mb()
            is_run = bool(core._run_task and not core._run_task.done())
            active_p = len(core.plugins.loaded) - len(core.plugins.disabled)
            tot_p = len(core.plugins.loaded)
            cur_st = str(getattr(core, "status", "disconnected")).lower()
            st_text = "Online" if is_run else "Paused"
            if "connected" in cur_st or "paired" in cur_st:
                net_text = "Connected"
            elif not is_run or "stopped" in cur_st:
                net_text = "Offline"
            else:
                net_text = "Connecting..."
            short_t = f"{net_text} | RAM: {ram}MB | Plugins: {active_p}/{tot_p}"
            big_t = f"State: {st_text}\nNetwork: {net_text}\nRAM Usage: {ram} MB\nActive Plugins: {active_p} / {tot_p}"
            update_service_notification(f"WA Core • {st_text}", short_t, is_running=is_run, stats_text=big_t)
        except Exception as e:
            print(f"[SVC _update_notif error] {e}", flush=True)

    def emit(addr, *vals):
        val_str = " ".join(str(v) for v in vals) if vals else ""
        print(f"[SVC] {addr} {val_str}", flush=True)
        try:
            ui.send_message(addr.encode(), list(vals) or [""])
        except Exception:
            pass                                    # UI not running: fine
        if addr in {"/status", "/engine_state"}:
            _update_notif()

    class UiLogHandler(logging.Handler):
        def emit(self, record):
            msg = self.format(record)
            if record.levelno >= logging.INFO:
                emit("/log", msg[:300])
            try:
                with open(activity_log_path(), "a", encoding="utf-8") as f:
                    f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
            except Exception:
                pass

    logging.getLogger().addHandler(UiLogHandler())

    core = WhatsAppCore(private_dir(), plugin_dir(), emit)

    # Register Android broadcast receiver for controllable notification buttons
    try:
        from android.broadcast import BroadcastReceiver
        def _on_broadcast(context, intent):
            act = intent.getAction()
            if act == "org.sanatani.waapp.ACTION_STOP":
                run(core.stop())
            elif act == "org.sanatani.waapp.ACTION_START":
                run(core.resume())
        _br = BroadcastReceiver(_on_broadcast, actions=["org.sanatani.waapp.ACTION_STOP", "org.sanatani.waapp.ACTION_START"])
        _br.start()
    except Exception:
        pass

    server = OSCThreadServer(encoding="utf8")
    server.listen(HOST, port=SVC_PORT, default=True)
    run = lambda coro: asyncio.run_coroutine_threadsafe(coro, loop)

    async def _snapshot():
        await core.snapshot()
        _update_notif()

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
        _update_notif()

    async def _toggle_plugin(name):
        if isinstance(name, bytes):
            name = name.decode()
        base = str(name).strip().removesuffix(".py").removesuffix(".pyc")
        active = core.plugins.toggle(base)
        st = "enabled" if active else "disabled"
        emit("/log", f"Plugin {base} {st}")
        emit("/plugins", core.plugins.formatted_list())
        _update_notif()

    async def _wipe_cache():
        count = 0
        p_anti = core.plugins.loaded.get("antidelete")
        if p_anti and hasattr(p_anti["mod"], "MESSAGE_CACHE"):
            count = len(p_anti["mod"].MESSAGE_CACHE)
            p_anti["mod"].MESSAGE_CACHE.clear()
        emit("/log", f"Cache wiped cleanly ({count} items purged).")
        emit("/cache_wiped", str(count))

    async def _kill_service():
        emit("/log", "Stopping WhatsApp engine and terminating background service...")
        try:
            await asyncio.wait_for(core.stop(), timeout=3.0)
        except Exception:
            pass
        stop_android_service()
        await asyncio.sleep(0.3)
        os._exit(0)

    # Periodic background stats updater (every 8 seconds)
    async def _periodic_notif_loop():
        while True:
            await asyncio.sleep(8)
            _update_notif()

    server.bind(b"/hello", lambda *a: run(_snapshot()))
    server.bind(b"/start_bot", lambda *a: run(core.resume()))
    server.bind(b"/stop_bot", lambda *a: run(core.stop()))
    server.bind(b"/kill_service", lambda *a: run(_kill_service()))
    server.bind(b"/pair", lambda phone: run(core.pair(phone)))
    server.bind(b"/send", lambda recipient, text: run(core.send_direct(recipient, text)))
    server.bind(b"/reload", lambda *a: run(core.reload_plugins()))
    server.bind(b"/toggle_plugin", lambda name: run(_toggle_plugin(name)))
    server.bind(b"/delete_plugin", lambda name: run(_delete_plugin(name)))
    server.bind(b"/clear_session", lambda *a: run(core.clear_session()))
    server.bind(b"/wipe_cache", lambda *a: run(_wipe_cache()))

    loop.create_task(core.start())
    loop.create_task(_periodic_notif_loop())

    try:
        loop.run_forever()
    finally:
        server.terminate()
        server.close()


if __name__ == "__main__":
    main()
