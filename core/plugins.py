"""importlib-based plugin loader. Drop .py files into plugin_dir(); no APK rebuild needed.

Plugin contract (all optional except on_message, on_call, or setup):
    NAME = "ping"
    async def setup(ctx): ...                # called once after load (register ctx.every(...) here)
    async def on_message(ctx, msg): ...      # called for every incoming message
    async def on_call(ctx, call_ev): ...     # called for call lifecycle events (incoming, ready, end, etc.)
    async def teardown(ctx): ...             # called before unload/reload
"""
import asyncio
import importlib.util
import json
import logging
import os
import sqlite3
import sys

from common import plugin_data_dir, plugin_media_dir
from .throttle import Job

log = logging.getLogger("wa.plugins")


class PluginContext:
    """The handle a plugin gets. Provides throttled messaging, scheduling, logger, isolated storage and DB handles."""

    def __init__(self, name: str, core):
        self.name = str(name).strip()
        self._core = core
        self.log = logging.getLogger(f"plugin.{self.name}")
        self.data_dir = plugin_data_dir(self.name)
        self.media_dir = plugin_media_dir(self.name)

    def get_path(self, filename: str, public: bool = False) -> str:
        """Return absolute path for a file inside this plugin's isolated directory."""
        folder = self.media_dir if public else self.data_dir
        return os.path.join(folder, filename)

    def load_json(self, filename: str, default=None):
        """Safely load JSON configuration or state from plugin private storage."""
        p = self.get_path(filename, public=False)
        if not os.path.exists(p):
            return default if default is not None else {}
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self.log.warning("load_json %s error: %s", filename, e)
            return default

    def save_json(self, filename: str, data) -> bool:
        """Atomically persist JSON data to plugin private storage."""
        p = self.get_path(filename, public=False)
        tmp = f"{p}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, p)
            return True
        except Exception as e:
            self.log.error("save_json %s error: %s", filename, e)
            return False

    def get_db(self, db_name: str = None) -> sqlite3.Connection:
        """Get or create an isolated SQLite database connection with WAL mode enabled."""
        name = db_name or f"{self.name}.sqlite3"
        path = self.get_path(name, public=False)
        conn = sqlite3.connect(path)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
        except Exception:
            pass
        return conn

    async def reply(self, msg, text: str) -> bool:
        return self._core.queue.put(Job(msg.chat, msg.chat_id, text, self.name, quoted=msg.raw))

    async def send(self, number: str, text: str) -> bool:
        return self._core.queue.put(Job(self._core.jid_for(number), number, text, self.name))

    def every(self, seconds: float, fn) -> bool:
        """Run `async fn(ctx)` periodically (min interval enforced by the supervisor)."""
        return self._core.supervisor.every(self.name, seconds, lambda: fn(self))

    # ---- VoIP & Video Call Control Helpers
    @property
    def calls(self):
        return self._core.calls

    async def call(self, target: str, video: bool = False) -> dict:
        return await self._core.calls.place_call(target, is_video=video)

    async def answer_call(self, call_id: str) -> dict:
        return await self._core.calls.answer(call_id)

    async def decline_call(self, call_id: str, caller_jid=None) -> dict:
        return await self._core.calls.decline(call_id, caller_jid=caller_jid)

    async def hangup_call(self, call_id: str) -> dict:
        return await self._core.calls.hangup(call_id)

    async def play_call_audio(self, call_id: str, track: str = "", loop: bool = False) -> dict:
        return await self._core.calls.play_audio(call_id, track, loop=loop)

    async def play_call_url(
        self,
        call_id: str,
        url: str,
        headers: dict = None,
        content_length: int = 0,
        label: str = "Live Stream",
        max_seconds: int = 3600,
    ) -> dict:
        return await self._core.calls.play_url_stream(
            call_id,
            url,
            headers=headers,
            content_length=content_length,
            label=label,
            max_seconds=max_seconds,
        )

    async def pause_call_audio(self, call_id: str) -> dict:
        return await self._core.calls.pause_audio(call_id)

    async def resume_call_audio(self, call_id: str) -> dict:
        return await self._core.calls.resume_audio(call_id)

    async def stop_call_audio(self, call_id: str) -> dict:
        return await self._core.calls.stop_audio(call_id)

    async def play_call_video(self, call_id: str, video_file: str = "", fps: int = 15, loop: bool = True, with_audio: bool = True) -> dict:
        return await self._core.calls.play_video(call_id, video_file, fps=fps, loop=loop, with_audio=with_audio)

    async def stop_call_video(self, call_id: str) -> dict:
        return await self._core.calls.stop_video_stream(call_id)

    async def record_call_audio(self, call_id: str, path: str = "auto") -> dict:
        return await self._core.calls.record_audio(call_id, path)

    async def record_call_video(self, call_id: str, path: str = "auto") -> dict:
        return await self._core.calls.record_video(call_id, path)

    async def send_call_reaction(self, call_id: str, emoji: str) -> dict:
        return await self._core.calls.send_reaction(call_id, emoji)


