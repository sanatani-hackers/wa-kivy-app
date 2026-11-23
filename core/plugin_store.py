"""Official GitHub Plugin Store & 3rd-Party Importer for WA Core.
Handles catalog discovery, dependency validation, AST verification, and hot installation.
"""
import ast
import json
import logging
import os
import re

import requests

from common import plugin_dir, private_dir
from core import pip_hub

log = logging.getLogger("wa.store")

DEFAULT_CATALOG_URL = (
    "https://raw.githubusercontent.com/sanatani-hackers/wa-kivy-app/main/wa_app/store/plugins_index.json"
)


def _get_cache_path() -> str:
    return os.path.join(private_dir(), "store_cache.json")


def _get_bundled_catalog_path() -> str:
    app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(app_root, "store", "plugins_index.json")


def fetch_catalog(custom_url: str = None) -> tuple[bool, dict, str]:
    """Fetch the plugin catalog from GitHub, falling back to local cache or bundled fallback."""
    url = (custom_url or "").strip() or DEFAULT_CATALOG_URL
    cache_file = _get_cache_path()

    # 1. Attempt network fetch
    try:
        resp = requests.get(url, timeout=8, headers={"User-Agent": "WACore-Store/1.0"})
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and "plugins" in data:
                # Cache successful fetch
                try:
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)
                except Exception:
                    pass
                return True, data, "Online catalog loaded."
    except Exception as e:
        log.warning("Catalog fetch failed (%s), attempting offline cache: %s", url, e)

    # 2. Attempt offline cache
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return True, data, "Loaded from offline cache (Network unavailable)."
        except Exception:
            pass

    # 3. Fallback to bundled catalog
    bundled = _get_bundled_catalog_path()
    if os.path.exists(bundled):
        try:
            with open(bundled, "r", encoding="utf-8") as f:
                data = json.load(f)
            return True, data, "Loaded bundled catalog."
        except Exception:
            pass

    return False, {"plugins": [], "categories": ["All"]}, "Could not load plugin catalog."


def get_installed_plugin_ids() -> set[str]:
    """Return set of installed plugin IDs in the user's plugin folder."""
    pdir = plugin_dir()
    if not os.path.isdir(pdir):
        return set()
    installed = set()
    for f in os.listdir(pdir):
        if f.endswith((".py", ".pyc")) and not f.startswith("_"):
            installed.add(f.rsplit(".", 1)[0])
    return installed


def validate_python_syntax(code: str) -> tuple[bool, str]:
    """Verify code can be parsed into an AST without syntax errors."""
    try:
        ast.parse(code)
        return True, "Syntax OK"
    except SyntaxError as e:
        return False, f"SyntaxError at line {e.lineno}: {e.msg}"
    except Exception as e:
        return False, f"Validation error: {e}"


def extract_plugin_metadata(code: str, fallback_name: str = "custom_plugin") -> dict:
    """Extract NAME, docstring description, and # requires: from plugin source code."""
    meta = {
        "id": fallback_name,
        "name": fallback_name.replace("_", " ").title(),
        "description": "No description provided.",
        "requires": [],
        "author": "Custom / 3rd Party",
    }

    # Extract NAME = "..."
    m_name = re.search(r'^\s*NAME\s*=\s*["\']([^"\']+)["\']', code, re.MULTILINE)
    if m_name:
        meta["id"] = m_name.group(1).strip()
        meta["name"] = meta["id"].replace("_", " ").title()

    # Extract docstring
    try:
        tree = ast.parse(code)
        doc = ast.get_docstring(tree)
        if doc:
            meta["description"] = doc.strip().split("\n\n")[0].replace("\n", " ")
    except Exception:
        pass

    # Extract # requires: pkg1, pkg2 or # deps: ...
    m_req = re.search(r'#\s*(?:requires|deps|dependencies)\s*:\s*([^\n]+)', code, re.IGNORECASE)
    if m_req:
        raw_deps = m_req.group(1).split(",")
        meta["requires"] = [d.strip() for d in raw_deps if d.strip()]

    return meta


