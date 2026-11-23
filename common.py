"""Shared constants + path helpers (imported by both the UI and the service process)."""
import os
import shutil
import time
import zipfile

PACKAGE = "org.sanatani.waapp"      # package.domain + "." + package.name in buildozer.spec
SERVICE = "Whatsapp"                # services = whatsapp:...  ->  class <PACKAGE>.ServiceWhatsapp
HOST = "127.0.0.1"
UI_PORT = int(os.environ.get("WA_UI_PORT", 38201))                     # UI process listens here
SVC_PORT = int(os.environ.get("WA_SVC_PORT", 38202))                    # service process listens here


def _android_ctx():
    """Return an Android Context (service ctx in the service process, activity in the UI) or None."""
    try:
        from jnius import autoclass
        for cls, field in (("org.kivy.android.PythonService", "mService"),
                           ("org.kivy.android.PythonActivity", "mActivity")):
            ctx = getattr(autoclass(cls), field)
            if ctx is not None:
                return ctx
    except Exception:
        pass
    return None


def private_dir():
    """App-private storage: session DB / keys live here (NOT readable by other apps)."""
    ctx = _android_ctx()
    base = ctx.getFilesDir().getAbsolutePath() if ctx else os.path.expanduser("~/.wa_app")
    os.makedirs(base, exist_ok=True)
    return base


def plugin_dir():
    """Plugin folder. On Android: <ext storage>/Android/data/<pkg>/files/plugins."""
    ctx = _android_ctx()
    if ctx:
        ext = ctx.getExternalFilesDir(None)
        base = ext.getAbsolutePath() if ext else ctx.getFilesDir().getAbsolutePath()
    else:
        base = os.path.expanduser("~/.wa_app")
    path = os.path.join(base, "plugins")
    os.makedirs(path, exist_ok=True)
    return path


def qr_cache_path():
    """Shared path for rendering the latest QR code image."""
    return os.path.join(private_dir(), "wa_qr.png")


def activity_log_path():
    """Persistent log file for all engine and plugin activity."""
    return os.path.join(private_dir(), "wa_activity.log")


def backup_folder():
    """App-external backup directory: Android/data/<pkg>/files/backups (100% permission-safe)."""
    ctx = _android_ctx()
    if ctx:
        ext = ctx.getExternalFilesDir(None)
        base = ext.getAbsolutePath() if ext else ctx.getFilesDir().getAbsolutePath()
    else:
        base = os.path.expanduser("~/.wa_app")
    p = os.path.join(base, "backups")
    os.makedirs(p, exist_ok=True)
    return p


def get_ram_usage_mb() -> int:
    """Return real-time VmRSS memory usage in Megabytes."""
    try:
        with open("/proc/self/status", "r") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 0


def send_android_notification(title: str, text: str, notif_id: int = 1001):
    """Post an Android heads-up alert notification (for Anti-delete, Anti-edit, Reminders)."""
    ctx = _android_ctx()
    if not ctx:
        return
    try:
        from jnius import autoclass
        NotificationBuilder = autoclass("android.app.Notification$Builder")
        NotificationManager = autoclass("android.app.NotificationManager")
        Context = autoclass("android.content.Context")

        nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE)
        channel_id = "org.sanatani.waapp.alerts"
        try:
            NotificationChannel = autoclass("android.app.NotificationChannel")
            channel = NotificationChannel(channel_id, "WA Core Alerts", NotificationManager.IMPORTANCE_HIGH)
            channel.setDescription("Real-time WhatsApp automation alerts")
            channel.enableVibration(True)
            nm.createNotificationChannel(channel)
            builder = NotificationBuilder(ctx, channel_id)
        except Exception:
            builder = NotificationBuilder(ctx)

        builder.setContentTitle(str(title))
        builder.setContentText(str(text))
        try:
            builder.setSmallIcon(ctx.getApplicationInfo().icon)
        except Exception:
            pass
        builder.setAutoCancel(True)
        try:
            BigTextStyle = autoclass("android.app.Notification$BigTextStyle")
            big_text = BigTextStyle()
            big_text.bigText(str(text))
            big_text.setBigContentTitle(str(title))
            builder.setStyle(big_text)
        except Exception:
            pass
        nm.notify(notif_id, builder.build())
    except Exception as e:
        print(f"send_android_notification failed: {e}")


