"""importlib-based plugin loader. Drop .py files into plugin_dir(); no APK rebuild needed.

Plugin contract (all optional except on_message or setup):
    NAME = "ping"
    async def setup(ctx): ...                # called once after load (register ctx.every(...) here)
    async def on_message(ctx, msg): ...      # called for every incoming message
    async def teardown(ctx): ...             # called before unload/reload
"""
import asyncio
import importlib.util
import logging
import os
import sys

from .throttle import Job

log = logging.getLogger("wa.plugins")


class PluginContext:
    """The ONLY handle a plugin gets. No raw neonize client => cannot bypass the throttle."""

    def __init__(self, name, core):
        self.name, self._core = name, core
        self.log = logging.getLogger(f"plugin.{name}")

    async def reply(self, msg, text: str) -> bool:
        return self._core.queue.put(Job(msg.chat, msg.chat_id, text, self.name, quoted=msg.raw))

    async def send(self, number: str, text: str) -> bool:
        return self._core.queue.put(Job(self._core.jid_for(number), number, text, self.name))

    def every(self, seconds: float, fn) -> bool:
        """Run `async fn(ctx)` periodically (min interval enforced by the supervisor)."""
        return self._core.supervisor.every(self.name, seconds, lambda: fn(self))


class PluginManager:
    def __init__(self, folder, core):
        self.folder, self.core = folder, core
        self.loaded: dict[str, dict] = {}
        self._tasks: set = set()

    async def reload(self) -> dict:
        """Rescan the folder: load new files, reload changed ones, unload deleted ones."""
        os.makedirs(self.folder, exist_ok=True)
        # Seed bundled plugins if not present
        bundle_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins")
        if os.path.isdir(bundle_dir):
            import shutil
            for f in os.listdir(bundle_dir):
                if f.endswith(".py") and not f.startswith("_"):
                    dst = os.path.join(self.folder, f)
                    if not os.path.exists(dst):
                        try:
                            shutil.copy2(os.path.join(bundle_dir, f), dst)
                        except Exception:
                            pass
        files = {f[:-3]: os.path.join(self.folder, f)
                 for f in sorted(os.listdir(self.folder)) if f.endswith(".py") and not f.startswith("_")}
        report = {"loaded": [], "failed": {}, "removed": []}
        for name in list(self.loaded):
            gone = name not in files
            if gone or os.path.getmtime(files[name]) != self.loaded[name]["mtime"]:
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
                log.exception("failed to load plugin %s", name)
                report["failed"][name] = repr(e)
        return report

    async def _load(self, name, path):
        spec = importlib.util.spec_from_file_location(f"wa_plugin_{name}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)                       # top-level code runs here: keep it light
        ctx = PluginContext(name, self.core)
        self.core.supervisor.reset(name)
        self.loaded[name] = {"mod": mod, "ctx": ctx, "mtime": os.path.getmtime(path)}
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
        """Fan-out to every plugin concurrently; one slow plugin can't hold up the others."""
        for name, p in self.loaded.items():
            fn = getattr(p["mod"], "on_message", None)
            if fn and not self.core.supervisor.is_quarantined(name):
                t = asyncio.create_task(self.core.supervisor.run(name, fn, p["ctx"], msg))
                self._tasks.add(t)
                t.add_done_callback(self._tasks.discard)

    def names(self):
        return sorted(self.loaded)
