"""Dynamic in-app package manager & wheel unpacker for WA Core plugins.
Supports pure-python wheels from PyPI and local .whl / .zip packages.
"""
import email
import importlib
import importlib.util
import json
import logging
import os
import shutil
import sys
import tempfile
import urllib.parse
import zipfile

from common import plugin_packages_dir

log = logging.getLogger("wa.piphub")


def _get_pkg_dir() -> str:
    pkg_dir = plugin_packages_dir()
    if pkg_dir not in sys.path:
        sys.path.insert(0, pkg_dir)
    return pkg_dir


def is_package_available(pkg_name: str) -> bool:
    """Check if a package is importable in the current environment or plugin_packages."""
    _get_pkg_dir()
    clean = pkg_name.strip().replace("-", "_").lower()
    try:
        if importlib.util.find_spec(clean) is not None:
            return True
    except Exception:
        pass
    try:
        __import__(clean)
        return True
    except Exception:
        return False


def list_installed_packages() -> list[dict]:
    """Scan plugin_packages_dir for all dynamically installed packages and their metadata."""
    pkg_dir = _get_pkg_dir()
    if not os.path.isdir(pkg_dir):
        return []

    results = []
    for entry in sorted(os.listdir(pkg_dir)):
        if entry.endswith(".dist-info") and os.path.isdir(os.path.join(pkg_dir, entry)):
            meta_path = os.path.join(pkg_dir, entry, "METADATA")
            pkg_info = {
                "name": entry.split("-")[0],
                "version": "unknown",
                "summary": "No description",
                "folder": entry,
                "size_kb": 0,
            }
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8", errors="ignore") as f:
                        msg = email.message_from_string(f.read())
                    pkg_info["name"] = msg.get("Name", pkg_info["name"])
                    pkg_info["version"] = msg.get("Version", "unknown")
                    pkg_info["summary"] = msg.get("Summary", "No description")
                except Exception as e:
                    log.debug("error reading metadata for %s: %s", entry, e)

            # Estimate total size
            total_b = 0
            # Look at RECORD if present
            record_path = os.path.join(pkg_dir, entry, "RECORD")
            if os.path.exists(record_path):
                try:
                    with open(record_path, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            parts = line.strip().split(",")
                            if len(parts) >= 2 and parts[1].isdigit():
                                total_b += int(parts[1])
                except Exception:
                    pass
            if total_b == 0:
                # Calculate size of dist-info folder at least
                for root, _, files in os.walk(os.path.join(pkg_dir, entry)):
                    for f in files:
                        total_b += os.path.getsize(os.path.join(root, f))
            pkg_info["size_kb"] = max(1, total_b // 1024)
            results.append(pkg_info)
    return results


def install_pypi_package(pkg_name: str, install_deps: bool = True) -> tuple[bool, str]:
    """Download a pure-Python wheel from PyPI and unpack it into plugin_packages."""
    import requests

    clean_name = pkg_name.strip()
    if not clean_name:
        return False, "Package name cannot be empty."

    pypi_url = f"https://pypi.org/pypi/{urllib.parse.quote(clean_name)}/json"
    try:
        resp = requests.get(pypi_url, timeout=12, headers={"User-Agent": "WACore-PipHub/1.0"})
        if resp.status_code == 404:
            return False, f"Package '{clean_name}' not found on PyPI."
        if resp.status_code != 200:
            return False, f"PyPI returned HTTP error: {resp.status_code}"
        data = resp.json()
    except Exception as e:
        return False, f"Network error contacting PyPI: {e}"

    info = data.get("info", {})
    version = info.get("version", "latest")
    urls = data.get("urls", [])

    # Find candidate wheels:
    # 1. Pure Python wheels (py3-none-any or py2.py3-none-any)
    wheel_url = None
    wheel_filename = ""
    for u in urls:
        fname = u.get("filename", "")
        if fname.endswith(".whl"):
            if "none-any" in fname:
                wheel_url = u.get("url")
                wheel_filename = fname
                break

    if not wheel_url:
        # Check if only binary wheels exist
        has_binary = any(u.get("filename", "").endswith(".whl") for u in urls)
        if has_binary:
            return False, (
                f"Package '{clean_name}' contains native C/C++ binaries and no pure-Python wheel. "
                "Linux binary wheels cannot run on Android. Please provide an Android-compiled .whl file."
            )
        return False, f"No compatible pure-Python wheel found on PyPI for '{clean_name}'."

    # Download wheel
    pkg_dir = _get_pkg_dir()
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".whl")
    os.close(tmp_fd)

    try:
        dl_resp = requests.get(wheel_url, timeout=30, stream=True, headers={"User-Agent": "WACore-PipHub/1.0"})
        if dl_resp.status_code != 200:
            return False, f"Failed to download wheel (HTTP {dl_resp.status_code})"
        with open(tmp_path, "wb") as f:
            for chunk in dl_resp.iter_content(chunk_size=16384):
                if chunk:
                    f.write(chunk)

        # Unpack wheel (wheels are zip archives)
        with zipfile.ZipFile(tmp_path, "r") as zf:
            zf.extractall(pkg_dir)

        # Ensure sys.path knows about it
        if pkg_dir not in sys.path:
            sys.path.insert(0, pkg_dir)

        # Invalidate import caches
        importlib.invalidate_caches()

        msg = f"Installed {clean_name} v{version} successfully."

        # Optionally install pure-python requirements (e.g. beautifulsoup4 -> soupsieve)
        reqs = info.get("requires_dist") or []
        if install_deps and reqs:
            deps_installed = []
            for r in reqs:
                # Basic requirement parsing e.g. "soupsieve (>1.2); extra == 'test'"
                dep_clean = r.split(";")[0].split("(")[0].split(">")[0].split("<")[0].split("=")[0].strip()
                if dep_clean and not is_package_available(dep_clean):
                    ok, _ = install_pypi_package(dep_clean, install_deps=False)
                    if ok:
                        deps_installed.append(dep_clean)
            if deps_installed:
                msg += f" (Dependencies installed: {', '.join(deps_installed)})"

        return True, msg
    except Exception as e:
        return False, f"Installation failed: {e}"
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def install_local_wheel(wheel_path: str) -> tuple[bool, str]:
    """Install a local .whl or .zip package file from device storage."""
    wheel_path = wheel_path.strip()
    if not os.path.exists(wheel_path):
        return False, f"File not found: {wheel_path}"

    if not zipfile.is_zipfile(wheel_path):
        return False, f"Not a valid zip or wheel archive: {os.path.basename(wheel_path)}"

    pkg_dir = _get_pkg_dir()
    try:
        with zipfile.ZipFile(wheel_path, "r") as zf:
            zf.extractall(pkg_dir)

        importlib.invalidate_caches()
        return True, f"Installed package from {os.path.basename(wheel_path)}"
    except Exception as e:
        return False, f"Failed to extract package: {e}"


def uninstall_package(pkg_name: str) -> tuple[bool, str]:
    """Remove an installed package and its dist-info metadata from plugin_packages."""
    pkg_dir = _get_pkg_dir()
    clean = pkg_name.strip().replace("-", "_").lower()

    removed = False
    for entry in os.listdir(pkg_dir):
        entry_clean = entry.split("-")[0].replace("-", "_").lower()
        if entry_clean == clean:
            full = os.path.join(pkg_dir, entry)
            try:
                if os.path.isdir(full):
                    shutil.rmtree(full)
                else:
                    os.remove(full)
                removed = True
            except Exception as e:
                return False, f"Error removing {entry}: {e}"

    importlib.invalidate_caches()
    if removed:
        return True, f"Uninstalled '{pkg_name}' successfully."
    return False, f"Package '{pkg_name}' was not found in plugin packages."