def install_plugin_from_store(entry: dict, on_log=None) -> tuple[bool, str]:
    """Download a plugin from the store, install missing dependencies, validate, and save."""
    dl_url = entry.get("download_url")
    plugin_id = entry.get("id") or "plugin"
    if not dl_url:
        return False, "Plugin has no download URL."

    log_fn = on_log or (lambda msg: None)
    log_fn(f"Downloading {entry.get('name', plugin_id)}...")

    try:
        resp = requests.get(dl_url, timeout=15, headers={"User-Agent": "WACore-Store/1.0"})
        if resp.status_code != 200:
            return False, f"Download failed with HTTP {resp.status_code}"
        code = resp.text
    except Exception as e:
        return False, f"Network error downloading plugin: {e}"

    # Syntax validation
    ok, err = validate_python_syntax(code)
    if not ok:
        return False, f"Corrupt plugin code: {err}"

    # Check and install pure-python requirements
    reqs = entry.get("requires") or []
    for dep in reqs:
        if not pip_hub.is_package_available(dep):
            log_fn(f"Installing missing requirement: {dep}...")
            ok, dep_err = pip_hub.install_pypi_package(dep)
            if not ok:
                log.warning("Could not auto-install requirement %s: %s", dep, dep_err)

    # Save to user plugins directory
    target_path = os.path.join(plugin_dir(), f"{plugin_id}.py")
    try:
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(code)
        return True, f"Installed {entry.get('name', plugin_id)} successfully."
    except Exception as e:
        return False, f"Failed to save plugin file: {e}"


def import_from_url(url: str) -> tuple[bool, str, dict]:
    """Fetch raw code from GitHub / Web, extract metadata, and validate."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return False, "Invalid URL (must start with http:// or https://)", {}

    # Convert GitHub web URL to raw URL if needed
    if "github.com" in url and "/blob/" in url:
        url = url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")

    try:
        resp = requests.get(url, timeout=12, headers={"User-Agent": "WACore-Store/1.0"})
        if resp.status_code != 200:
            return False, f"HTTP Error {resp.status_code} fetching URL", {}
        code = resp.text
    except Exception as e:
        return False, f"Network error: {e}", {}

    ok, err = validate_python_syntax(code)
    if not ok:
        return False, f"Syntax verification failed: {err}", {}

    fallback_id = url.rstrip("/").split("/")[-1].replace(".py", "") or "imported_plugin"
    meta = extract_plugin_metadata(code, fallback_name=fallback_id)
    return True, code, meta


def import_from_file(file_path: str) -> tuple[bool, str, dict]:
    """Load local .py file from device storage, extract metadata, and validate."""
    file_path = file_path.strip()
    if not os.path.exists(file_path):
        return False, f"File not found: {file_path}", {}

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            code = f.read()
    except Exception as e:
        return False, f"Failed to read file: {e}", {}

    ok, err = validate_python_syntax(code)
    if not ok:
        return False, f"Syntax verification failed: {err}", {}

    fallback_id = os.path.basename(file_path).replace(".py", "")
    meta = extract_plugin_metadata(code, fallback_name=fallback_id)
    return True, code, meta


def save_plugin(plugin_id: str, code: str, requires: list = None, on_log=None) -> tuple[bool, str]:
    """Save code to plugins/<plugin_id>.py after syntax validation and dependency installation."""
    clean_id = str(plugin_id).strip().replace(" ", "_").lower().removesuffix(".py")
    if not clean_id:
        return False, "Plugin ID cannot be empty."

    ok, err = validate_python_syntax(code)
    if not ok:
        return False, err

    log_fn = on_log or (lambda msg: None)
    if requires:
        for dep in requires:
            if not pip_hub.is_package_available(dep):
                log_fn(f"Installing requirement: {dep}...")
                pip_hub.install_pypi_package(dep)

    target_path = os.path.join(plugin_dir(), f"{clean_id}.py")
    try:
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(code)
        return True, f"Saved plugin as {clean_id}.py"
    except Exception as e:
        return False, f"Failed to write file: {e}"
