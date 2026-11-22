"""Shared constants + path helpers (imported by both the UI and the service process)."""
import os

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


def send_android_notification(title: str, text: str, notif_id: int = 1001):
    """Post or update an Android system notification using native Java APIs."""
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
