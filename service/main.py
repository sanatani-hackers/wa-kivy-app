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
    cancel_android_notification,
    get_ram_usage_mb,
    plugin_dir,
    private_dir,
    send_android_notification,
    send_call_notification,
    stop_android_service,
    update_service_notification,
)
from core import call_media
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
            active_calls = getattr(core.calls, "_active", {}) if hasattr(core, "calls") else {}
            if active_calls:
                first_call = next(iter(active_calls.values()))
                c_peer = first_call.get("peer_number") or "Caller"
                c_track = first_call.get("audio_path") or ""
                c_phase = str(first_call.get("phase") or "active").capitalize()
                chip_title = f"♪ Call • {c_peer}" if c_track else f"Call • {c_peer}"
                short_t = f"{c_phase} | {c_track}" if c_track else f"{c_phase} | RAM: {ram}MB"
                big_t = (
                    f"Active Call: {c_peer} ({c_phase})\n"
                    f"Track: {c_track or 'Idle'}\n"
                    f"Network: {net_text} | RAM: {ram} MB | Plugins: {active_p}/{tot_p}"
                )
                update_service_notification(chip_title, short_t, is_running=is_run, stats_text=big_t)
            else:
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
        if addr in {"/status", "/engine_state", "/call_event"}:
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
            elif act == "org.sanatani.waapp.ACTION_CALL_ANSWER_MUSIC":
                cid = ""
                try:
                    cid = str(intent.getStringExtra("call_id") or "")
                except Exception:
                    pass
                run(_notif_answer_and_play(cid))
            elif act == "org.sanatani.waapp.ACTION_CALL_DECLINE":
                cid = ""
                try:
                    cid = str(intent.getStringExtra("call_id") or "")
                except Exception:
                    pass
                run(_notif_decline(cid))
        _br = BroadcastReceiver(_on_broadcast, actions=[
            "org.sanatani.waapp.ACTION_STOP",
            "org.sanatani.waapp.ACTION_START",
            "org.sanatani.waapp.ACTION_CALL_ANSWER_MUSIC",
            "org.sanatani.waapp.ACTION_CALL_DECLINE",
        ])
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

    def _dec(v):
        if isinstance(v, bytes):
            return v.decode("utf-8", "ignore")
        return str(v or "")

    async def _notif_answer_and_play(cid: str):
        cancel_android_notification(3001)
        cid = cid or (core.calls.latest_active_call_id() or "")
        if not cid:
            return
        res = await core.calls.answer(cid)
        if res.get("ok"):
            # Brief wait for relay RTP readiness before starting default track
            for _ in range(30):
                st = core.calls._active.get(cid, {})
                if st.get("phase") == "active":
                    break
                await asyncio.sleep(0.25)
            await core.calls.play_audio(cid, "", loop=True)

    async def _notif_decline(cid: str):
        cancel_android_notification(3001)
        cid = cid or (core.calls.latest_active_call_id() or "")
        if cid:
            await core.calls.decline(cid)

    async def _call_place(target, is_video):
        tgt = _dec(target).strip()
        vid = int(is_video) != 0 if str(is_video).isdigit() else bool(is_video)
        res = await core.calls.place_call(tgt, is_video=vid)
        if not res.get("ok"):
            emit("/log", f"✗ CallPlace error: {res.get('error')}")

    async def _call_play(call_id, track, loop_flag):
        cid = _dec(call_id).strip() or (core.calls.latest_active_call_id() or "")
        if not cid:
            emit("/log", "✗ No active call selected to play audio.")
            return
        lp = int(loop_flag) != 0 if str(loop_flag).lstrip("-").isdigit() else bool(loop_flag)
        res = await core.calls.play_audio(cid, _dec(track).strip(), loop=lp)
        if not res.get("ok"):
            emit("/log", f"✗ Audio play error: {res.get('error')}")
        else:
            emit("/call_event", json.dumps({"event": "track_update", "active_calls": list(core.calls._active.values())}))

    async def _call_play_video(call_id, vpath, fps, loop_flag):
        cid = _dec(call_id).strip() or (core.calls.latest_active_call_id() or "")
        if not cid:
            emit("/log", "✗ No active call selected to stream video.")
            return
        try:
            fps_i = int(fps)
        except Exception:
            fps_i = 15
        lp = int(loop_flag) != 0 if str(loop_flag).lstrip("-").isdigit() else True
        res = await core.calls.play_video(cid, _dec(vpath).strip(), fps=fps_i, loop=lp)
        if not res.get("ok"):
            emit("/log", f"✗ Video stream error: {res.get('error')}")

    async def _call_refresh():
        emit("/call_tracks", json.dumps(call_media.list_media_tracks()))
        emit("/call_history", json.dumps(core.calls.history(25)))
        emit("/call_event", json.dumps({
            "event": "snapshot",
            "supported": core.calls.is_supported(),
            "active_calls": list(core.calls._active.values()),
        }))

    async def _call_simple(action: str, cid_raw="", extra=None):
        cid = _dec(cid_raw).strip() or (core.calls.latest_active_call_id() or "")
        if not cid:
            emit("/log", f"✗ Cannot {action}: no active call.")
            return
        if action == "answer":
            cancel_android_notification(3001)
            res = await core.calls.answer(cid)
        elif action == "decline":
            cancel_android_notification(3001)
            res = await core.calls.decline(cid)
        elif action == "hangup":
            cancel_android_notification(3001)
            res = await core.calls.hangup(cid)
        elif action == "pause":
            res = await core.calls.pause_audio(cid)
        elif action == "resume":
            res = await core.calls.resume_audio(cid)
        elif action == "stop_audio":
            res = await core.calls.stop_audio(cid)
        elif action == "stop_video":
            res = await core.calls.stop_video_stream(cid)
        elif action == "record":
            en = 1 if extra is None else (int(extra) if str(extra).lstrip("-").isdigit() else 1)
            res = await core.calls.record_audio(cid, "auto" if en else "")
        elif action == "react":
            res = await core.calls.send_reaction(cid, _dec(extra) or "🎵")
        else:
            res = {"ok": False, "error": f"unknown action {action}"}
        if not res.get("ok"):
            emit("/log", f"✗ Call {action} failed: {res.get('error')}")

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
    server.bind(b"/call_place", lambda *a: run(_call_place(a[0] if len(a) > 0 else "", a[1] if len(a) > 1 else 0)))
    server.bind(b"/call_answer", lambda *a: run(_call_simple("answer", a[0] if a else "")))
    server.bind(b"/call_decline", lambda *a: run(_call_simple("decline", a[0] if a else "")))
    server.bind(b"/call_hangup", lambda *a: run(_call_simple("hangup", a[0] if a else "")))
    server.bind(b"/call_play", lambda *a: run(_call_play(a[0] if len(a) > 0 else "", a[1] if len(a) > 1 else "", a[2] if len(a) > 2 else 1)))
    server.bind(b"/call_pause", lambda *a: run(_call_simple("pause", a[0] if a else "")))
    server.bind(b"/call_resume", lambda *a: run(_call_simple("resume", a[0] if a else "")))
    server.bind(b"/call_stop_audio", lambda *a: run(_call_simple("stop_audio", a[0] if a else "")))
    server.bind(b"/call_play_video", lambda *a: run(_call_play_video(a[0] if len(a) > 0 else "", a[1] if len(a) > 1 else "", a[2] if len(a) > 2 else 15, a[3] if len(a) > 3 else 1)))
    server.bind(b"/call_stop_video", lambda *a: run(_call_simple("stop_video", a[0] if a else "")))
    server.bind(b"/call_record", lambda *a: run(_call_simple("record", a[0] if len(a) > 0 else "", a[1] if len(a) > 1 else 1)))
    server.bind(b"/call_react", lambda *a: run(_call_simple("react", a[0] if len(a) > 0 else "", a[1] if len(a) > 1 else "🎵")))
    server.bind(b"/call_refresh", lambda *a: run(_call_refresh()))
    server.bind(b"/call_clear_history", lambda *a: (core.calls.clear_history(), run(_call_refresh())))

    loop.create_task(core.start())
    loop.create_task(_periodic_notif_loop())

    try:
        loop.run_forever()
    finally:
        server.terminate()
        server.close()


if __name__ == "__main__":
    main()
