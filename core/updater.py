"""In-App OTA Self-Updater for WA Core via GitHub Releases.
Checks for new versions, streams signed APK downloads, and invokes the Android Package Installer.
"""
import logging
import os
import re

import requests

log = logging.getLogger("wa.updater")

GITHUB_API_URL = "https://api.github.com/repos/sanatani-hackers/wa-kivy-app/releases/latest"


def _parse_version(v_str: str) -> tuple[int, ...]:
    """Extract numeric version tuple from strings like 'v0.2.0' or '0.2.1-beta'."""
    clean = re.sub(r"[^0-9.]", "", v_str)
    try:
        return tuple(int(x) for x in clean.split(".") if x.isdigit())
    except Exception:
        return (0, 0, 0)


def check_for_updates(current_version: str = "0.2.0") -> tuple[bool, dict, str]:
    """Check GitHub Releases for an updated version."""
    try:
        resp = requests.get(GITHUB_API_URL, timeout=10, headers={"User-Agent": "WACore-Updater/1.0"})
        if resp.status_code == 404:
            return False, {}, "No GitHub releases found for this repository yet."
        if resp.status_code != 200:
            return False, {}, f"GitHub returned HTTP {resp.status_code}"
        data = resp.json()
    except Exception as e:
        return False, {}, f"Network error checking for updates: {e}"

    tag = data.get("tag_name", "").strip()
    cur_tuple = _parse_version(current_version)
    latest_tuple = _parse_version(tag)

    has_update = latest_tuple > cur_tuple

    # Look for signed APK asset
    assets = data.get("assets", [])
    apk_url = None
    apk_size = 0
    apk_name = ""

    # Priority 1: signed arm64 APK
    for a in assets:
        name = a.get("name", "").lower()
        if name.endswith(".apk"):
            if "signed" in name:
                apk_url = a.get("browser_download_url")
                apk_size = a.get("size", 0)
                apk_name = a.get("name")
                break

    # Priority 2: any APK asset
    if not apk_url:
        for a in assets:
            if a.get("name", "").endswith(".apk"):
                apk_url = a.get("browser_download_url")
                apk_size = a.get("size", 0)
                apk_name = a.get("name")
                break

    info = {
        "has_update": has_update,
        "current_version": current_version,
        "latest_version": tag or "0.2.0",
        "title": data.get("name") or f"Release {tag}",
        "changelog": data.get("body") or "No changelog provided.",
        "download_url": apk_url,
        "filename": apk_name or f"waapp-{tag}.apk",
        "size_mb": round(apk_size / (1024 * 1024), 1) if apk_size else 0,
        "published_at": data.get("published_at", "")[:10],
    }

    if has_update and apk_url:
        msg = f"New version {tag} is available! (Current: v{current_version})"
    elif has_update and not apk_url:
        msg = f"New version {tag} tagged, but no APK attached to the release."
    else:
        msg = f"WA Core is up to date (v{current_version})."

    return True, info, msg


def download_update_apk(download_url: str, dest_path: str = None, on_progress=None) -> tuple[bool, str]:
    """Download update APK directly to device storage with percentage callbacks."""
    if not download_url:
        return False, "Invalid download URL."

    if not dest_path:
        dest_dir = "/sdcard/Download"
        if not os.path.isdir(dest_dir):
            from common import backup_folder
            dest_dir = backup_folder()
        dest_path = os.path.join(dest_dir, "waapp-latest-update.apk")

    prog_fn = on_progress or (lambda pct, mb, tot: None)

    try:
        resp = requests.get(download_url, timeout=60, stream=True, headers={"User-Agent": "WACore-Updater/1.0"})
        if resp.status_code != 200:
            return False, f"Download failed with HTTP {resp.status_code}"

        total_bytes = int(resp.headers.get("content-length", 0))
        downloaded = 0

        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_bytes > 0:
                        pct = int((downloaded / total_bytes) * 100)
                        prog_fn(pct, downloaded / (1024 * 1024), total_bytes / (1024 * 1024))

        return True, dest_path
    except Exception as e:
        return False, f"Download error: {e}"


def launch_apk_installer(apk_path: str) -> tuple[bool, str]:
    """Launch Android Package Installer for the downloaded APK using native Intent."""
    if not os.path.exists(apk_path):
        return False, f"APK file not found: {apk_path}"

    try:
        from jnius import autoclass
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        Intent = autoclass("android.content.Intent")
        File = autoclass("java.io.File")
        act = PythonActivity.mActivity
        if not act:
            return False, "Android Activity not available."

        apk_file = File(apk_path)
        intent = Intent(Intent.ACTION_VIEW)

        # Android 7.0+ (API 24+) requires FileProvider
        try:
            FileProvider = autoclass("androidx.core.content.FileProvider")
            pkg_name = act.getPackageName()
            uri = FileProvider.getUriForFile(act, f"{pkg_name}.fileprovider", apk_file)
            intent.setDataAndType(uri, "application/vnd.android.package-archive")
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        except Exception:
            Uri = autoclass("android.net.Uri")
            intent.setDataAndType(Uri.fromFile(apk_file), "application/vnd.android.package-archive")

        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        act.startActivity(intent)
        return True, "Installation prompt opened."
    except Exception as e:
        return False, f"Could not launch installer: {e}\nAPK saved to: {apk_path}"
