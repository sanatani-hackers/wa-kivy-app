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
                    if not os.path.exists(dst_py):
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
