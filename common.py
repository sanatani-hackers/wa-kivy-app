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
        channel_id = "org.kivy.p4a1"
        try:
            NotificationChannel = autoclass("android.app.NotificationChannel")
            channel = NotificationChannel(channel_id, "WA Core Background Service", NotificationManager.IMPORTANCE_LOW)
            channel.setShowBadge(False)
            channel.enableVibration(False)
            nm.createNotificationChannel(channel)
            builder = NotificationBuilder(ctx, channel_id)
        except Exception:
            builder = NotificationBuilder(ctx)

        builder.setContentTitle(str(title))
        builder.setContentText(str(text))
        builder.setOngoing(True)
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
            app_icon = ctx.getApplicationInfo().icon
            builder.addAction(app_icon, btn_label, p_action)
        except Exception as e:
            print(f"addAction failed: {e}")

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
    """Export session.sqlite3 to public /sdcard/Download or app storage."""
    base = private_dir()
    db_file = os.path.join(base, "session.sqlite3")
    if not os.path.exists(db_file):
        return False, "No active session file found to export."

    target_dir = "/sdcard/Download"
    if not os.path.isdir(target_dir):
        target_dir = base

    ts = time.strftime("%Y%m%d_%H%M%S")
    out_zip = os.path.join(target_dir, f"WA_Session_{ts}.zip")
    try:
        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(db_file, "session.sqlite3")
        return True, out_zip
    except Exception as e:
        return False, str(e)


def import_session_zip(path: str) -> tuple[bool, str]:
    """Restore session.sqlite3 from a backup zip or sqlite3 file."""
    path = path.strip()
    if not os.path.exists(path):
        return False, f"File does not exist: {path}"

    base = private_dir()
    db_dest = os.path.join(base, "session.sqlite3")
    try:
        if path.endswith(".zip"):
            with zipfile.ZipFile(path, "r") as zf:
                if "session.sqlite3" not in zf.namelist():
                    return False, "Invalid archive: 'session.sqlite3' not found in zip."
                zf.extract("session.sqlite3", base)
        else:
            shutil.copy2(path, db_dest)
        return True, "Session restored successfully!"
    except Exception as e:
        return False, str(e)


def export_activity_log() -> tuple[bool, str]:
    """Copy wa_activity.log to public Download directory."""
    src = activity_log_path()
    if not os.path.exists(src):
        return False, "Activity log file is empty."
    target_dir = "/sdcard/Download"
    if not os.path.isdir(target_dir):
        target_dir = private_dir()
    dst = os.path.join(target_dir, "WA_Activity_Logs.txt")
    try:
        shutil.copy2(src, dst)
        return True, dst
    except Exception as e:
        return False, str(e)