def update_service_notification(title: str, text: str, is_running: bool = True, stats_text: str = ""):
    """Update persistent foreground notification (ID 1) with live stats and [STOP]/[START] action button."""
    ctx = _android_ctx()
    if not ctx:
        return
    try:
        from jnius import autoclass
        NotificationBuilder = autoclass("android.app.Notification$Builder")
        NotificationManager = autoclass("android.app.NotificationManager")
        Context = autoclass("android.content.Context")
        Intent = autoclass("android.content.Intent")
        PendingIntent = autoclass("android.app.PendingIntent")

        nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE)
        # Use IMPORTANCE_DEFAULT on a dedicated channel so action buttons are always visible in Android 13-16
        channel_id = "org.sanatani.waapp.service_channel_v4"
        try:
            NotificationChannel = autoclass("android.app.NotificationChannel")
            channel = NotificationChannel(channel_id, "WA Core Service", NotificationManager.IMPORTANCE_DEFAULT)
            channel.setShowBadge(False)
            channel.enableVibration(False)
            channel.setSound(None, None)
            nm.createNotificationChannel(channel)
            builder = NotificationBuilder(ctx, channel_id)
        except Exception:
            builder = NotificationBuilder(ctx)

        StringClass = autoclass("java.lang.String")
        builder.setContentTitle(StringClass(str(title)))
        builder.setContentText(StringClass(str(text)))
        builder.setOngoing(True)
        builder.setOnlyAlertOnce(True)
        try:
            builder.setSmallIcon(ctx.getApplicationInfo().icon)
        except Exception:
            pass

        # Open UI on notification click
        try:
            pkg = ctx.getPackageName()
            pm = ctx.getPackageManager()
            launch_intent = pm.getLaunchIntentForPackage(pkg)
            if launch_intent:
                p_app = PendingIntent.getActivity(ctx, 0, launch_intent, 0x04000000 | 0x08000000)
                builder.setContentIntent(p_app)
        except Exception:
            pass

        # Controllable Action button: STOP or START via Broadcast
        try:
            action_name = "org.sanatani.waapp.ACTION_STOP" if is_running else "org.sanatani.waapp.ACTION_START"
            btn_label = "■ STOP" if is_running else "▶ START"
            action_intent = Intent(action_name)
            action_intent.setPackage(ctx.getPackageName())
            p_action = PendingIntent.getBroadcast(ctx, 101, action_intent, 0x04000000 | 0x08000000)
            app_icon_res = ctx.getApplicationInfo().icon
            j_label = StringClass(btn_label)

            action_added = False
            # Method 1: Android 6+ through Android 16 using android.graphics.drawable.Icon
            try:
                Icon = autoclass("android.graphics.drawable.Icon")
                NotificationActionBuilder = autoclass("android.app.Notification$Action$Builder")
                icon_obj = Icon.createWithResource(ctx, app_icon_res)
                action = NotificationActionBuilder(icon_obj, j_label, p_action).build()
                builder.addAction(action)
                action_added = True
            except Exception as e_m1:
                print(f"Action method 1 failed: {e_m1}")

            # Method 2: Fallback Action.Builder(int, CharSequence, PendingIntent)
            if not action_added:
                try:
                    NotificationActionBuilder = autoclass("android.app.Notification$Action$Builder")
                    action = NotificationActionBuilder(app_icon_res, j_label, p_action).build()
                    builder.addAction(action)
                    action_added = True
                except Exception as e_m2:
                    print(f"Action method 2 failed: {e_m2}")

            # Method 3: Direct builder.addAction(int, CharSequence, PendingIntent)
            if not action_added:
                try:
                    builder.addAction(app_icon_res, j_label, p_action)
                except Exception as e_m3:
                    print(f"Action method 3 failed: {e_m3}")
        except Exception as e:
            print(f"addAction setup failed: {e}")

        # BigTextStyle multiline stats
        if stats_text:
            try:
                BigTextStyle = autoclass("android.app.Notification$BigTextStyle")
                big = BigTextStyle()
                big.setBigContentTitle(str(title))
                big.bigText(str(stats_text))
                builder.setStyle(big)
            except Exception:
                pass

        notif = builder.build()
        try:
            ctx.startForeground(1, notif)
        except Exception:
            nm.notify(1, notif)
    except Exception as e:
        print(f"update_service_notification failed: {e}")


def stop_android_service():
    """Completely stop foreground status, clear notification, and terminate service process."""
    ctx = _android_ctx()
    if ctx:
        try:
            from jnius import autoclass
            Context = autoclass("android.content.Context")
            nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE)
            nm.cancel(1)
        except Exception:
            pass
        try:
            ctx.stopForeground(True)
            ctx.stopSelf()
        except Exception as e:
            print(f"stop_android_service failed: {e}")