class PluginManager:
    def __init__(self, folder, core):
        self.folder, self.core = folder, core
        self.loaded: dict[str, dict] = {}
        self.disabled: set[str] = set()
        self._tasks: set = set()

    async def reload(self) -> dict:
        """Rescan the folder: load new files, reload changed ones, unload deleted ones."""
        os.makedirs(self.folder, exist_ok=True)
        app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bundle_dir = os.path.join(app_root, "plugins")
        bundle_json = os.path.join(app_root, "bundled_plugins.json")

        # 0. Seed bundled plugins from bundled_plugins.json if present
        if os.path.exists(bundle_json):
            try:
                import json
                with open(bundle_json, "r", encoding="utf-8") as fp:
                    b_dict = json.load(fp)
                for name, code in b_dict.items():
                    dst_py = os.path.join(self.folder, f"{name}.py")
                    should_write = not os.path.exists(dst_py)
                    if not should_write and name == "ytcall" and "play_call_url" in code:
                        try:
                            with open(dst_py, "r", encoding="utf-8", errors="ignore") as cur_fp:
                                cur_src = cur_fp.read()
                            if "sanatani-hackers/ytstream" in cur_src and "play_call_url" not in cur_src:
                                should_write = True
                        except Exception:
                            pass
                    if should_write:
                        with open(dst_py, "w", encoding="utf-8") as fp:
                            fp.write(code)
            except Exception as e:
                log.debug("could not seed plugins from json: %s", e)

        # 1. Seed bundled plugins (.py or .pyc) if not already in self.folder
        if os.path.isdir(bundle_dir):
            import shutil
            for f in os.listdir(bundle_dir):
                if f.startswith("_"):
                    continue
                if f.endswith((".py", ".pyc")):
                    base = f[:-4] if f.endswith(".pyc") else f[:-3]
                    dst_py = os.path.join(self.folder, f"{base}.py")
                    dst_pyc = os.path.join(self.folder, f"{base}.pyc")
                    if not os.path.exists(dst_py) and not os.path.exists(dst_pyc):
                        try:
                            shutil.copy2(os.path.join(bundle_dir, f), os.path.join(self.folder, f))
                        except Exception as e:
                            log.debug("could not seed plugin %s: %s", f, e)

        # 2. Collect candidate files (prefer .py over .pyc)
        files: dict[str, str] = {}
        # Scan user folder
        if os.path.isdir(self.folder):
            for f in sorted(os.listdir(self.folder)):
                if f.startswith("_"):
                    continue
                if f.endswith(".py"):
                    files[f[:-3]] = os.path.join(self.folder, f)
                elif f.endswith(".pyc"):
                    name = f[:-4]
                    if name not in files:
                        files[name] = os.path.join(self.folder, f)

        # Fallback to bundle_dir for any missing built-in plugins
        if os.path.isdir(bundle_dir):
            for f in sorted(os.listdir(bundle_dir)):
                if f.startswith("_"):
                    continue
                if f.endswith(".py"):
                    name = f[:-3]
                    if name not in files:
                        files[name] = os.path.join(bundle_dir, f)
                elif f.endswith(".pyc"):
                    name = f[:-4]
                    if name not in files:
                        files[name] = os.path.join(bundle_dir, f)

        report = {"loaded": [], "failed": {}, "removed": []}
        for name in list(self.loaded):
            gone = name not in files
            cur_path = files.get(name)
            prev_entry = self.loaded[name]
            path_changed = cur_path != prev_entry.get("path")
            mtime_changed = False
            if cur_path and os.path.exists(cur_path):
                mtime_changed = os.path.getmtime(cur_path) != prev_entry.get("mtime")
            if gone or path_changed or mtime_changed:
                await self._unload(name)
                if gone:
                    report["removed"].append(name)
        for name, path in files.items():
            if name in self.loaded:
                continue
            try:
                await self._load(name, path)
                report["loaded"].append(name)
            except Exception as e:
                log.exception("failed to load plugin %s from %s", name, path)
                report["failed"][name] = repr(e)
        return report

    async def _load(self, name, path):
        spec = importlib.util.spec_from_file_location(f"wa_plugin_{name}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)                       # top-level code runs here: keep it light
        ctx = PluginContext(name, self.core)
        self.core.supervisor.reset(name)
        self.loaded[name] = {"mod": mod, "ctx": ctx, "mtime": os.path.getmtime(path), "path": path}
        sys.modules[spec.name] = mod
        if hasattr(mod, "setup"):
            await self.core.supervisor.run(name, mod.setup, ctx)

    async def _unload(self, name):
        p = self.loaded.pop(name, None)
        if not p:
            return
        self.core.supervisor.cancel_plugin(name)
        if hasattr(p["mod"], "teardown"):
            await self.core.supervisor.run(name, p["mod"].teardown, p["ctx"])
        sys.modules.pop(f"wa_plugin_{name}", None)

    def dispatch(self, msg):
        """Fan-out to every active plugin concurrently; one slow plugin can't hold up the others."""
        for name, p in self.loaded.items():
            if name in self.disabled:
                continue
            fn = getattr(p["mod"], "on_message", None)
            if fn and not self.core.supervisor.is_quarantined(name):
                t = asyncio.create_task(self.core.supervisor.run(name, fn, p["ctx"], msg))
                self._tasks.add(t)
                t.add_done_callback(self._tasks.discard)

    def dispatch_call(self, call_ev):
        """Fan-out call lifecycle events (incoming, ready, end, etc.) to active plugins defining on_call."""
        for name, p in self.loaded.items():
            if name in self.disabled:
                continue
            fn = getattr(p["mod"], "on_call", None)
            if fn and not self.core.supervisor.is_quarantined(name):
                t = asyncio.create_task(self.core.supervisor.run(name, fn, p["ctx"], call_ev))
                self._tasks.add(t)
                t.add_done_callback(self._tasks.discard)

    def toggle(self, name: str) -> bool:
        """Toggle enable/disable state of a plugin. Returns True if now enabled, False if disabled."""
        clean = str(name).strip().removesuffix(".py").removesuffix(".pyc")
        if clean in self.disabled:
            self.disabled.remove(clean)
            return True
        else:
            self.disabled.add(clean)
            return False

    def is_enabled(self, name: str) -> bool:
        clean = str(name).strip().removesuffix(".py").removesuffix(".pyc")
        return clean not in self.disabled

    def formatted_list(self) -> str:
        """Return formatted string like 'ping:1,antidelete:0'."""
        items = []
        for name in sorted(self.loaded):
            val = "0" if name in self.disabled else "1"
            items.append(f"{name}:{val}")
        return ",".join(items)

    def names(self):
        return sorted(self.loaded)