def export_session_zip() -> tuple[bool, str]:
    """Export session.sqlite3 to both app-external backups and public /sdcard/Download."""
    base = private_dir()
    db_file = os.path.join(base, "session.sqlite3")
    if not os.path.exists(db_file):
        return False, "No active session file found to export."

    ts = time.strftime("%Y%m%d_%H%M%S")
    fname = f"WA_Session_{ts}.zip"

    # 1. Primary: Save to app's own external backups folder (100% permission-safe)
    app_backup = os.path.join(backup_folder(), fname)
    try:
        with zipfile.ZipFile(app_backup, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(db_file, "session.sqlite3")
    except Exception as e:
        return False, f"Failed to create backup archive: {e}"

    # 2. Secondary: Attempt copying to public /sdcard/Download
    pub_dir = "/sdcard/Download"
    pub_file = os.path.join(pub_dir, fname)
    saved_to = app_backup
    if os.path.isdir(pub_dir):
        try:
            shutil.copy2(app_backup, pub_file)
            saved_to = f"{pub_file}\n(Also saved in: {app_backup})"
        except Exception:
            pass

    return True, saved_to


def import_session_zip(path: str) -> tuple[bool, str]:
    """Restore session.sqlite3 from a backup zip or sqlite3 file with intelligent path search & header detection."""
    path = path.strip()
    if not path:
        return False, "Please enter a backup file path or filename."

    # Look in multiple candidate locations
    candidates = [
        path,
        os.path.join(backup_folder(), path),
        os.path.join("/sdcard/Download", path),
        os.path.join("/sdcard", path),
        os.path.join(private_dir(), path)
    ]

    actual_path = None
    for cp in candidates:
        if os.path.exists(cp) and os.path.isfile(cp):
            actual_path = cp
            break

    if not actual_path:
        avail = [f for f in os.listdir(backup_folder()) if f.endswith((".zip", ".sqlite3"))] if os.path.isdir(backup_folder()) else []
        avail_str = ", ".join(avail) if avail else "No backups found"
        return False, f"File not found: {path}\nAvailable in backups: {avail_str}"

    file_size = os.path.getsize(actual_path)
    if file_size == 0:
        return False, f"Selected backup file is empty (0 bytes):\n{actual_path}"

    base = private_dir()
    db_dest = os.path.join(base, "session.sqlite3")
    try:
        # Read magic header bytes to accurately identify file format
        with open(actual_path, "rb") as f:
            header = f.read(32)

        is_zip = header.startswith(b"PK\x03\x04") or header.startswith(b"PK\x05\x06") or zipfile.is_zipfile(actual_path)
        is_sqlite = header.startswith(b"SQLite format 3")

        if is_zip:
            with zipfile.ZipFile(actual_path, "r") as zf:
                # Find any sqlite3 database entry inside the zip (supporting subdirectories)
                target_entry = None
                for entry in zf.namelist():
                    if os.path.basename(entry) == "session.sqlite3":
                        target_entry = entry
                        break
                    elif entry.endswith(".sqlite3") or entry.endswith(".db"):
                        target_entry = entry

                if not target_entry:
                    return False, f"Invalid archive: no .sqlite3 database found in zip.\nContents: {zf.namelist()}"

                with zf.open(target_entry) as zf_in, open(db_dest, "wb") as dst_out:
                    shutil.copyfileobj(zf_in, dst_out)

            try:
                os.chmod(db_dest, 0o600)
            except Exception:
                pass
            return True, f"Session restored successfully from ZIP:\n{os.path.basename(actual_path)} ({file_size // 1024} KB)"

        elif is_sqlite:
            shutil.copy2(actual_path, db_dest)
            try:
                os.chmod(db_dest, 0o600)
            except Exception:
                pass
            return True, f"Session database restored successfully from:\n{os.path.basename(actual_path)} ({file_size // 1024} KB)"

        else:
            return False, (
                f"Unrecognized file format: {os.path.basename(actual_path)} ({file_size} bytes)\n"
                f"Header: {header[:16]!r}\n\n"
                f"Please ensure you select a valid WA_Session.zip or session.sqlite3 backup."
            )
    except PermissionError:
        return False, f"Permission Denied accessing:\n{actual_path}\n\nPlease copy your backup file to:\n{backup_folder()}\nor grant All Files Access."
    except Exception as e:
        return False, f"Restore failed: {e}"


def export_activity_log() -> tuple[bool, str]:
    """Copy wa_activity.log to app backup folder and public Download directory."""
    src = activity_log_path()
    if not os.path.exists(src):
        return False, "Activity log file is empty."

    # Save to app backup folder
    app_log = os.path.join(backup_folder(), "WA_Activity_Logs.txt")
    try:
        shutil.copy2(src, app_log)
    except Exception:
        pass

    target_dir = "/sdcard/Download"
    dst = os.path.join(target_dir, "WA_Activity_Logs.txt")
    if os.path.isdir(target_dir):
        try:
            shutil.copy2(src, dst)
            return True, f"{dst}\n(Also in: {app_log})"
        except Exception:
            pass
    return True, app_log
