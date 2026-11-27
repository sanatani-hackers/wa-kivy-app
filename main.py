"""WA Core - Minimalistic Modern WhatsApp Automation Engine for Android.
Features:
- Pure Kivy Dark Design System (obsidian & emerald palette, rounded cards)
- Dynamic Tab Navigation: Auto-hides Connect tab once paired
- Settings Screen: Ghost & Privacy Mode, Auto-Wipe, Full Service Kill, Session Export/Import, Logout
- Full In-App Plugin Editor: Edit bundled and custom plugins live with hot-reload
- Monospace Console with Pagination, Log File Persistence and 1-tap Log Export
- Controllable Android Foreground Notification with Live Stats (RAM, Network, Plugins) & Action Buttons
- DejaVuSans font integration for crisp symbol rendering without tofu glyph boxes
"""
import ast
import json
import os
import shutil
import threading
import time
import traceback

from kivy.metrics import dp, sp
from kivy.app import App
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.core.text import DEFAULT_FONT, LabelBase
from kivy.core.window import Window
from kivy.factory import Factory
from kivy.graphics import Color, Line, Rectangle, RoundedRectangle

# Ensure window displays status bar and dynamically shifts up when software keyboard appears
Window.fullscreen = False
Window.borderless = False
Window.softinput_mode = ""
Window.keyboard_anim_args = {'d': 0.2, 't': 'in_out_quart'}
from kivy.lang import Builder
from kivy.properties import BooleanProperty, NumericProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.screenmanager import FadeTransition, Screen, ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import get_color_from_hex, platform
from oscpy.client import OSCClient
from oscpy.server import OSCThreadServer

APP_VERSION = "0.3.0"

# UbuntuMono Nerd Font Icons (verified in custom user font.ttf)
ICON_STOP = "\uf04d"       # nf-fa-stop
ICON_PLAY = "\uf04b"       # nf-fa-play
ICON_PAUSE = "\uf04c"      # nf-fa-pause
ICON_GEAR = "\uf013"       # nf-fa-cog
ICON_REFRESH = "\uf021"    # nf-fa-refresh
ICON_EDIT = "\uf040"       # nf-fa-pencil
ICON_DELETE = "\uf1f8"     # nf-fa-trash
ICON_CLOSE = "\uf00d"      # nf-fa-times
ICON_CHECK = "\uf00c"      # nf-fa-check
ICON_DOT_ON = "\uf111"     # nf-fa-circle
ICON_DOT_OFF = "\uf10c"    # nf-fa-circle_o
ICON_DOWNLOAD = "\uf019"   # nf-fa-download
ICON_UPLOAD = "\uf093"     # nf-fa-upload
ICON_BOLT = "\uf0e7"       # nf-fa-bolt
ICON_POWER = "\uf011"      # nf-fa-power_off
ICON_WARNING = "\uf071"    # nf-fa-warning
ICON_BROOM = "\uf12d"      # nf-fa-eraser
ICON_PREV = "\uf053"       # nf-fa-chevron_left
ICON_NEXT = "\uf054"       # nf-fa-chevron_right

BUILTIN_PLUGINS = {
    "antidelete", "antiviewonce", "autoreply", "callmusic", "echo", "ping", "remind", "stats", "ytcall"
}

PLUGIN_DESCRIPTIONS = {
    "antidelete": "Intercepts and recovers deleted messages & media",
    "antiviewonce": "Bypasses view-once restriction on photos & media",
    "autoreply": "Automated keyword replies and responder triggers",
    "callmusic": "Voice & video call handler, auto-answer, and in-call music DJ",
    "echo": "Echo & broadcast testing tool (!say command)",
    "ping": "Heartbeat and bot response latency checker",
    "remind": "Task scheduler and timed reminder alerts",
    "stats": "Message traffic monitor and session telemetry",
    "ytcall": "True live YouTube PCMStream DJ for voice & video calls",
}

from common import (
    HOST,
    PACKAGE,
    SERVICE,
    SVC_PORT,
    UI_PORT,
    activity_log_path,
    backup_folder,
    export_activity_log,
    export_session_zip,
    get_ram_usage_mb,
    import_session_zip,
    plugin_data_dir,
    plugin_dir,
    plugin_media_dir,
    plugin_packages_dir,
    private_dir,
    qr_cache_path,
    stop_android_service,
)
from core import call_media, pip_hub, plugin_store, updater
from core.client_db import ClientDB, normalize_jid
from core.ghost import GhostConfig


def ask_all_files_permission():
    """Request All Files Access on Android 11+ (API 30+) safely with multiple fallback intents."""
    try:
        from jnius import autoclass
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        activity = PythonActivity.mActivity
        if not activity:
            return
        Build = autoclass("android.os.Build$VERSION")
        if Build.SDK_INT >= 30:
            Environment = autoclass("android.os.Environment")
            if not Environment.isExternalStorageManager():
                Intent = autoclass("android.content.Intent")
                Settings = autoclass("android.provider.Settings")
                Uri = autoclass("android.net.Uri")

                # Strategy 1: General All Files Access settings page (universal across Android 11-16)
                try:
                    intent1 = Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION)
                    intent1.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    activity.startActivity(intent1)
                    return
                except Exception:
                    pass

                # Strategy 2: App-specific All Files settings page
                try:
                    intent2 = Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION)
                    intent2.setData(Uri.parse(f"package:{activity.getPackageName()}"))
                    intent2.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    activity.startActivity(intent2)
                    return
                except Exception:
                    pass

                # Strategy 3: Standard Application Details Settings page
                try:
                    intent3 = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                    intent3.setData(Uri.parse(f"package:{activity.getPackageName()}"))
                    intent3.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    activity.startActivity(intent3)
                except Exception:
                    pass
    except Exception as e:
        print(f"ask_all_files_permission error: {e}")

# Register DejaVuSans for crisp symbol support
_font_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_dejavu_reg = os.path.join(_font_dir, "DejaVuSans.ttf")
_dejavu_bold = os.path.join(_font_dir, "DejaVuSans-Bold.ttf")
if os.path.exists(_dejavu_reg):
    LabelBase.register(
        DEFAULT_FONT,
        fn_regular=_dejavu_reg,
        fn_bold=_dejavu_bold if os.path.exists(_dejavu_bold) else _dejavu_reg,
    )


def log_exception(exc_text):
    try:
        crash_path = os.path.join(private_dir(), "last_crash.log")
        with open(crash_path, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {exc_text}\n")
    except Exception:
        pass


# Unslopped Tactical Design Styles (Strict 4px grid & Obsidian Palette)
KV_STYLES = """
#:import get_color_from_hex kivy.utils.get_color_from_hex

<Root>:
    orientation: 'vertical'
    canvas.before:
        Color:
            rgba: get_color_from_hex('#080C14')
        Rectangle:
            pos: self.pos
            size: self.size

<Card@BoxLayout>:
    orientation: 'vertical'
    padding: dp(16)
    spacing: dp(12)
    size_hint_y: None
    height: self.minimum_height
    canvas.before:
        Color:
            rgba: get_color_from_hex('#0F172A')
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(12)]
        Color:
            rgba: get_color_from_hex('#1E293B')
        Line:
            rounded_rectangle: [self.x, self.y, self.width, self.height, dp(12)]
            width: dp(1)

<DialogCard@BoxLayout>:
    orientation: 'vertical'
    padding: dp(16)
    spacing: dp(12)
    canvas.before:
        Color:
            rgba: get_color_from_hex('#0F172A')
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(14)]
        Color:
            rgba: get_color_from_hex('#1E293B')
        Line:
            rounded_rectangle: [self.x, self.y, self.width, self.height, dp(14)]
            width: dp(1)

<CodeBox@BoxLayout>:
    size_hint_y: None
    height: dp(52)
    padding: [dp(12), dp(8), dp(12), dp(8)]
    spacing: dp(8)
    canvas.before:
        Color:
            rgba: get_color_from_hex('#06090F')
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(8)]
        Color:
            rgba: get_color_from_hex('#10B981')
        Line:
            rounded_rectangle: [self.x, self.y, self.width, self.height, dp(8)]
            width: dp(1)

<TerminalCard@BoxLayout>:
    orientation: 'vertical'
    padding: dp(12)
    canvas.before:
        Color:
            rgba: get_color_from_hex('#06090F')
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(8)]
        Color:
            rgba: get_color_from_hex('#1E293B')
        Line:
            rounded_rectangle: [self.x, self.y, self.width, self.height, dp(8)]
            width: dp(1)

<StatusPill@BoxLayout>:
    size_hint: (1, 1)
    padding: [dp(12), dp(4), dp(12), dp(4)]
    canvas.before:
        Color:
            rgba: get_color_from_hex('#0F172A')
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(14)]
        Color:
            rgba: get_color_from_hex('#1E293B')
        Line:
            rounded_rectangle: [self.x, self.y, self.width, self.height, dp(14)]
            width: dp(1)

<MasterHeaderBtn>:
    background_color: (0, 0, 0, 0)
    background_normal: ''
    background_down: ''
    bold: True
    font_size: '13sp'
    size_hint: (None, None)
    size: (dp(48), dp(32))
    color: get_color_from_hex('#EF4444') if self.is_running else get_color_from_hex('#080C14')
    canvas.before:
        Color:
            rgba: (get_color_from_hex('#2A1215') if self.is_running else get_color_from_hex('#10B981')) if self.state != 'down' else get_color_from_hex('#334155')
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(8)]
        Color:
            rgba: get_color_from_hex('#7F1D1D') if self.is_running else get_color_from_hex('#059669')
        Line:
            rounded_rectangle: [self.x, self.y, self.width, self.height, dp(8)]
            width: dp(1)

<PrimaryButton@Button>:
    background_color: (0, 0, 0, 0)
    background_normal: ''
    background_down: ''
    color: get_color_from_hex('#080C14')
    bold: True
    font_size: '13sp'
    size_hint_y: None
    height: dp(42)
    canvas.before:
        Color:
            rgba: get_color_from_hex('#059669') if self.state == 'down' else get_color_from_hex('#10B981')
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(8)]

<SecondaryButton@Button>:
    background_color: (0, 0, 0, 0)
    background_normal: ''
    background_down: ''
    color: get_color_from_hex('#F8FAFC')
    font_size: '12sp'
    size_hint_y: None
    height: dp(38)
    canvas.before:
        Color:
            rgba: get_color_from_hex('#283548') if self.state == 'down' else get_color_from_hex('#1E293B')
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(8)]
        Color:
            rgba: get_color_from_hex('#334155')
        Line:
            rounded_rectangle: [self.x, self.y, self.width, self.height, dp(8)]
            width: dp(1)

<DangerButton@Button>:
    background_color: (0, 0, 0, 0)
    background_normal: ''
    background_down: ''
    color: get_color_from_hex('#EF4444')
    font_size: '12sp'
    size_hint_y: None
    height: dp(38)
    canvas.before:
        Color:
            rgba: get_color_from_hex('#451A1A') if self.state == 'down' else get_color_from_hex('#2A1215')
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(8)]
        Color:
            rgba: get_color_from_hex('#7F1D1D')
        Line:
            rounded_rectangle: [self.x, self.y, self.width, self.height, dp(8)]
            width: dp(1)

<SwitchBtn>:
    background_color: (0, 0, 0, 0)
    background_normal: ''
    background_down: ''
    color: get_color_from_hex('#080C14') if self.is_active else get_color_from_hex('#94A3B8')
    bold: True
    font_size: '11sp'
    size_hint: (None, None)
    size: (dp(44), dp(30))
    canvas.before:
        Color:
            rgba: (get_color_from_hex('#059669') if self.state == 'down' else get_color_from_hex('#10B981')) if self.is_active else (get_color_from_hex('#283548') if self.state == 'down' else get_color_from_hex('#1E293B'))
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(6)]
        Color:
            rgba: (0, 0, 0, 0) if self.is_active else get_color_from_hex('#334155')
        Line:
            rounded_rectangle: [self.x, self.y, self.width, self.height, dp(6)]
            width: dp(1)

<SymbolEditBtn@Button>:
    background_color: (0, 0, 0, 0)
    background_normal: ''
    background_down: ''
    color: get_color_from_hex('#F8FAFC')
    font_size: '13sp'
    bold: True
    size_hint: (None, None)
    size: (dp(36), dp(30))
    canvas.before:
        Color:
            rgba: get_color_from_hex('#283548') if self.state == 'down' else get_color_from_hex('#1E293B')
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(6)]
        Color:
            rgba: get_color_from_hex('#334155')
        Line:
            rounded_rectangle: [self.x, self.y, self.width, self.height, dp(6)]
            width: dp(1)

<SymbolDeleteBtn@Button>:
    background_color: (0, 0, 0, 0)
    background_normal: ''
    background_down: ''
    color: get_color_from_hex('#EF4444')
    font_size: '13sp'
    bold: True
    size_hint: (None, None)
    size: (dp(36), dp(30))
    canvas.before:
        Color:
            rgba: get_color_from_hex('#451A1A') if self.state == 'down' else get_color_from_hex('#2A1215')
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(6)]
        Color:
            rgba: get_color_from_hex('#7F1D1D')
        Line:
            rounded_rectangle: [self.x, self.y, self.width, self.height, dp(6)]
            width: dp(1)

<ModernInput@TextInput>:
    background_color: (0, 0, 0, 0)
    background_normal: ''
    background_active: ''
    cursor_color: get_color_from_hex('#10B981')
    foreground_color: get_color_from_hex('#F8FAFC')
    hint_text_color: get_color_from_hex('#64748B')
    font_size: '13sp'
    padding: [dp(12), dp(10), dp(12), dp(10)]
    size_hint_y: None
    height: dp(42)
    multiline: False
    canvas.before:
        Color:
            rgba: get_color_from_hex('#06090F')
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(8)]
        Color:
            rgba: get_color_from_hex('#1E293B')
        Line:
            rounded_rectangle: [self.x, self.y, self.width, self.height, dp(8)]
            width: dp(1)

<TabButton>:
    background_color: (0, 0, 0, 0)
    background_normal: ''
    background_down: ''
    font_size: '11sp'
    bold: True
    color: get_color_from_hex('#10B981') if self.active else get_color_from_hex('#94A3B8')
    canvas.before:
        Color:
            rgba: get_color_from_hex('#151E2E') if self.active else (0, 0, 0, 0)
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(6)]
        Color:
            rgba: get_color_from_hex('#10B981') if self.active else (0, 0, 0, 0)
        Line:
            points: [self.x + dp(8), self.y + dp(2), self.right - dp(8), self.y + dp(2)] if self.active else []
            width: dp(2)

<FilterPill>:
    background_color: (0, 0, 0, 0)
    background_normal: ''
    background_down: ''
    font_size: '11sp'
    bold: True
    size_hint_y: None
    height: dp(30)
    color: get_color_from_hex('#080C14') if self.active else get_color_from_hex('#94A3B8')
    canvas.before:
        Color:
            rgba: get_color_from_hex('#10B981') if self.active else (get_color_from_hex('#151E2E') if self.state == 'down' else get_color_from_hex('#0F172A'))
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(8)]
        Color:
            rgba: (0, 0, 0, 0) if self.active else get_color_from_hex('#1E293B')
        Line:
            rounded_rectangle: [self.x, self.y, self.width, self.height, dp(8)]
            width: dp(1)

<AvatarBadge>:
    size_hint: (None, None)
    size: (dp(42), dp(42))
    font_size: '13sp'
    bold: True
    color: get_color_from_hex('#10B981') if self.is_group else get_color_from_hex('#F8FAFC')
    halign: 'center'
    valign: 'middle'
    canvas.before:
        Color:
            rgba: get_color_from_hex('#064E3B') if self.is_group else get_color_from_hex('#1E293B')
        Ellipse:
            pos: self.pos
            size: self.size
        Color:
            rgba: get_color_from_hex('#10B981') if self.is_online else get_color_from_hex('#334155')
        Line:
            ellipse: [self.x, self.y, self.width, self.height]
            width: dp(1.2) if self.is_online else dp(1)

<UnreadBadge>:
    size_hint: (None, None)
    height: dp(20)
    width: dp(24) if self.count < 10 else dp(32)
    font_size: '10sp'
    bold: True
    color: get_color_from_hex('#080C14')
    halign: 'center'
    valign: 'middle'
    canvas.before:
        Color:
            rgba: get_color_from_hex('#10B981') if self.count > 0 else (0, 0, 0, 0)
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(10)]

<ClickableRow>:
    canvas.before:
        Color:
            rgba: get_color_from_hex('#151E2E') if self.state == 'down' else get_color_from_hex('#0F172A')
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(10)]
        Color:
            rgba: get_color_from_hex('#1E293B')
        Line:
            rounded_rectangle: [self.x, self.y, self.width, self.height, dp(10)]
            width: dp(1)

<ClickableBubble>:
    canvas.before:
        Color:
            rgba: (get_color_from_hex('#059669') if self.state == 'down' else get_color_from_hex('#064E3B')) if self.from_me else (get_color_from_hex('#283548') if self.state == 'down' else get_color_from_hex('#1E293B'))
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(12)]
        Color:
            rgba: get_color_from_hex('#059669') if self.from_me else get_color_from_hex('#334155')
        Line:
            rounded_rectangle: [self.x, self.y, self.width, self.height, dp(12)]
            width: dp(1)
"""


class TabButton(Button):
    active = BooleanProperty(False)


class FilterPill(Button):
    active = BooleanProperty(False)


class SwitchBtn(Button):
    is_active = BooleanProperty(True)


class MasterHeaderBtn(Button):
    is_running = BooleanProperty(True)


class AvatarBadge(Label):
    is_group = BooleanProperty(False)
    is_online = BooleanProperty(False)


class UnreadBadge(Label):
    count = NumericProperty(0)


class _LongPressBehavior(ButtonBehavior):
    """ButtonBehavior extension supporting clean single-tap release vs long-press callback."""

    def __init__(self, on_long_press=None, **kw):
        super().__init__(**kw)
        self._lp_cb = on_long_press
        self._lp_event = None
        self._lp_triggered = False

    def _fire_lp(self, _dt):
        self._lp_event = None
        self._lp_triggered = True
        self.state = "normal"
        if self._lp_cb:
            self._lp_cb(self)

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            self._lp_triggered = False
            if self._lp_cb:
                self._lp_event = Clock.schedule_once(self._fire_lp, 0.55)
        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        if self._lp_event is not None:
            if abs(touch.x - touch.ox) > dp(10) or abs(touch.y - touch.oy) > dp(10):
                self._lp_event.cancel()
                self._lp_event = None
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if self._lp_event is not None:
            self._lp_event.cancel()
            self._lp_event = None
        if self._lp_triggered and touch.grab_current is self:
            touch.ungrab(self)
            self.state = "normal"
            return True
        return super().on_touch_up(touch)


class ClickableRow(_LongPressBehavior, BoxLayout):
    pass


class ClickableBubble(_LongPressBehavior, BoxLayout):
    from_me = BooleanProperty(False)


def _short_jid(jid: str) -> str:
    s = normalize_jid(jid)
    return s.split("@", 1)[0] if "@" in s else s


def _initials(name: str, jid: str = "") -> str:
    clean = (name or "").strip()
    if clean and clean != jid and "@" not in clean:
        parts = [p for p in clean.split() if p]
        if len(parts) >= 2:
            return (parts[0][0] + parts[1][0]).upper()
        if parts:
            return parts[0][:2].upper()
    s = _short_jid(jid or clean)
    return (s[-2:] if len(s) >= 2 else s or "?").upper()


def _fmt_ts(ts: int, include_time: bool = False) -> str:
    if not ts:
        return ""
    try:
        t_val = int(ts)
        lt = time.localtime(t_val)
        now = time.localtime()
        if (lt.tm_year, lt.tm_yday) == (now.tm_year, now.tm_yday):
            return time.strftime("%H:%M", lt)
        if lt.tm_year == now.tm_year and (now.tm_yday - lt.tm_yday) == 1:
            return f"Yest {time.strftime('%H:%M', lt)}" if include_time else "Yesterday"
        return time.strftime("%d/%m %H:%M" if include_time else "%d/%m", lt)
    except Exception:
        return ""


def _infer_media_kind(path: str) -> str:
    ext = os.path.splitext(path or "")[1].lower()
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
        return "image"
    if ext in (".mp4", ".mkv", ".mov", ".avi", ".3gp", ".webm"):
        return "video"
    if ext in (".mp3", ".wav", ".ogg", ".opus", ".m4a", ".aac", ".flac"):
        return "audio"
    return "document"


def _open_media_file(path: str):
    """Open a local media file via Android VIEW intent or show location fallback."""
    if not path or not os.path.exists(path):
        InfoDialog("Media Unavailable", "Media file is not downloaded locally yet.").open()
        return
    if platform == "android":
        try:
            from jnius import autoclass
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            Intent = autoclass("android.content.Intent")
            Uri = autoclass("android.net.Uri")
            File = autoclass("java.io.File")
            StrictMode = autoclass("android.os.StrictMode")
            try:
                VmPolicyBuilder = autoclass("android.os.StrictMode$VmPolicy$Builder")
                StrictMode.setVmPolicy(VmPolicyBuilder().build())
            except Exception:
                pass
            kind = _infer_media_kind(path)
            mime_map = {"image": "image/*", "video": "video/*", "audio": "audio/*"}
            mime = mime_map.get(kind, "*/*")
            intent = Intent(Intent.ACTION_VIEW)
            intent.setDataAndType(Uri.fromFile(File(path)), mime)
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_GRANT_READ_URI_PERMISSION)
            act.startActivity(intent)
            return
        except Exception:
            pass
    InfoDialog("Saved Media File", f"Path:\n{path}").open()


def _pick_attachment_file(on_picked, fallback_dir: str = "/sdcard/Download"):
    """Pick any file using Android native chooser or Kivy FileChooser fallback."""
    if platform == "android":
        try:
            from jnius import autoclass
            from android.activity import bind as android_bind, unbind as android_unbind
            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            if activity:
                Intent = autoclass("android.content.Intent")
                intent = Intent(Intent.ACTION_GET_CONTENT)
                intent.setType("*/*")
                intent.addCategory(Intent.CATEGORY_OPENABLE)
                REQ_ATTACH = 8812

                def _on_res(req, res, data_intent):
                    if req == REQ_ATTACH:
                        try:
                            android_unbind(on_activity_result=_on_res)
                        except Exception:
                            pass
                        if res == -1 and data_intent is not None:
                            try:
                                uri = data_intent.getData()
                                if uri:
                                    dest_dir = os.path.join(private_dir(), "outgoing_media")
                                    saved = copy_content_uri_to_folder(activity, uri, dest_dir, fallback_name="attachment.bin")
                                    Clock.schedule_once(lambda dt: on_picked(saved), 0)
                                    return
                            except Exception as e:
                                Clock.schedule_once(lambda dt, err=e: InfoDialog("Attachment Error", str(err)).open(), 0)

                android_bind(on_activity_result=_on_res)
                activity.startActivityForResult(intent, REQ_ATTACH)
                return
        except Exception:
            pass

    from kivy.uix.filechooser import FileChooserListView
    from kivy.uix.popup import Popup
    start_dir = fallback_dir if os.path.isdir(fallback_dir) else os.path.expanduser("~")
    content = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(8))
    fc = FileChooserListView(path=start_dir)
    btn_box = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(8))
    popup = Popup(title="Select Attachment", size_hint=(0.95, 0.9), auto_dismiss=True)

    def _select(*_):
        if fc.selection:
            sel = fc.selection[0]
            popup.dismiss()
            on_picked(sel)

    btn_ok = Factory.PrimaryButton(text="Select")
    btn_ok.bind(on_release=_select)
    btn_cancel = Factory.SecondaryButton(text="Cancel")
    btn_cancel.bind(on_release=popup.dismiss)
    btn_box.add_widget(btn_cancel)
    btn_box.add_widget(btn_ok)
    content.add_widget(fc)
    content.add_widget(btn_box)
    popup.content = content
    popup.open()


Builder.load_string(KV_STYLES)


# Android lifecycle helpers
def start_service():
    if platform != "android":
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        is_free = False
        try:
            sock.bind((HOST, SVC_PORT))
            sock.close()
            is_free = True
        except OSError:
            pass
        if is_free:
            try:
                import subprocess, sys
                svc_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "service", "main.py")
                if os.path.exists(svc_path):
                    subprocess.Popen([sys.executable, svc_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            except Exception as e:
                log_exception(f"Local start_service error: {e}")
        return
    try:
        from jnius import autoclass
        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        autoclass(f"{PACKAGE}.Service{SERVICE}").start(activity, "")
    except Exception as e:
        log_exception(f"start_service error: {e}")


def ask_permissions():
    if platform != "android":
        return
    try:
        from android.permissions import request_permissions
        request_permissions(["android.permission.POST_NOTIFICATIONS", "android.permission.READ_EXTERNAL_STORAGE", "android.permission.WRITE_EXTERNAL_STORAGE"])
    except Exception as e:
        log_exception(f"ask_permissions error: {e}")


def ask_ignore_battery_optimizations():
    if platform != "android":
        return
    try:
        from jnius import autoclass
        Intent, Settings, Uri = (autoclass("android.content.Intent"),
                                 autoclass("android.provider.Settings"),
                                 autoclass("android.net.Uri"))
        act = autoclass("org.kivy.android.PythonActivity").mActivity
        i = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
        i.setData(Uri.parse("package:" + act.getPackageName()))
        act.startActivity(i)
    except Exception as e:
        log_exception(f"battery optimizations error: {e}")


# --------------------------------------------------------------------------- Dialogs
_android_decor_view = None
_android_rect = None

def get_keyboard_height_px():
    """Detect soft keyboard height on Android using window visible display frame."""
    global _android_decor_view, _android_rect
    if platform != "android":
        return 0
    try:
        if _android_decor_view is None:
            from jnius import autoclass
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            act = PythonActivity.mActivity
            if not act:
                return 0
            _android_decor_view = act.getWindow().getDecorView()
            Rect = autoclass("android.graphics.Rect")
            _android_rect = Rect()

        _android_decor_view.getWindowVisibleDisplayFrame(_android_rect)
        total_h = _android_decor_view.getRootView().getHeight()
        diff = total_h - _android_rect.bottom
        if diff > total_h * 0.15:
            return diff
        return 0
    except Exception:
        return 0


class PluginEditorDialog(ModalView):
    """In-app Plugin Studio & Code Editor with real-time telemetry, clean layout, and keyboard-aware resizing."""

    def __init__(self, on_save, **kw):
        super().__init__(size_hint=(0.96, 0.94), auto_dismiss=True, **kw)
        self.on_save = on_save
        self.background_color = (0, 0, 0, 0.85)
        self._last_kh = 0
        self._kb_timer = None

        card = Factory.DialogCard()
        card.padding = dp(10)
        card.spacing = dp(6)

        # 1. Studio Header Toolbar Row 1: [Cancel]  [Filename Input (full width)]  [Save]
        header = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(8))

        btn_cancel = Factory.SecondaryButton(text="Cancel", size_hint=(None, None), size=(dp(60), dp(36)))
        btn_cancel.font_size = "11sp"
        btn_cancel.bind(on_release=lambda *_: self.dismiss())

        self.filename_input = Factory.ModernInput(size_hint_x=1)
        self.filename_input.height = dp(36)
        self.filename_input.font_size = "12sp"
        self.filename_input.hint_text = "custom_bot.py"

        btn_save = Factory.PrimaryButton(text="Save", size_hint=(None, None), size=(dp(64), dp(36)))
        btn_save.font_size = "11sp"
        btn_save.bind(on_release=self._save)

        header.add_widget(btn_cancel)
        header.add_widget(self.filename_input)
        header.add_widget(btn_save)

        # 2. Sub-Toolbar Row 2: [Templates]  ...  [Live Telemetry Status]
        sub_toolbar = BoxLayout(size_hint_y=None, height=dp(28), spacing=dp(8))

        self.btn_snippets = Factory.SecondaryButton(text="Templates", size_hint=(None, None), size=(dp(84), dp(28)))
        self.btn_snippets.font_size = "11sp"
        self.btn_snippets.bind(on_release=self.show_snippets_menu)

        self.lbl_telemetry = Label(
            text="Ln 1 | 0 B | Ready",
            font_size="10sp",
            color=get_color_from_hex("#64748B"),
            halign="right", valign="middle"
        )
        self.lbl_telemetry.bind(size=self.lbl_telemetry.setter('text_size'))

        sub_toolbar.add_widget(self.btn_snippets)
        sub_toolbar.add_widget(self.lbl_telemetry)

        # 3. Code Editor Area (Full Space, Monospace, High-Contrast)
        self.code_input = TextInput(
            background_color=get_color_from_hex('#06090F'),
            foreground_color=get_color_from_hex('#F8FAFC'),
            cursor_color=get_color_from_hex('#10B981'),
            hint_text_color=get_color_from_hex('#475569'),
            font_size="12sp",
            font_name=DEFAULT_FONT,
            hint_text="# Write Python automation code here\nNAME = 'my_plugin'\n\nasync def on_message(ctx, msg):\n    pass",
            multiline=True,
            size_hint=(1, 1),
            padding=[dp(10), dp(10), dp(10), dp(10)]
        )
        self.code_input.bind(text=self._update_telemetry)
        self.code_input.bind(focus=lambda *_: Clock.schedule_once(lambda dt: self._poll_keyboard_height(), 0.05))

        card.add_widget(header)
        card.add_widget(sub_toolbar)
        card.add_widget(self.code_input)
        self.add_widget(card)

    def on_pre_open(self):
        super().on_pre_open()
        if self._kb_timer is None:
            self._kb_timer = Clock.schedule_interval(self._poll_keyboard_height, 0.1)

    def on_open(self):
        super().on_open()
        if self._kb_timer is None:
            self._kb_timer = Clock.schedule_interval(self._poll_keyboard_height, 0.1)

    def on_dismiss(self):
        super().on_dismiss()
        if self._kb_timer is not None:
            self._kb_timer.cancel()
            self._kb_timer = None
        self._apply_keyboard_resize(0)

    def _poll_keyboard_height(self, *_):
        kh = max(get_keyboard_height_px(), Window.keyboard_height)
        if kh != self._last_kh:
            self._last_kh = kh
            self._apply_keyboard_resize(kh)

    def _apply_keyboard_resize(self, kb_height):
        win_h = float(max(1, Window.height))
        if kb_height > 0:
            top_gap = dp(28)
            bottom_gap = kb_height + dp(8)
            avail = max(dp(200), win_h - bottom_gap - top_gap)
            self.size_hint = (0.96, None)
            self.height = avail
            self.pos_hint = {'center_x': 0.5, 'y': bottom_gap / win_h}
        else:
            self.size_hint = (0.96, 0.94)
            self.pos_hint = {'center_x': 0.5, 'center_y': 0.5}

    def _update_telemetry(self, *_):
        txt = self.code_input.text
        lines = len(txt.split("\n"))
        size_bytes = len(txt.encode("utf-8"))
        size_str = f"{size_bytes} B" if size_bytes < 1024 else f"{size_bytes / 1024:.1f} KB"
        self.lbl_telemetry.text = f"Ln {lines} | {size_str} | Ready"

    def show_snippets_menu(self, *_):
        dialog = ModalView(size_hint=(0.88, None), height=dp(336), auto_dismiss=True)
        dcard = Factory.DialogCard()
        dcard.padding = dp(14)
        dcard.spacing = dp(6)
        lbl_stitle = Label(text="Insert Starter Template", font_size="14sp", bold=True,
                           color=get_color_from_hex("#F8FAFC"), size_hint_y=None, height=dp(26))
        dcard.add_widget(lbl_stitle)

        def _pick(kind):
            self.load_template(kind)
            dialog.dismiss()

        btn1 = Factory.SecondaryButton(text="Echo Command (!say)")
        btn1.bind(on_release=lambda *_: _pick("echo"))
        btn2 = Factory.SecondaryButton(text="Keyword Auto-Responder")
        btn2.bind(on_release=lambda *_: _pick("responder"))
        btn3 = Factory.SecondaryButton(text="Web Scraper (requests + bs4)")
        btn3.bind(on_release=lambda *_: _pick("scraper"))
        btn4 = Factory.SecondaryButton(text="Gemini AI Assistant (REST)")
        btn4.bind(on_release=lambda *_: _pick("gemini"))
        btn5 = Factory.SecondaryButton(text="Persistent Anti-Delete (SQLite & ctx.get_db)")
        btn5.bind(on_release=lambda *_: _pick("antidelete"))
        btn6 = Factory.SecondaryButton(text="Periodic Task (ctx.every)")
        btn6.bind(on_release=lambda *_: _pick("remind"))
        btn7 = Factory.SecondaryButton(text="Call Music Responder (on_call)")
        btn7.bind(on_release=lambda *_: _pick("callmusic"))

        for b in (btn1, btn2, btn3, btn4, btn5, btn6, btn7):
            b.font_size = "11sp"
            b.size_hint_y = None
            b.height = dp(32)
            dcard.add_widget(b)
        dialog.add_widget(dcard)
        dialog.open()

    def load_template(self, kind):
        if kind == "echo":
            if not self.filename_input.text:
                self.filename_input.text = "custom_echo.py"
            self.code_input.text = (
                "# Echo command plugin\n"
                "NAME = 'custom_echo'\n\n"
                "async def on_message(ctx, msg):\n"
                "    if msg.text and msg.text.startswith('!say '):\n"
                "        await ctx.reply(msg, msg.text[5:])\n"
            )
        elif kind == "responder":
            if not self.filename_input.text:
                self.filename_input.text = "keyword_reply.py"
            self.code_input.text = (
                "# Keyword automated responder with isolated JSON storage\n"
                "NAME = 'keyword_reply'\n\n"
                "async def setup(ctx):\n"
                "    # Load config or default keywords\n"
                "    cfg = ctx.load_json('config.json', {'help': 'Active 24/7!', 'info': 'WA Core Engine'})\n"
                "    ctx.save_json('config.json', cfg)\n\n"
                "async def on_message(ctx, msg):\n"
                "    text = (msg.text or '').strip().lower()\n"
                "    cfg = ctx.load_json('config.json', {})\n"
                "    for kw, reply in cfg.items():\n"
                "        if kw in text:\n"
                "            await ctx.reply(msg, reply)\n"
                "            break\n"
            )
        elif kind == "scraper":
            if not self.filename_input.text:
                self.filename_input.text = "web_scraper.py"
            self.code_input.text = (
                "# Web Scraper plugin using requests and BeautifulSoup\n"
                "# requires: requests, beautifulsoup4\n"
                "import requests\n"
                "from bs4 import BeautifulSoup\n\n"
                "NAME = 'web_scraper'\n\n"
                "async def on_message(ctx, msg):\n"
                "    text = (msg.text or '').strip()\n"
                "    if text.startswith('!quote'):\n"
                "        try:\n"
                "            res = requests.get('https://dummyjson.com/quotes/random', timeout=6).json()\n"
                "            await ctx.reply(msg, f\"💬 \\\"{res['quote']}\\\"\\n— *{res['author']}*\")\n"
                "        except Exception as e:\n"
                "            await ctx.reply(msg, f\"❌ Error: {e}\")\n"
            )
        elif kind == "gemini":
            if not self.filename_input.text:
                self.filename_input.text = "gemini_bot.py"
            self.code_input.text = (
                "# Gemini AI Assistant Bot via REST API\n"
                "# requires: requests\n"
                "import requests\n\n"
                "NAME = 'gemini_bot'\n"
                "GEMINI_API_KEY = 'YOUR_API_KEY_HERE'\n\n"
                "async def on_message(ctx, msg):\n"
                "    text = (msg.text or '').strip()\n"
                "    if text.startswith('!ai '):\n"
                "        prompt = text[4:].strip()\n"
                "        url = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}'\n"
                "        body = {'contents': [{'parts': [{'text': prompt}]}]}\n"
                "        try:\n"
                "            resp = requests.post(url, json=body, timeout=12).json()\n"
                "            ans = resp['candidates'][0]['content']['parts'][0]['text']\n"
                "            await ctx.reply(msg, f\"🤖 *Gemini:*\\n{ans}\")\n"
                "        except Exception as e:\n"
                "            await ctx.reply(msg, f\"❌ AI Error: {e}\")\n"
            )
        elif kind == "antidelete":
            if not self.filename_input.text:
                self.filename_input.text = "sqlite_antidelete.py"
            self.code_input.text = (
                "# Persistent Anti-Delete using isolated SQLite storage\n"
                "import time\n\n"
                "NAME = 'sqlite_antidelete'\n\n"
                "async def setup(ctx):\n"
                "    db = ctx.get_db()\n"
                "    with db:\n"
                "        db.execute('''\n"
                "            CREATE TABLE IF NOT EXISTS messages (\n"
                "                msg_id TEXT PRIMARY KEY,\n"
                "                sender TEXT,\n"
                "                chat_id TEXT,\n"
                "                content TEXT,\n"
                "                ts INTEGER\n"
                "            );\n"
                "        ''')\n"
                "    ctx.log.info('SQLite Anti-Delete DB ready.')\n\n"
                "async def on_message(ctx, msg):\n"
                "    # Log message to database\n"
                "    pass\n"
            )
        elif kind == "remind":
            if not self.filename_input.text:
                self.filename_input.text = "periodic_task.py"
            self.code_input.text = (
                "# Periodic background task using ctx.every\n"
                "NAME = 'periodic_task'\n\n"
                "async def run_task(ctx):\n"
                "    ctx.log.info('Running periodic health check.')\n\n"
                "async def setup(ctx):\n"
                "    # Schedule to run every 5 minutes (300 seconds)\n"
                "    ctx.every(300, run_task)\n"
            )
        elif kind == "callmusic":
            if not self.filename_input.text:
                self.filename_input.text = "custom_call_dj.py"
            self.code_input.text = (
                "# Custom Call & Video Call Music Handler\n"
                "NAME = 'custom_call_dj'\n\n"
                "async def on_call(ctx, call_ev):\n"
                "    if call_ev.event == 'incoming':\n"
                "        await ctx.answer_call(call_ev.call_id)\n"
                "    elif call_ev.event == 'ready':\n"
                "        await ctx.play_call_audio(call_ev.call_id, '', loop=True)\n\n"
                "async def on_message(ctx, msg):\n"
                "    if (msg.text or '').strip().lower() == '!songcall':\n"
                "        res = await ctx.call(msg.chat_id, video=False)\n"
                "        await ctx.reply(msg, f\"Calling: {res}\")\n"
            )
        self._update_telemetry()

    def _save(self, *_):
        name = self.filename_input.text.strip()
        code = self.code_input.text
        if not name:
            self.lbl_telemetry.text = "Error: Filename required"
            self.lbl_telemetry.color = get_color_from_hex("#EF4444")
            return
        if not name.endswith(".py"):
            name += ".py"

        # AST syntax check to prevent saving broken code
        try:
            ast.parse(code)
        except SyntaxError as e:
            self.lbl_telemetry.text = f"SyntaxError Ln {e.lineno}: {e.msg}"
            self.lbl_telemetry.color = get_color_from_hex("#EF4444")
            return
        except Exception as e:
            self.lbl_telemetry.text = f"Error: {e}"
            self.lbl_telemetry.color = get_color_from_hex("#EF4444")
            return

        self.lbl_telemetry.color = get_color_from_hex("#10B981")
        self.lbl_telemetry.text = "Saved & Verified!"
        self.on_save(self, name, code)


class InfoDialog(ModalView):
    """Clean popup alert for notifications, export paths, and feedback."""
    def __init__(self, title_text, msg_text, **kw):
        super().__init__(size_hint=(0.88, 0.36), auto_dismiss=True, **kw)
        self.background_color = (0, 0, 0, 0.80)
        card = Factory.DialogCard()
        card.padding = dp(16)
        card.spacing = dp(10)

        lbl_t = Label(text=title_text, font_size="15sp", bold=True,
                      color=get_color_from_hex("#F8FAFC"), size_hint_y=None, height=dp(28))
        lbl_m = Label(text=msg_text, font_size="12sp", color=get_color_from_hex("#94A3B8"),
                      halign="center", valign="middle")
        lbl_m.bind(size=lbl_m.setter('text_size'))

        btn_ok = Factory.PrimaryButton(text="OK")
        btn_ok.bind(on_release=lambda *_: self.dismiss())

        card.add_widget(lbl_t)
        card.add_widget(lbl_m)
        card.add_widget(btn_ok)
        self.add_widget(card)


class UpdateDialog(ModalView):
    """Clean OTA Update Dialog showing changelog, version diff, and 1-tap download & install."""
    def __init__(self, info: dict, on_install, **kw):
        super().__init__(size_hint=(0.92, 0.65), auto_dismiss=True, **kw)
        self.info = info
        self.on_install = on_install
        self.background_color = (0, 0, 0, 0.85)

        card = Factory.DialogCard()
        card.padding = dp(14)
        card.spacing = dp(8)

        lbl_t = Label(
            text=f"New Version: {info.get('latest_version')}",
            font_size="15sp", bold=True, color=get_color_from_hex("#10B981"),
            size_hint_y=None, height=dp(28), halign="left"
        )
        lbl_t.bind(size=lbl_t.setter('text_size'))

        lbl_sub = Label(
            text=f"Current: v{info.get('current_version')}  •  Size: {info.get('size_mb')} MB",
            font_size="11sp", color=get_color_from_hex("#94A3B8"),
            size_hint_y=None, height=dp(20), halign="left"
        )
        lbl_sub.bind(size=lbl_sub.setter('text_size'))

        lbl_cl_head = Label(
            text="Release Notes:", font_size="12sp", bold=True,
            color=get_color_from_hex("#F8FAFC"), size_hint_y=None, height=dp(20), halign="left"
        )
        lbl_cl_head.bind(size=lbl_cl_head.setter('text_size'))

        scroll = ScrollView(size_hint=(1, 1))
        lbl_changelog = Label(
            text=str(info.get('changelog') or 'Bug fixes and performance enhancements.'),
            font_size="11sp", color=get_color_from_hex("#CBD5E1"),
            size_hint_y=None, halign="left", valign="top"
        )
        lbl_changelog.bind(width=lambda *_: setattr(lbl_changelog, 'text_size', (lbl_changelog.width, None)))
        lbl_changelog.bind(texture_size=lambda *_: setattr(lbl_changelog, 'height', lbl_changelog.texture_size[1]))
        scroll.add_widget(lbl_changelog)

        self.lbl_progress = Label(
            text="", font_size="11sp", color=get_color_from_hex("#10B981"),
            size_hint_y=None, height=dp(22)
        )

        row_btns = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(8))
        btn_cancel = Factory.SecondaryButton(text="Later", size_hint=(0.35, 1))
        btn_cancel.bind(on_release=lambda *_: self.dismiss())

        self.btn_download = Factory.PrimaryButton(text="Download & Install", size_hint=(0.65, 1))
        self.btn_download.bind(on_release=self._start_download)

        row_btns.add_widget(btn_cancel)
        row_btns.add_widget(self.btn_download)

        card.add_widget(lbl_t)
        card.add_widget(lbl_sub)
        card.add_widget(lbl_cl_head)
        card.add_widget(scroll)
        card.add_widget(self.lbl_progress)
        card.add_widget(row_btns)
        self.add_widget(card)

    def _start_download(self, *_):
        self.btn_download.disabled = True
        self.btn_download.text = "Downloading..."
        self.on_install(self, self.info.get("download_url"))


class PluginStoreDialog(ModalView):
    """Integrated Plugin Marketplace, 3rd-Party Importer, and Python Library Hub."""

    def __init__(self, app_root, **kw):
        super().__init__(size_hint=(0.96, 0.94), auto_dismiss=True, **kw)
        self.root = app_root
        self.background_color = (0, 0, 0, 0.88)
        self.catalog = {"plugins": []}
        self.active_tab = "store"

        card = Factory.DialogCard()
        card.padding = dp(12)
        card.spacing = dp(8)

        # Header: Title & Close Button
        header = BoxLayout(size_hint_y=None, height=dp(34), spacing=dp(8))
        lbl_h = Label(
            text="Plugin Store & Ecosystem", font_size="15sp", bold=True,
            color=get_color_from_hex("#F8FAFC"), halign="left", valign="middle"
        )
        lbl_h.bind(size=lbl_h.setter('text_size'))
        btn_close = Factory.SecondaryButton(text=ICON_CLOSE, size_hint=(None, None), size=(dp(34), dp(34)))
        btn_close.bind(on_release=lambda *_: self.dismiss())
        header.add_widget(lbl_h)
        header.add_widget(btn_close)
        card.add_widget(header)

        # Tab Navigation Row: [Official Store] [3rd-Party Import] [Libraries (Pip)]
        tab_row = BoxLayout(size_hint_y=None, height=dp(34), spacing=dp(6))
        self.btn_tab_store = Factory.PrimaryButton(text="Store Catalog", size_hint=(1, 1))
        self.btn_tab_store.font_size = "11sp"
        self.btn_tab_store.bind(on_release=lambda *_: self.switch_tab("store"))

        self.btn_tab_import = Factory.SecondaryButton(text="3rd-Party Import", size_hint=(1, 1))
        self.btn_tab_import.font_size = "11sp"
        self.btn_tab_import.bind(on_release=lambda *_: self.switch_tab("import"))

        self.btn_tab_libs = Factory.SecondaryButton(text="Libraries (Pip)", size_hint=(1, 1))
        self.btn_tab_libs.font_size = "11sp"
        self.btn_tab_libs.bind(on_release=lambda *_: self.switch_tab("libs"))

        tab_row.add_widget(self.btn_tab_store)
        tab_row.add_widget(self.btn_tab_import)
        tab_row.add_widget(self.btn_tab_libs)
        card.add_widget(tab_row)

        # Dynamic Container for active tab
        self.tab_container = BoxLayout(orientation="vertical", spacing=dp(6), size_hint=(1, 1))
        card.add_widget(self.tab_container)
        self.add_widget(card)

        # Load store catalog asynchronously
        self.switch_tab("store")

    def switch_tab(self, tab_name):
        self.active_tab = tab_name
        self.btn_tab_store.background_color = get_color_from_hex("#10B981") if tab_name == "store" else get_color_from_hex("#1E293B")
        self.btn_tab_import.background_color = get_color_from_hex("#10B981") if tab_name == "import" else get_color_from_hex("#1E293B")
        self.btn_tab_libs.background_color = get_color_from_hex("#10B981") if tab_name == "libs" else get_color_from_hex("#1E293B")

        self.tab_container.clear_widgets()
        if tab_name == "store":
            self._render_store_tab()
        elif tab_name == "import":
            self._render_import_tab()
        elif tab_name == "libs":
            self._render_libs_tab()

    # ---------------------------------------------------- Store Tab
    def _render_store_tab(self):
        # Search & Refresh Row
        top_row = BoxLayout(size_hint_y=None, height=dp(34), spacing=dp(6))
        self.store_search_input = Factory.ModernInput(size_hint_x=1)
        self.store_search_input.font_size = "11sp"
        self.store_search_input.hint_text = "Search store plugins..."
        self.store_search_input.bind(text=self._filter_store_plugins)

        btn_refresh_catalog = Factory.SecondaryButton(text=ICON_REFRESH, size_hint=(None, None), size=(dp(36), dp(34)))
        btn_refresh_catalog.bind(on_release=lambda *_: self._fetch_catalog(force=True))

        top_row.add_widget(self.store_search_input)
        top_row.add_widget(btn_refresh_catalog)
        self.tab_container.add_widget(top_row)

        # Scrollable Store List
        scroll = ScrollView(size_hint=(1, 1))
        self.store_list_box = BoxLayout(orientation="vertical", spacing=dp(8), size_hint_y=None)
        self.store_list_box.bind(minimum_height=self.store_list_box.setter("height"))
        scroll.add_widget(self.store_list_box)
        self.tab_container.add_widget(scroll)

        if not self.catalog.get("plugins"):
            self._fetch_catalog(force=False)
        else:
            self._populate_store_items(self.catalog.get("plugins", []))

    def _fetch_catalog(self, force=False):
        self.store_list_box.clear_widgets()
        lbl_load = Label(
            text="Fetching official plugins from GitHub...",
            font_size="12sp", color=get_color_from_hex("#94A3B8"), size_hint_y=None, height=dp(40)
        )
        self.store_list_box.add_widget(lbl_load)

        def _worker():
            ok, cat, msg = plugin_store.fetch_catalog()
            def _apply(dt):
                self.catalog = cat if ok else {"plugins": []}
                self._populate_store_items(self.catalog.get("plugins", []))
                self.root.on_log(f"Catalog: {msg}")
            Clock.schedule_once(_apply, 0.05)

        threading.Thread(target=_worker, daemon=True).start()

    def _filter_store_plugins(self, *_):
        query = self.store_search_input.text.strip().lower()
        all_plugins = self.catalog.get("plugins", [])
        if not query:
            self._populate_store_items(all_plugins)
        else:
            filtered = [
                p for p in all_plugins
                if query in p.get("name", "").lower()
                or query in p.get("id", "").lower()
                or query in p.get("category", "").lower()
                or query in p.get("description", "").lower()
            ]
            self._populate_store_items(filtered)

    def _populate_store_items(self, plugins):
        self.store_list_box.clear_widgets()
        installed_ids = plugin_store.get_installed_plugin_ids()

        if not plugins:
            empty_card = Factory.Card()
            empty_card.add_widget(Label(
                text="No plugins found matching search.",
                color=get_color_from_hex("#64748B"), size_hint_y=None, height=dp(40)
            ))
            self.store_list_box.add_widget(empty_card)
            return

        for p in plugins:
            p_id = p.get("id", "")
            is_installed = p_id in installed_ids

            card = Factory.Card()
            card.padding = [dp(12), dp(10), dp(12), dp(10)]
            card.spacing = dp(4)

            # Row 1: Title + Version + Category
            r1 = BoxLayout(size_hint_y=None, height=dp(24), spacing=dp(6))
            lbl_name = Label(
                text=f"{p.get('name', p_id)}", font_size="13sp", bold=True,
                color=get_color_from_hex("#F8FAFC"), halign="left", valign="middle"
            )
            lbl_name.bind(size=lbl_name.setter('text_size'))

            lbl_ver = Label(
                text=f"v{p.get('version', '1.0')}", font_size="10sp",
                color=get_color_from_hex("#10B981") if is_installed else get_color_from_hex("#64748B"),
                size_hint=(None, 1), width=dp(45), halign="right", valign="middle"
            )
            lbl_ver.bind(size=lbl_ver.setter('text_size'))

            r1.add_widget(lbl_name)
            r1.add_widget(lbl_ver)
            card.add_widget(r1)

            # Row 2: Description
            lbl_desc = Label(
                text=p.get("description", ""), font_size="11sp",
                color=get_color_from_hex("#94A3B8"), size_hint_y=None, height=dp(32),
                halign="left", valign="top"
            )
            lbl_desc.bind(size=lbl_desc.setter('text_size'))
            card.add_widget(lbl_desc)

            # Row 3: Dependencies + Action Button
            r3 = BoxLayout(size_hint_y=None, height=dp(30), spacing=dp(8))
            reqs = p.get("requires", [])
            req_text = f"Libs: {', '.join(reqs)}" if reqs else "Libs: Built-in"
            lbl_req = Label(
                text=req_text, font_size="10sp", color=get_color_from_hex("#64748B"),
                halign="left", valign="middle"
            )
            lbl_req.bind(size=lbl_req.setter('text_size'))
            r3.add_widget(lbl_req)

            if is_installed:
                btn_stat = Factory.SecondaryButton(text="Installed", size_hint=(None, 1), width=dp(70))
                btn_stat.font_size = "10sp"
                btn_stat.disabled = True

                btn_edit = Factory.PrimaryButton(text="Edit", size_hint=(None, 1), width=dp(50))
                btn_edit.font_size = "10sp"
                btn_edit.bind(on_release=lambda *_, pid=p_id: self._edit_from_store(pid))

                r3.add_widget(btn_stat)
                r3.add_widget(btn_edit)
            else:
                btn_install = Factory.PrimaryButton(text="Install", size_hint=(None, 1), width=dp(74))
                btn_install.font_size = "11sp"
                btn_install.bind(on_release=lambda b, p_entry=p: self._install_plugin(b, p_entry))
                r3.add_widget(btn_install)

            card.add_widget(r3)
            self.store_list_box.add_widget(card)

    def _install_plugin(self, btn, plugin_entry):
        btn.disabled = True
        btn.text = "Installing..."

        def _worker():
            def _log(msg):
                Clock.schedule_once(lambda dt: self.root.on_log(msg), 0)

            ok, res_msg = plugin_store.install_plugin_from_store(plugin_entry, on_log=_log)

            def _done(dt):
                self.root.on_log(res_msg)
                if ok:
                    self.root.send("/reload")
                    btn.text = "Installed"
                    self._populate_store_items(self.catalog.get("plugins", []))
                else:
                    btn.disabled = False
                    btn.text = "Retry"
                    InfoDialog("Installation Failed", res_msg).open()
            Clock.schedule_once(_done, 0.05)

        threading.Thread(target=_worker, daemon=True).start()

    def _edit_from_store(self, plugin_id):
        self.dismiss()
        self.root.screen_plugins.edit_plugin(plugin_id)

    # ---------------------------------------------------- 3rd-Party Import Tab
    def _render_import_tab(self):
        scroll = ScrollView(size_hint=(1, 1))
        content = BoxLayout(orientation="vertical", spacing=dp(10), size_hint_y=None)
        content.bind(minimum_height=content.setter("height"))

        card_url = Factory.Card()
        card_url.padding = [dp(12), dp(10), dp(12), dp(10)]
        card_url.spacing = dp(8)

        lbl_u_title = Label(
            text="Import from GitHub URL", font_size="13sp", bold=True,
            color=get_color_from_hex("#F8FAFC"), size_hint_y=None, height=dp(22), halign="left"
        )
        lbl_u_title.bind(size=lbl_u_title.setter('text_size'))

        lbl_u_sub = Label(
            text="Paste raw link to any community .py plugin on GitHub or Web:",
            font_size="11sp", color=get_color_from_hex("#94A3B8"), size_hint_y=None, height=dp(18), halign="left"
        )
        lbl_u_sub.bind(size=lbl_u_sub.setter('text_size'))

        self.import_url_input = Factory.ModernInput(size_hint_y=None, height=dp(36))
        self.import_url_input.font_size = "11sp"
        self.import_url_input.hint_text = "https://raw.githubusercontent.com/user/repo/main/plugin.py"

        row_inspect = BoxLayout(size_hint_y=None, height=dp(34), spacing=dp(8))
        btn_inspect = Factory.PrimaryButton(text="Inspect URL")
        btn_inspect.bind(on_release=self._inspect_import_url)

        btn_pick_file = Factory.SecondaryButton(text="Load from /sdcard/Download")
        btn_pick_file.bind(on_release=self._inspect_local_file)

        row_inspect.add_widget(btn_inspect)
        row_inspect.add_widget(btn_pick_file)

        card_url.add_widget(lbl_u_title)
        card_url.add_widget(lbl_u_sub)
        card_url.add_widget(self.import_url_input)
        card_url.add_widget(row_inspect)
        content.add_widget(card_url)

        # Inspection Preview Card
        self.card_preview = Factory.Card()
        self.card_preview.padding = [dp(12), dp(10), dp(12), dp(10)]
        self.card_preview.spacing = dp(6)

        self.lbl_prev_title = Label(
            text="Plugin Preview: [Not Inspected]", font_size="13sp", bold=True,
            color=get_color_from_hex("#64748B"), size_hint_y=None, height=dp(22), halign="left"
        )
        self.lbl_prev_title.bind(size=self.lbl_prev_title.setter('text_size'))

        self.lbl_prev_desc = Label(
            text="Enter URL and tap 'Inspect URL' to verify AST syntax and dependencies.",
            font_size="11sp", color=get_color_from_hex("#64748B"), size_hint_y=None, height=dp(30), halign="left"
        )
        self.lbl_prev_desc.bind(size=self.lbl_prev_desc.setter('text_size'))

        self.lbl_prev_deps = Label(
            text="Dependencies: None", font_size="10sp", color=get_color_from_hex("#64748B"),
            size_hint_y=None, height=dp(18), halign="left"
        )
        self.lbl_prev_deps.bind(size=self.lbl_prev_deps.setter('text_size'))

        self.btn_confirm_import = Factory.PrimaryButton(text="Install Inspected Plugin", size_hint_y=None, height=dp(36))
        self.btn_confirm_import.disabled = True
        self.btn_confirm_import.bind(on_release=self._execute_import)

        self.card_preview.add_widget(self.lbl_prev_title)
        self.card_preview.add_widget(self.lbl_prev_desc)
        self.card_preview.add_widget(self.lbl_prev_deps)
        self.card_preview.add_widget(self.btn_confirm_import)
        content.add_widget(self.card_preview)

        scroll.add_widget(content)
        self.tab_container.add_widget(scroll)

        self._inspected_code = ""
        self._inspected_meta = {}

    def _inspect_import_url(self, *_):
        url = self.import_url_input.text.strip()
        if not url:
            InfoDialog("Input Error", "Please paste a GitHub or raw URL first.").open()
            return

        self.lbl_prev_title.text = "Fetching and analyzing..."
        self.lbl_prev_title.color = get_color_from_hex("#F8FAFC")

        def _worker():
            ok, code_or_err, meta = plugin_store.import_from_url(url)
            def _apply(dt):
                if ok:
                    self._inspected_code = code_or_err
                    self._inspected_meta = meta
                    self.lbl_prev_title.text = f"Verified: {meta.get('name', 'Custom Plugin')}"
                    self.lbl_prev_title.color = get_color_from_hex("#10B981")
                    self.lbl_prev_desc.text = meta.get("description", "No description.")
                    self.lbl_prev_desc.color = get_color_from_hex("#CBD5E1")
                    deps = meta.get("requires", [])
                    self.lbl_prev_deps.text = f"Dependencies: {', '.join(deps) if deps else 'None'}"
                    self.btn_confirm_import.disabled = False
                else:
                    self.lbl_prev_title.text = "Verification Failed"
                    self.lbl_prev_title.color = get_color_from_hex("#EF4444")
                    self.lbl_prev_desc.text = code_or_err
                    self.btn_confirm_import.disabled = True
            Clock.schedule_once(_apply, 0.05)

        threading.Thread(target=_worker, daemon=True).start()

    def _inspect_local_file(self, *_):
        candidates = ["/sdcard/Download", backup_folder(), "/sdcard"]
        py_files = []
        for d in candidates:
            if os.path.isdir(d):
                for f in os.listdir(d):
                    if f.endswith(".py") and not f.startswith("_"):
                        py_files.append(os.path.join(d, f))

        if not py_files:
            InfoDialog("No Files", "No .py plugin files found in /sdcard/Download.").open()
            return

        target = py_files[0]
        self.import_url_input.text = target
        ok, code_or_err, meta = plugin_store.import_from_file(target)
        if ok:
            self._inspected_code = code_or_err
            self._inspected_meta = meta
            self.lbl_prev_title.text = f"Local File: {meta.get('name', os.path.basename(target))}"
            self.lbl_prev_title.color = get_color_from_hex("#10B981")
            self.lbl_prev_desc.text = meta.get("description", "Local file.")
            self.lbl_prev_desc.color = get_color_from_hex("#CBD5E1")
            deps = meta.get("requires", [])
            self.lbl_prev_deps.text = f"Dependencies: {', '.join(deps) if deps else 'None'}"
            self.btn_confirm_import.disabled = False
        else:
            InfoDialog("Error Reading File", code_or_err).open()

    def _execute_import(self, *_):
        if not self._inspected_code:
            return
        p_id = self._inspected_meta.get("id", "custom_plugin")
        reqs = self._inspected_meta.get("requires", [])

        self.btn_confirm_import.disabled = True
        self.btn_confirm_import.text = "Installing..."

        def _worker():
            def _log(msg):
                Clock.schedule_once(lambda dt: self.root.on_log(msg), 0)

            ok, res_msg = plugin_store.save_plugin(p_id, self._inspected_code, reqs, on_log=_log)

            def _done(dt):
                self.root.on_log(res_msg)
                if ok:
                    self.root.send("/reload")
                    InfoDialog("Plugin Installed", f"{res_msg}\nPlugin is now active in WA Core.").open()
                    self.switch_tab("store")
                else:
                    self.btn_confirm_import.disabled = False
                    self.btn_confirm_import.text = "Install Inspected Plugin"
                    InfoDialog("Import Failed", res_msg).open()
            Clock.schedule_once(_done, 0.05)

        threading.Thread(target=_worker, daemon=True).start()

    # ---------------------------------------------------- Libraries (Pip Hub) Tab
    def _render_libs_tab(self):
        scroll = ScrollView(size_hint=(1, 1))
        content = BoxLayout(orientation="vertical", spacing=dp(10), size_hint_y=None)
        content.bind(minimum_height=content.setter("height"))

        # Add Library Card
        card_add = Factory.Card()
        card_add.padding = [dp(12), dp(10), dp(12), dp(10)]
        card_add.spacing = dp(8)

        lbl_l_title = Label(
            text="Install Python Libraries (Pip Hub)", font_size="13sp", bold=True,
            color=get_color_from_hex("#F8FAFC"), size_hint_y=None, height=dp(22), halign="left"
        )
        lbl_l_title.bind(size=lbl_l_title.setter('text_size'))

        lbl_l_sub = Label(
            text="Download pure-python wheels directly from PyPI into plugin_packages:",
            font_size="11sp", color=get_color_from_hex("#94A3B8"), size_hint_y=None, height=dp(18), halign="left"
        )
        lbl_l_sub.bind(size=lbl_l_sub.setter('text_size'))

        row_install = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(8))
        self.input_pkg_name = Factory.ModernInput(size_hint_x=1)
        self.input_pkg_name.font_size = "11sp"
        self.input_pkg_name.hint_text = "Package name (e.g. beautifulsoup4, feedparser)"

        btn_do_pip = Factory.PrimaryButton(text="Install", size_hint=(None, 1), width=dp(72))
        btn_do_pip.bind(on_release=self._install_pypi_pkg)

        row_install.add_widget(self.input_pkg_name)
        row_install.add_widget(btn_do_pip)

        btn_local_whl = Factory.SecondaryButton(text="Install Local .whl Archive from /sdcard/Download", size_hint_y=None, height=dp(32))
        btn_local_whl.font_size = "11sp"
        btn_local_whl.bind(on_release=self._install_local_wheel_pkg)

        self.lbl_pip_status = Label(
            text="", font_size="10sp", color=get_color_from_hex("#10B981"),
            size_hint_y=None, height=dp(18), halign="left"
        )
        self.lbl_pip_status.bind(size=self.lbl_pip_status.setter('text_size'))

        card_add.add_widget(lbl_l_title)
        card_add.add_widget(lbl_l_sub)
        card_add.add_widget(row_install)
        card_add.add_widget(btn_local_whl)
        card_add.add_widget(self.lbl_pip_status)
        content.add_widget(card_add)

        # Installed Packages List Card
        card_list = Factory.Card()
        card_list.padding = [dp(12), dp(10), dp(12), dp(10)]
        card_list.spacing = dp(6)

        lbl_inst_title = Label(
            text="Dynamic Packages (Loaded in sys.path)", font_size="13sp", bold=True,
            color=get_color_from_hex("#F8FAFC"), size_hint_y=None, height=dp(22), halign="left"
        )
        lbl_inst_title.bind(size=lbl_inst_title.setter('text_size'))
        card_list.add_widget(lbl_inst_title)

        installed_pkgs = pip_hub.list_installed_packages()
        if not installed_pkgs:
            card_list.add_widget(Label(
                text="No extra dynamic libraries installed yet.\nPre-bundled: requests, protobuf, segno, phonenumbers, filetype.",
                font_size="11sp", color=get_color_from_hex("#64748B"), size_hint_y=None, height=dp(40)
            ))
        else:
            for pkg in installed_pkgs:
                p_row = BoxLayout(size_hint_y=None, height=dp(32), spacing=dp(8))
                lbl_p_meta = Label(
                    text=f"{pkg['name']} v{pkg['version']} ({pkg['size_kb']} KB)",
                    font_size="11sp", color=get_color_from_hex("#E2E8F0"),
                    halign="left", valign="middle"
                )
                lbl_p_meta.bind(size=lbl_p_meta.setter('text_size'))

                btn_uninst = Factory.DangerButton(text="Remove", size_hint=(None, 1), width=dp(64))
                btn_uninst.font_size = "10sp"
                btn_uninst.bind(on_release=lambda *_, p=pkg['name']: self._uninstall_pkg(p))

                p_row.add_widget(lbl_p_meta)
                p_row.add_widget(btn_uninst)
                card_list.add_widget(p_row)

        content.add_widget(card_list)
        scroll.add_widget(content)
        self.tab_container.add_widget(scroll)

    def _install_pypi_pkg(self, *_):
        pkg = self.input_pkg_name.text.strip()
        if not pkg:
            InfoDialog("Input Error", "Please enter a valid package name.").open()
            return

        self.lbl_pip_status.text = f"Contacting PyPI and downloading '{pkg}'..."
        self.lbl_pip_status.color = get_color_from_hex("#38BDF8")

        def _worker():
            ok, msg = pip_hub.install_pypi_package(pkg)
            def _done(dt):
                self.lbl_pip_status.text = msg
                self.lbl_pip_status.color = get_color_from_hex("#10B981") if ok else get_color_from_hex("#EF4444")
                self.root.on_log(msg)
                if ok:
                    self.input_pkg_name.text = ""
                    Clock.schedule_once(lambda dt: self.switch_tab("libs"), 0.8)
                else:
                    InfoDialog("Installation Notice", msg).open()
            Clock.schedule_once(_done, 0.05)

        threading.Thread(target=_worker, daemon=True).start()

    def _install_local_wheel_pkg(self, *_):
        candidates = ["/sdcard/Download", backup_folder(), "/sdcard"]
        wheels = []
        for d in candidates:
            if os.path.isdir(d):
                for f in os.listdir(d):
                    if f.endswith((".whl", ".zip")):
                        wheels.append(os.path.join(d, f))

        if not wheels:
            InfoDialog("No Archives", "No .whl or .zip archives found in /sdcard/Download.").open()
            return

        target = wheels[0]
        ok, msg = pip_hub.install_local_wheel(target)
        self.root.on_log(msg)
        InfoDialog("Local Package", msg).open()
        if ok:
            self.switch_tab("libs")

    def _uninstall_pkg(self, pkg_name):
        ok, msg = pip_hub.uninstall_package(pkg_name)
        self.root.on_log(msg)
        self.switch_tab("libs")


# --------------------------------------------------------------------------- Screens
class ConnectScreen(Screen):
    """Pairing with phone number, viewing live QR code, and session status."""

    def __init__(self, app_root, **kw):
        super().__init__(name="connect", **kw)
        self.root = app_root

        scroll = ScrollView(size_hint=(1, 1))
        content = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12), size_hint_y=None)
        content.bind(minimum_height=content.setter("height"))

        # Card 1: Pairing with Phone Code
        self.card_pair = Factory.Card()
        lbl_pair_title = Label(
            text="Pair with Phone Number", font_size="15sp", bold=True,
            color=get_color_from_hex("#F8FAFC"), size_hint_y=None, height=dp(26), halign="left", valign="middle"
        )
        lbl_pair_title.bind(size=lbl_pair_title.setter('text_size'))

        self.lbl_pair_sub = Label(
            text="Enter number with country code without spaces (e.g. 919876543210):",
            font_size="11sp", color=get_color_from_hex("#94A3B8"),
            size_hint_y=None, height=dp(22), halign="left", valign="middle"
        )
        self.lbl_pair_sub.bind(size=self.lbl_pair_sub.setter('text_size'))

        self.input_phone = Factory.ModernInput()
        self.input_phone.hint_text = "Country code + phone (e.g. 9198xxxxxxxx)"
        self.input_phone.input_filter = "int"

        btn_get_code = Factory.PrimaryButton(text="Request 8-Digit Pairing Code")
        btn_get_code.bind(on_release=self.on_request_code)

        # Code display box
        self.code_box = Factory.CodeBox()
        self.lbl_code = Label(text="------", font_size="20sp", bold=True,
                              color=get_color_from_hex("#10B981"))
        self.btn_copy = Factory.SecondaryButton(text="Copy", size_hint=(None, None), size=(dp(72), dp(36)))
        self.btn_copy.font_size = "11sp"
        self.btn_copy.bind(on_release=self.copy_code)
        self.code_box.add_widget(self.lbl_code)
        self.code_box.add_widget(self.btn_copy)

        self.card_pair.add_widget(lbl_pair_title)
        self.card_pair.add_widget(self.lbl_pair_sub)
        self.card_pair.add_widget(self.input_phone)
        self.card_pair.add_widget(btn_get_code)
        self.card_pair.add_widget(self.code_box)

        # Card 2: QR Code Section
        self.card_qr = Factory.Card()
        lbl_qr_title = Label(
            text="Scan QR Code", font_size="15sp", bold=True,
            color=get_color_from_hex("#F8FAFC"), size_hint_y=None, height=dp(26), halign="left", valign="middle"
        )
        lbl_qr_title.bind(size=lbl_qr_title.setter('text_size'))
        self.lbl_qr_sub = Label(
            text="Scan with WhatsApp > Linked Devices to authenticate.",
            font_size="11sp", color=get_color_from_hex("#94A3B8"),
            size_hint_y=None, height=dp(22), halign="left", valign="middle"
        )
        self.lbl_qr_sub.bind(size=self.lbl_qr_sub.setter('text_size'))
        self.qr_image = Image(size_hint_y=None, height=dp(220), fit_mode="contain")
        self.card_qr.add_widget(lbl_qr_title)
        self.card_qr.add_widget(self.lbl_qr_sub)
        self.card_qr.add_widget(self.qr_image)

        content.add_widget(self.card_pair)
        content.add_widget(self.card_qr)
        scroll.add_widget(content)
        self.add_widget(scroll)

    def on_request_code(self, *_):
        phone = self.input_phone.text.strip().lstrip("+")
        if phone:
            self.lbl_code.text = "Requesting..."
            self.root.send("/pair", phone)

    def set_code(self, code):
        self.lbl_code.text = code
        self.btn_copy.text = "Copy"

    def copy_code(self, *_):
        c = self.lbl_code.text.strip()
        if c and c != "------" and c != "Requesting...":
            Clipboard.put(c)
            self.btn_copy.text = "Copied"
            Clock.schedule_once(lambda dt: setattr(self.btn_copy, "text", "Copy"), 2)

    def set_qr(self, path_or_str):
        if os.path.exists(path_or_str):
            self.qr_image.source = path_or_str
            self.qr_image.reload()
            self.lbl_qr_sub.text = "Scan with WhatsApp > Linked Devices to authenticate:"
        else:
            self.lbl_qr_sub.text = f"QR Payload: {path_or_str[:30]}..."


class PluginsScreen(Screen):
    """Manage installed automation plugins, toggle ON/OFF, and hot-reload."""

    def __init__(self, app_root, **kw):
        super().__init__(name="plugins", **kw)
        self.root = app_root

        layout = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(10))

        # Header with actions
        header_card = Factory.Card()
        row = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(8))
        self.lbl_title = Label(
            text="Plugins (7 active)", font_size="15sp", bold=True,
            color=get_color_from_hex("#F8FAFC"), halign="left", valign="middle"
        )
        self.lbl_title.bind(size=self.lbl_title.setter('text_size'))

        btn_store = Factory.PrimaryButton(text="Store", size_hint=(None, None), size=(dp(66), dp(36)))
        btn_store.font_size = "12sp"
        btn_store.bind(on_release=lambda *_: self.open_store())

        btn_add = Factory.SecondaryButton(text="+ Code", size_hint=(None, None), size=(dp(64), dp(36)))
        btn_add.font_size = "12sp"
        btn_add.bind(on_release=lambda *_: self.open_editor())

        btn_reload = Factory.SecondaryButton(text=ICON_REFRESH, size_hint=(None, None), size=(dp(38), dp(36)))
        btn_reload.font_size = "15sp"
        btn_reload.bold = True
        btn_reload.bind(on_release=lambda *_: self.root.send("/reload"))

        row.add_widget(self.lbl_title)
        row.add_widget(btn_store)
        row.add_widget(btn_add)
        row.add_widget(btn_reload)
        header_card.add_widget(row)
        layout.add_widget(header_card)

        # Plugins list scroll
        scroll = ScrollView(size_hint=(1, 1))
        self.list_box = BoxLayout(orientation="vertical", spacing=dp(8), size_hint_y=None)
        self.list_box.bind(minimum_height=self.list_box.setter("height"))
        scroll.add_widget(self.list_box)
        layout.add_widget(scroll)

        self.add_widget(layout)

    def open_store(self):
        PluginStoreDialog(self.root).open()

    def open_editor(self, filename="", initial_code=""):
        dlg = PluginEditorDialog(self.root.save_plugin)
        if filename:
            dlg.filename_input.text = filename
        if initial_code:
            dlg.code_input.text = initial_code
        dlg.open()

    def edit_plugin(self, name):
        code = self.root.get_plugin_source(name)
        fname = f"{name}.py" if not name.endswith(".py") else name
        self.open_editor(filename=fname, initial_code=code)

    def update_plugins(self, plugin_data_str):
        self.list_box.clear_widgets()
        items = [x.strip() for x in str(plugin_data_str).split(",") if x.strip()]

        plugins = []
        for item in items:
            if ":" in item:
                parts = item.split(":", 1)
                p_name = parts[0].strip()
                p_active = (parts[1].strip() == "1")
            else:
                p_name = item
                p_active = True
            if p_name:
                plugins.append((p_name, p_active))

        active_count = sum(1 for _, is_on in plugins if is_on)
        self.lbl_title.text = f"Plugins ({active_count}/{len(plugins)} active)"

        if not plugins:
            empty_card = Factory.Card()
            empty_card.add_widget(Label(
                text="No plugins loaded. Tap '+ Add' to install one.",
                color=get_color_from_hex("#64748B"), size_hint_y=None, height=dp(40)
            ))
            self.list_box.add_widget(empty_card)
            return

        for name, is_on in plugins:
            p_card = Factory.Card()
            p_card.padding = [dp(14), dp(10), dp(14), dp(10)]
            p_card.spacing = dp(4)

            row = BoxLayout(size_hint_y=None, height=dp(34), spacing=dp(8))

            display_name = f"{name}.py" if not name.endswith(".py") else name
            lbl_name = Label(
                text=f"[b]{display_name}[/b]",
                markup=True,
                font_size="14sp",
                color=get_color_from_hex("#F8FAFC"),
                halign="left", valign="middle"
            )
            lbl_name.bind(size=lbl_name.setter('text_size'))
            row.add_widget(lbl_name)

            # Interactive ON / OFF toggle button
            btn_toggle = SwitchBtn(text="ON" if is_on else "OFF", is_active=is_on)
            btn_toggle.bind(on_release=lambda _, n=name: self.root.send("/toggle_plugin", n))
            row.add_widget(btn_toggle)

            # In-app Code Editor symbol button (pencil icon)
            btn_edit = Factory.SymbolEditBtn(text=ICON_EDIT)
            btn_edit.bind(on_release=lambda _, n=name: self.edit_plugin(n))
            row.add_widget(btn_edit)

            # Delete symbol button (only for custom user plugins; built-in plugins show tag)
            if name not in BUILTIN_PLUGINS:
                btn_del = Factory.SymbolDeleteBtn(text=ICON_DELETE)
                btn_del.bind(on_release=lambda _, n=name: self.root.send("/delete_plugin", n))
                row.add_widget(btn_del)
            else:
                lbl_tag = Label(text="Core", font_size="10sp", bold=True,
                                color=get_color_from_hex("#64748B"), size_hint=(None, None), size=(dp(36), dp(30)))
                row.add_widget(lbl_tag)

            # Quiet purposeful description
            desc = PLUGIN_DESCRIPTIONS.get(name, "Custom automation handler")
            lbl_sub = Label(
                text=desc,
                font_size="11sp",
                color=get_color_from_hex("#94A3B8"),
                size_hint_y=None, height=dp(16),
                halign="left", valign="middle"
            )
            lbl_sub.bind(size=lbl_sub.setter('text_size'))

            p_card.add_widget(row)
            p_card.add_widget(lbl_sub)
            self.list_box.add_widget(p_card)


class PowerScreen(Screen):
    """Power tools hub combining Plugins, Quick Send, and Console inside compact segment tabs."""

    SUBTABS = [("plugins", "Plugins"), ("send", "Quick Send"), ("console", "Console")]

    def __init__(self, app_root, **kw):
        super().__init__(name="power", **kw)
        self.root = app_root
        self.sub_btns = {}

        layout = BoxLayout(orientation="vertical")

        seg_bar = BoxLayout(
            size_hint_y=None, height=dp(42),
            padding=[dp(12), dp(6), dp(12), dp(4)], spacing=dp(6)
        )
        for sid, slabel in self.SUBTABS:
            btn = FilterPill(text=slabel, size_hint=(1, None), height=dp(30))
            btn.bind(on_release=lambda _, s=sid: self.switch_subtab(s))
            self.sub_btns[sid] = btn
            seg_bar.add_widget(btn)

        self.inner_sm = ScreenManager(transition=FadeTransition(duration=0.12))
        for scr in (app_root.screen_plugins, app_root.screen_send, app_root.screen_console):
            if scr.parent:
                scr.parent.remove_widget(scr)
            self.inner_sm.add_widget(scr)

        layout.add_widget(seg_bar)
        layout.add_widget(self.inner_sm)
        self.add_widget(layout)
        self.switch_subtab("plugins")

    def switch_subtab(self, sub_id: str):
        if sub_id not in self.sub_btns:
            sub_id = "plugins"
        self.inner_sm.current = sub_id
        for sid, btn in self.sub_btns.items():
            btn.active = (sid == sub_id)


class ChatsScreen(Screen):
    """Full WhatsApp chat inbox backed directly by local SQLite WAL ClientDB."""

    FILTERS = [("all", "All"), ("unread", "Unread"), ("groups", "Groups"), ("archived", "Archived")]

    def __init__(self, app_root, **kw):
        super().__init__(name="chats", **kw)
        self.root = app_root
        self.filter_mode = "all"
        self.render_limit = 100
        self._refresh_ev = None
        self.filter_btns = {}

        layout = BoxLayout(orientation="vertical", padding=[dp(12), dp(8), dp(12), dp(8)], spacing=dp(8))

        # Top Search + New Chat + Resync row
        top_row = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(6))
        self.search_input = Factory.ModernInput(size_hint_x=1, height=dp(38))
        self.search_input.font_size = "12sp"
        self.search_input.hint_text = "Search chats, names, messages..."
        self.search_input.bind(text=lambda *_: self.schedule_refresh(0.08))

        btn_new = Factory.PrimaryButton(text="New Chat", size_hint=(None, None), size=(dp(78), dp(38)))
        btn_new.font_size = "11sp"
        btn_new.bind(on_release=lambda *_: self.root.switch_tab("contacts"))

        btn_resync = Factory.SecondaryButton(text=ICON_REFRESH, size_hint=(None, None), size=(dp(38), dp(38)))
        btn_resync.font_size = "13sp"
        btn_resync.bind(on_release=self.on_resync_press)

        top_row.add_widget(self.search_input)
        top_row.add_widget(btn_new)
        top_row.add_widget(btn_resync)
        layout.add_widget(top_row)

        # Filter pills row
        filter_row = BoxLayout(size_hint_y=None, height=dp(32), spacing=dp(6))
        for fid, flabel in self.FILTERS:
            btn = FilterPill(text=flabel, size_hint=(1, None), height=dp(30))
            btn.bind(on_release=lambda _, f=fid: self.set_filter(f))
            self.filter_btns[fid] = btn
            filter_row.add_widget(btn)
        layout.add_widget(filter_row)

        # Scrollable chat list
        self.scroll = ScrollView(size_hint=(1, 1))
        self.list_box = BoxLayout(orientation="vertical", spacing=dp(6), size_hint_y=None)
        self.list_box.bind(minimum_height=self.list_box.setter("height"))
        self.scroll.add_widget(self.list_box)
        layout.add_widget(self.scroll)

        self.add_widget(layout)
        self._update_filter_pills()

    def _update_filter_pills(self):
        for fid, btn in self.filter_btns.items():
            btn.active = (fid == self.filter_mode)

    def set_filter(self, mode: str):
        self.filter_mode = mode
        self.render_limit = 100
        self._update_filter_pills()
        self.refresh(immediate=True)

    def on_resync_press(self, *_):
        self.root.send("/resync_contacts")
        self.root.on_log("Requested contacts & chats resync...")

    def schedule_refresh(self, delay: float = 0.12):
        if self._refresh_ev is not None:
            self._refresh_ev.cancel()
        self._refresh_ev = Clock.schedule_once(lambda _dt: self.refresh(immediate=True), delay)

    def refresh(self, *_args, immediate: bool = False):
        if self._refresh_ev is not None:
            self._refresh_ev.cancel()
            self._refresh_ev = None
        try:
            db = ClientDB.get(private_dir())
            q = self.search_input.text.strip()
            rows = db.get_chats(filter_mode=self.filter_mode, search=q, limit=self.render_limit + 1)
        except Exception as e:
            log_exception(f"ChatsScreen.refresh error: {e}")
            rows = []

        has_more = len(rows) > self.render_limit
        chats = rows[: self.render_limit]
        ghost_read = bool(getattr(getattr(self.root, "ghost_config", None), "ghost_read", False))

        self.list_box.clear_widgets()
        if not chats:
            empty_card = Factory.Card()
            empty_card.padding = dp(16)
            msg = (
                "No chats match your search/filter."
                if (q or self.filter_mode != "all")
                else "No chats synced yet. Incoming messages or contact sync will appear here."
            )
            lbl_empty = Label(
                text=msg, font_size="12sp", color=get_color_from_hex("#94A3B8"),
                size_hint_y=None, height=dp(44), halign="center", valign="middle"
            )
            lbl_empty.bind(size=lbl_empty.setter("text_size"))
            empty_card.add_widget(lbl_empty)
            btn_row = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(8))
            btn_contacts = Factory.PrimaryButton(text="Open Contacts", size_hint=(0.5, 1))
            btn_contacts.font_size = "11sp"
            btn_contacts.bind(on_release=lambda *_: self.root.switch_tab("contacts"))
            btn_sync = Factory.SecondaryButton(text="Resync Contacts", size_hint=(0.5, 1))
            btn_sync.font_size = "11sp"
            btn_sync.bind(on_release=self.on_resync_press)
            btn_row.add_widget(btn_contacts)
            btn_row.add_widget(btn_sync)
            empty_card.add_widget(btn_row)
            self.list_box.add_widget(empty_card)
            return

        now_ts = int(time.time())
        for chat in chats:
            self.list_box.add_widget(self._build_chat_row(chat, ghost_read, now_ts))

        if has_more:
            btn_more = Factory.SecondaryButton(text="Load More Chats", size_hint_y=None, height=dp(36))
            btn_more.font_size = "11sp"
            btn_more.bind(on_release=self._load_more)
            self.list_box.add_widget(btn_more)

    def _load_more(self, *_):
        self.render_limit += 100
        self.refresh(immediate=True)

    def _build_chat_row(self, chat: dict, ghost_read: bool, now_ts: int) -> BoxLayout:
        jid = chat.get("jid", "")
        display_name = str(chat.get("display_name") or _short_jid(jid) or jid)
        is_group = bool(chat.get("is_group"))
        is_online = bool(chat.get("is_online"))
        is_typing = bool(chat.get("is_typing"))
        unread = int(chat.get("unread_count") or 0)
        pinned = bool(chat.get("pinned"))
        muted = int(chat.get("muted_until") or 0) > now_ts

        wrapper = BoxLayout(size_hint_y=None, height=dp(68), spacing=dp(4))

        row_btn = ClickableRow(
            on_long_press=lambda _w, ch=chat: self.open_chat_actions(ch),
            orientation="horizontal",
            padding=[dp(10), dp(8), dp(10), dp(8)],
            spacing=dp(10),
        )
        row_btn.bind(on_release=lambda w, j=jid: (None if getattr(w, "_lp_triggered", False) else self.root.open_chat(j)))

        avatar = AvatarBadge(text=_initials(display_name, jid), is_group=is_group, is_online=is_online)
        avatar_wrap = BoxLayout(orientation="vertical", size_hint=(None, 1), width=dp(42))
        avatar_wrap.add_widget(Widget())
        avatar_wrap.add_widget(avatar)
        avatar_wrap.add_widget(Widget())

        mid = BoxLayout(orientation="vertical", spacing=dp(3))

        # Top line: Name + Tags + Timestamp
        top_line = BoxLayout(size_hint_y=None, height=dp(22), spacing=dp(4))
        lbl_name = Label(
            text=display_name, font_size="13sp", bold=True,
            color=get_color_from_hex("#F8FAFC"),
            halign="left", valign="middle", shorten=True, shorten_from="right"
        )
        lbl_name.bind(size=lbl_name.setter("text_size"))
        top_line.add_widget(lbl_name)

        tags = []
        if pinned:
            tags.append("[PIN]")
        if muted:
            tags.append("[MUTE]")
        if ghost_read and unread > 0:
            tags.append("[GHOST]")
        if tags:
            tag_txt = " ".join(tags)
            lbl_tags = Label(
                text=tag_txt, font_size="9sp", bold=True,
                color=get_color_from_hex("#10B981" if pinned or ghost_read else "#64748B"),
                size_hint=(None, 1), width=dp(len(tag_txt) * 6.5 + 4),
                halign="right", valign="middle"
            )
            lbl_tags.bind(size=lbl_tags.setter("text_size"))
            top_line.add_widget(lbl_tags)

        ts_str = _fmt_ts(chat.get("last_ts") or 0)
        lbl_ts = Label(
            text=ts_str, font_size="10sp",
            color=get_color_from_hex("#10B981" if unread > 0 else "#64748B"),
            size_hint=(None, 1), width=dp(54), halign="right", valign="middle"
        )
        lbl_ts.bind(size=lbl_ts.setter("text_size"))
        top_line.add_widget(lbl_ts)

        # Bottom line: Preview + Unread Pill
        bot_line = BoxLayout(size_hint_y=None, height=dp(22), spacing=dp(6))
        if is_typing:
            prev_text = "Typing..."
            prev_color = "#10B981"
        else:
            raw_prev = str(chat.get("last_preview") or "").replace("\r", " ").replace("\n", " ").strip()
            prev_text = raw_prev or _short_jid(jid)
            prev_color = "#CBD5E1" if unread > 0 else "#94A3B8"

        lbl_prev = Label(
            text=prev_text, font_size="11sp",
            color=get_color_from_hex(prev_color),
            halign="left", valign="middle", shorten=True, shorten_from="right"
        )
        lbl_prev.bind(size=lbl_prev.setter("text_size"))
        bot_line.add_widget(lbl_prev)

        if unread > 0:
            badge_txt = "99+" if unread > 99 else str(unread)
            badge = UnreadBadge(text=badge_txt, count=unread)
            bot_line.add_widget(badge)

        mid.add_widget(top_line)
        mid.add_widget(bot_line)

        row_btn.add_widget(avatar_wrap)
        row_btn.add_widget(mid)

        btn_menu = Factory.SecondaryButton(text="...", size_hint=(None, None), size=(dp(32), dp(68)))
        btn_menu.font_size = "12sp"
        btn_menu.bold = True
        btn_menu.bind(on_release=lambda _b, ch=chat: self.open_chat_actions(ch))

        wrapper.add_widget(row_btn)
        wrapper.add_widget(btn_menu)
        return wrapper

    def open_chat_actions(self, chat: dict):
        jid = chat.get("jid", "")
        if not jid:
            return
        name = str(chat.get("display_name") or _short_jid(jid))
        pinned = bool(chat.get("pinned"))
        archived = bool(chat.get("archived"))
        muted = int(chat.get("muted_until") or 0) > int(time.time())
        unread = int(chat.get("unread_count") or 0)

        modal = ModalView(size_hint=(0.86, None), height=dp(360), auto_dismiss=True)
        modal.background_color = (0, 0, 0, 0.82)
        card = Factory.DialogCard()
        card.padding = dp(14)
        card.spacing = dp(8)

        lbl_t = Label(
            text=name, font_size="15sp", bold=True, color=get_color_from_hex("#F8FAFC"),
            size_hint_y=None, height=dp(24), halign="left", valign="middle", shorten=True
        )
        lbl_t.bind(size=lbl_t.setter("text_size"))
        lbl_j = Label(
            text=jid, font_size="10sp", color=get_color_from_hex("#64748B"),
            size_hint_y=None, height=dp(18), halign="left", valign="middle", shorten=True
        )
        lbl_j.bind(size=lbl_j.setter("text_size"))
        card.add_widget(lbl_t)
        card.add_widget(lbl_j)

        def _act(action_name: str):
            self.root.send("/chat_action", jid, action_name)
            modal.dismiss()
            Clock.schedule_once(lambda _dt: self.refresh(immediate=True), 0.25)

        actions = [
            ("Unpin Chat" if pinned else "Pin Chat", "unpin" if pinned else "pin"),
            ("Unarchive Chat" if archived else "Archive Chat", "unarchive" if archived else "archive"),
            ("Unmute Chat" if muted else "Mute Chat", "unmute" if muted else "mute"),
        ]
        for label_txt, act_code in actions:
            b = Factory.SecondaryButton(text=label_txt, size_hint_y=None, height=dp(36))
            b.font_size = "12sp"
            b.bind(on_release=lambda _b, ac=act_code: _act(ac))
            card.add_widget(b)

        if unread > 0:
            b_read = Factory.PrimaryButton(text="Mark as Read", size_hint_y=None, height=dp(36))
            b_read.font_size = "12sp"
            def _do_read(*_):
                self.root.send("/mark_read", jid)
                modal.dismiss()
                Clock.schedule_once(lambda _dt: self.refresh(immediate=True), 0.25)
            b_read.bind(on_release=_do_read)
            card.add_widget(b_read)

        row_calls = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(8))
        b_v = Factory.SecondaryButton(text="Voice Call", size_hint=(0.5, 1))
        b_v.font_size = "11sp"
        b_v.bind(on_release=lambda *_: (self.root.send("/call_place", jid, 0), modal.dismiss()))
        b_vid = Factory.SecondaryButton(text="Video Call", size_hint=(0.5, 1))
        b_vid.font_size = "11sp"
        b_vid.bind(on_release=lambda *_: (self.root.send("/call_place", jid, 1), modal.dismiss()))
        row_calls.add_widget(b_v)
        row_calls.add_widget(b_vid)
        card.add_widget(row_calls)

        b_close = Factory.SecondaryButton(text="Close", size_hint_y=None, height=dp(34))
        b_close.bind(on_release=lambda *_: modal.dismiss())
        card.add_widget(b_close)

        modal.add_widget(card)
        modal.open()


class ChatRoomScreen(Screen):
    """Full-featured Chat Room view with bubbles, quotes, media, reactions, Ghost Read & live YT call."""

    def __init__(self, app_root, **kw):
        super().__init__(name="chatroom", **kw)
        self.root = app_root
        self.current_jid = ""
        self.chat_info = {}
        self.msg_limit = 100
        self.reply_to_msg = None
        self._refresh_ev = None
        self._contact_names = {}

        layout = BoxLayout(orientation="vertical")

        # ------------------------------------------------ Top Header Bar
        header = BoxLayout(
            size_hint_y=None, height=dp(58),
            padding=[dp(8), dp(6), dp(8), dp(6)], spacing=dp(6)
        )
        with header.canvas.before:
            Color(*get_color_from_hex("#0F172A"))
            self._hdr_bg = Rectangle(pos=header.pos, size=header.size)
            Color(*get_color_from_hex("#1E293B"))
            self._hdr_line = Line(points=[header.x, header.y, header.right, header.y], width=dp(1))
        header.bind(
            pos=lambda w, *_: (
                setattr(self._hdr_bg, "pos", w.pos),
                setattr(self._hdr_line, "points", [w.x, w.y, w.right, w.y]),
            ),
            size=lambda w, *_: (
                setattr(self._hdr_bg, "size", w.size),
                setattr(self._hdr_line, "points", [w.x, w.y, w.right, w.y]),
            ),
        )

        btn_back = Factory.SecondaryButton(text=ICON_PREV, size_hint=(None, None), size=(dp(34), dp(38)))
        btn_back.font_size = "13sp"
        btn_back.bind(on_release=lambda *_: self.root.switch_tab("chats"))

        title_box = BoxLayout(orientation="vertical", spacing=dp(1))
        self.lbl_title = Label(
            text="Chat", font_size="14sp", bold=True, color=get_color_from_hex("#F8FAFC"),
            halign="left", valign="middle", shorten=True, shorten_from="right"
        )
        self.lbl_title.bind(size=self.lbl_title.setter("text_size"))

        self.lbl_sub = Label(
            text="", font_size="10sp", color=get_color_from_hex("#94A3B8"),
            halign="left", valign="middle", shorten=True, shorten_from="right"
        )
        self.lbl_sub.bind(size=self.lbl_sub.setter("text_size"))
        title_box.add_widget(self.lbl_title)
        title_box.add_widget(self.lbl_sub)

        btn_voice = Factory.SecondaryButton(text="Voice", size_hint=(None, None), size=(dp(44), dp(34)))
        btn_voice.font_size = "10sp"
        btn_voice.bind(on_release=lambda *_: self.root.send("/call_place", self.current_jid, 0))

        btn_video = Factory.SecondaryButton(text="Video", size_hint=(None, None), size=(dp(44), dp(34)))
        btn_video.font_size = "10sp"
        btn_video.bind(on_release=lambda *_: self.root.send("/call_place", self.current_jid, 1))

        btn_yt = Factory.SecondaryButton(text="YT", size_hint=(None, None), size=(dp(32), dp(34)))
        btn_yt.font_size = "10sp"
        btn_yt.bold = True
        btn_yt.color = get_color_from_hex("#10B981")
        btn_yt.bind(on_release=lambda *_: self.open_yt_modal())

        self.btn_mark_read = Factory.PrimaryButton(text="Read", size_hint=(None, None), size=(dp(44), dp(34)))
        self.btn_mark_read.font_size = "10sp"
        self.btn_mark_read.bind(on_release=self.on_manual_mark_read)

        header.add_widget(btn_back)
        header.add_widget(title_box)
        header.add_widget(btn_voice)
        header.add_widget(btn_video)
        header.add_widget(btn_yt)
        header.add_widget(self.btn_mark_read)
        layout.add_widget(header)

        # ------------------------------------------------ Message List
        self.msg_scroll = ScrollView(size_hint=(1, 1))
        self.msg_box = BoxLayout(
            orientation="vertical", padding=[dp(10), dp(10), dp(10), dp(10)],
            spacing=dp(8), size_hint_y=None
        )
        self.msg_box.bind(minimum_height=self.msg_box.setter("height"))
        self.msg_scroll.add_widget(self.msg_box)
        layout.add_widget(self.msg_scroll)

        # ------------------------------------------------ Collapsible Reply Preview Bar
        self.reply_bar = BoxLayout(
            size_hint_y=None, height=0, opacity=0,
            padding=[dp(10), dp(4), dp(10), dp(4)], spacing=dp(8)
        )
        with self.reply_bar.canvas.before:
            Color(*get_color_from_hex("#0F172A"))
            self._rep_bg = Rectangle(pos=self.reply_bar.pos, size=self.reply_bar.size)
            Color(*get_color_from_hex("#10B981"))
            self._rep_bar = Rectangle(pos=self.reply_bar.pos, size=(dp(3), self.reply_bar.height))
        self.reply_bar.bind(
            pos=lambda w, *_: (
                setattr(self._rep_bg, "pos", w.pos),
                setattr(self._rep_bar, "pos", w.pos),
            ),
            size=lambda w, *_: (
                setattr(self._rep_bg, "size", w.size),
                setattr(self._rep_bar, "size", (dp(3), w.height)),
            ),
        )
        self.lbl_reply_preview = Label(
            text="", font_size="11sp", color=get_color_from_hex("#CBD5E1"),
            halign="left", valign="middle", shorten=True, shorten_from="right"
        )
        self.lbl_reply_preview.bind(size=self.lbl_reply_preview.setter("text_size"))
        btn_cancel_reply = Factory.SecondaryButton(text=ICON_CLOSE, size_hint=(None, None), size=(dp(28), dp(28)))
        btn_cancel_reply.font_size = "11sp"
        btn_cancel_reply.bind(on_release=lambda *_: self.clear_reply())
        self.reply_bar.add_widget(self.lbl_reply_preview)
        self.reply_bar.add_widget(btn_cancel_reply)
        layout.add_widget(self.reply_bar)

        # ------------------------------------------------ Composer Bar
        composer = BoxLayout(
            size_hint_y=None, height=dp(54),
            padding=[dp(8), dp(6), dp(8), dp(6)], spacing=dp(6)
        )
        with composer.canvas.before:
            Color(*get_color_from_hex("#0F172A"))
            self._comp_bg = Rectangle(pos=composer.pos, size=composer.size)
            Color(*get_color_from_hex("#1E293B"))
            self._comp_line = Line(points=[composer.x, composer.top, composer.right, composer.top], width=dp(1))
        composer.bind(
            pos=lambda w, *_: (
                setattr(self._comp_bg, "pos", w.pos),
                setattr(self._comp_line, "points", [w.x, w.top, w.right, w.top]),
            ),
            size=lambda w, *_: (
                setattr(self._comp_bg, "size", w.size),
                setattr(self._comp_line, "points", [w.x, w.top, w.right, w.top]),
            ),
        )

        btn_attach = Factory.SecondaryButton(text="+", size_hint=(None, None), size=(dp(38), dp(40)))
        btn_attach.font_size = "16sp"
        btn_attach.bold = True
        btn_attach.bind(on_release=self.on_attach_press)

        self.input_msg = Factory.ModernInput(size_hint_x=1, height=dp(40))
        self.input_msg.font_size = "13sp"
        self.input_msg.hint_text = "Message..."
        self.input_msg.bind(on_text_validate=self.on_send_press)

        btn_send = Factory.PrimaryButton(text="Send", size_hint=(None, None), size=(dp(62), dp(40)))
        btn_send.font_size = "12sp"
        btn_send.bind(on_release=self.on_send_press)

        composer.add_widget(btn_attach)
        composer.add_widget(self.input_msg)
        composer.add_widget(btn_send)
        layout.add_widget(composer)

        self.add_widget(layout)

    def _ghost_read_enabled(self) -> bool:
        cfg = getattr(self.root, "ghost_config", None)
        if cfg is not None:
            return bool(getattr(cfg, "ghost_read", False))
        return bool(GhostConfig.load(private_dir()).ghost_read)

    def load_chat(self, jid: str):
        self.current_jid = normalize_jid(jid)
        self.msg_limit = 100
        self.clear_reply()
        if not self.current_jid:
            return
        self.root.send("/subscribe_presence", self.current_jid)
        if not self._ghost_read_enabled():
            self.root.send("/mark_read", self.current_jid)
        self.refresh(scroll_to_bottom=True)

    def on_manual_mark_read(self, *_):
        if not self.current_jid:
            return
        self.root.send("/mark_read", self.current_jid)
        Clock.schedule_once(lambda _dt: self.refresh(scroll_to_bottom=False), 0.25)

    def on_chat_updated(self, jid: str = ""):
        if not self.current_jid:
            return
        if jid and normalize_jid(jid) != self.current_jid:
            return
        self.schedule_refresh(0.1)

    def on_presence_update(self, jid: str, payload: dict = None):
        if not self.current_jid:
            return
        if jid and normalize_jid(jid) != self.current_jid:
            return
        self._update_header_meta(override_presence=payload if isinstance(payload, dict) else None)

    def schedule_refresh(self, delay: float = 0.12):
        if self._refresh_ev is not None:
            self._refresh_ev.cancel()
        self._refresh_ev = Clock.schedule_once(lambda _dt: self.refresh(scroll_to_bottom=False), delay)

    def _update_header_meta(self, override_presence: dict = None):
        if not self.current_jid:
            return
        chat = None
        try:
            db = ClientDB.get(private_dir())
            chat = db.get_chat(self.current_jid)
            if chat is None:
                # Contact without chat row yet
                contacts = db.get_contacts(search=_short_jid(self.current_jid), limit=20)
                for ct in contacts:
                    if normalize_jid(ct.get("jid", "")) == self.current_jid:
                        chat = {
                            "jid": self.current_jid,
                            "display_name": ct.get("display_name") or _short_jid(self.current_jid),
                            "is_group": ct.get("is_group", 0),
                            "is_online": ct.get("is_online", 0),
                            "is_typing": ct.get("is_typing", 0),
                            "last_seen_ts": ct.get("last_seen_ts", 0),
                            "unread_count": 0,
                        }
                        break
        except Exception as e:
            log_exception(f"ChatRoom _update_header_meta error: {e}")

        self.chat_info = chat or {"jid": self.current_jid, "display_name": _short_jid(self.current_jid), "unread_count": 0}
        if override_presence:
            for k in ("is_online", "is_typing", "last_seen_ts"):
                if k in override_presence and override_presence[k] is not None:
                    self.chat_info[k] = override_presence[k]

        self.lbl_title.text = str(self.chat_info.get("display_name") or _short_jid(self.current_jid))

        is_typing = bool(self.chat_info.get("is_typing"))
        is_online = bool(self.chat_info.get("is_online"))
        last_seen = int(self.chat_info.get("last_seen_ts") or 0)
        if is_typing:
            self.lbl_sub.text = "Typing..."
            self.lbl_sub.color = get_color_from_hex("#10B981")
        elif is_online:
            self.lbl_sub.text = "Online"
            self.lbl_sub.color = get_color_from_hex("#10B981")
        elif last_seen > 0:
            self.lbl_sub.text = f"Last seen {_fmt_ts(last_seen, include_time=True)}"
            self.lbl_sub.color = get_color_from_hex("#94A3B8")
        else:
            self.lbl_sub.text = _short_jid(self.current_jid)
            self.lbl_sub.color = get_color_from_hex("#64748B")

        unread = int(self.chat_info.get("unread_count") or 0)
        show_read = (unread > 0) or self._ghost_read_enabled()
        if show_read:
            self.btn_mark_read.width = dp(46)
            self.btn_mark_read.opacity = 1
            self.btn_mark_read.disabled = False
        else:
            self.btn_mark_read.width = 0
            self.btn_mark_read.opacity = 0
            self.btn_mark_read.disabled = True

    def _resolve_sender_label(self, sender_jid: str) -> str:
        nj = normalize_jid(sender_jid)
        if nj in self._contact_names:
            return self._contact_names[nj]
        return _short_jid(nj) or "Contact"

    def refresh(self, *_args, scroll_to_bottom: bool = False):
        if self._refresh_ev is not None:
            self._refresh_ev.cancel()
            self._refresh_ev = None
        if not self.current_jid:
            return

        self._update_header_meta()
        try:
            db = ClientDB.get(private_dir())
            # Build fast sender lookup map once per render
            self._contact_names = {
                normalize_jid(ct.get("jid", "")): str(ct.get("display_name") or _short_jid(ct.get("jid", "")))
                for ct in db.get_contacts(limit=500)
            }
            rows = db.get_messages(self.current_jid, limit=self.msg_limit + 1, offset=0)
        except Exception as e:
            log_exception(f"ChatRoomScreen.refresh error: {e}")
            rows = []

        has_older = len(rows) > self.msg_limit
        messages = rows[-self.msg_limit:] if has_older else rows
        is_group = bool(self.chat_info.get("is_group")) or self.current_jid.endswith("@g.us")

        self.msg_box.clear_widgets()
        if has_older:
            btn_older = Factory.SecondaryButton(text="Load Older Messages", size_hint_y=None, height=dp(32))
            btn_older.font_size = "11sp"
            btn_older.bind(on_release=self._load_older)
            self.msg_box.add_widget(btn_older)

        if not messages:
            lbl_none = Label(
                text="No messages stored for this chat yet.",
                font_size="12sp", color=get_color_from_hex("#64748B"),
                size_hint_y=None, height=dp(60), halign="center", valign="middle"
            )
            lbl_none.bind(size=lbl_none.setter("text_size"))
            self.msg_box.add_widget(lbl_none)
            return

        for msg in messages:
            self.msg_box.add_widget(self._build_bubble_row(msg, is_group))

        if scroll_to_bottom:
            Clock.schedule_once(lambda _dt: setattr(self.msg_scroll, "scroll_y", 0), 0.05)

    def _load_older(self, *_):
        self.msg_limit += 100
        self.refresh(scroll_to_bottom=False)

    def _build_bubble_row(self, msg: dict, is_group: bool) -> BoxLayout:
        from_me = bool(msg.get("from_me"))
        outer = BoxLayout(orientation="horizontal", size_hint_y=None)

        bubble = ClickableBubble(
            from_me=from_me,
            orientation="vertical",
            size_hint=(0.82, None),
            padding=[dp(10), dp(8), dp(10), dp(8)],
            spacing=dp(4),
        )
        bubble.bind(
            minimum_height=bubble.setter("height"),
            height=lambda _w, h: setattr(outer, "height", h),
        )
        bubble.bind(on_release=lambda w, m=msg: (None if getattr(w, "_lp_triggered", False) else self.open_bubble_actions(m)))

        def _fit_lbl(lbl: Label):
            lbl.bind(
                width=lambda w, v: setattr(w, "text_size", (max(dp(40), v), None)),
                texture_size=lambda w, ts: setattr(w, "height", max(dp(14), ts[1])),
            )
            return lbl

        # 1. Group sender label
        if is_group and not from_me and msg.get("sender_id"):
            sender_lbl = _fit_lbl(Label(
                text=self._resolve_sender_label(msg["sender_id"]),
                font_size="11sp", bold=True, color=get_color_from_hex("#38BDF8"),
                size_hint_y=None, halign="left", valign="top"
            ))
            bubble.add_widget(sender_lbl)

        # 2. Quoted reply snippet
        quoted_id = str(msg.get("quoted_id") or "")
        if quoted_id:
            q_sender = self._resolve_sender_label(msg.get("quoted_sender") or "") if msg.get("quoted_sender") else "Reply"
            q_body = str(msg.get("quoted_body") or "[Quoted message]").replace("\n", " ").strip()
            if len(q_body) > 90:
                q_body = q_body[:87] + "..."
            q_box = BoxLayout(orientation="vertical", size_hint_y=None, padding=[dp(8), dp(4), dp(6), dp(4)], spacing=dp(1))
            q_box.bind(minimum_height=q_box.setter("height"))
            with q_box.canvas.before:
                Color(*get_color_from_hex("#0F172A"))
                q_bg = RoundedRectangle(pos=q_box.pos, size=q_box.size, radius=[dp(6)])
                Color(*get_color_from_hex("#10B981"))
                q_bar = Rectangle(pos=q_box.pos, size=(dp(3), q_box.height))
            q_box.bind(
                pos=lambda w, *_: (setattr(q_bg, "pos", w.pos), setattr(q_bar, "pos", w.pos)),
                size=lambda w, *_: (setattr(q_bg, "size", w.size), setattr(q_bar, "size", (dp(3), w.height))),
            )
            q_box.add_widget(_fit_lbl(Label(
                text=q_sender, font_size="10sp", bold=True,
                color=get_color_from_hex("#10B981"), size_hint_y=None, halign="left", valign="top"
            )))
            q_box.add_widget(_fit_lbl(Label(
                text=q_body, font_size="11sp",
                color=get_color_from_hex("#CBD5E1"), size_hint_y=None, halign="left", valign="top"
            )))
            bubble.add_widget(q_box)

        # 3. View-Once / Deleted / Edited badges
        if msg.get("is_view_once"):
            bubble.add_widget(_fit_lbl(Label(
                text="[View-Once Saved]", font_size="10sp", bold=True,
                color=get_color_from_hex("#F59E0B"), size_hint_y=None, halign="left", valign="top"
            )))

        if msg.get("is_deleted"):
            rec = str(msg.get("original_body") or msg.get("body") or "[media]").strip()
            bubble.add_widget(_fit_lbl(Label(
                text=f"[Deleted - Recovered: {rec}]", font_size="11sp", bold=True,
                color=get_color_from_hex("#EF4444"), size_hint_y=None, halign="left", valign="top"
            )))
        elif msg.get("is_edited") and msg.get("original_body"):
            orig = str(msg.get("original_body") or "").strip()
            bubble.add_widget(_fit_lbl(Label(
                text=f"[Edited - Orig: {orig}]", font_size="10sp",
                color=get_color_from_hex("#F59E0B"), size_hint_y=None, halign="left", valign="top"
            )))

        # 4. Media Attachment Preview / Action
        mtype = str(msg.get("msg_type") or "text").lower()
        mpath = str(msg.get("media_path") or "")
        tag_map = {
            "image": "[Photo]", "video": "[Video]", "audio": "[Audio]",
            "ptt": "[Voice Note]", "document": "[Document]", "sticker": "[Sticker]"
        }
        if mtype != "text" or mpath:
            tag = tag_map.get(mtype, f"[{mtype.capitalize()}]")
            if mpath and os.path.exists(mpath):
                ext = os.path.splitext(mpath)[1].lower()
                if mtype == "image" and ext in (".jpg", ".jpeg", ".png") and os.path.getsize(mpath) > 0:
                    img_preview = Image(source=mpath, size_hint_y=None, height=dp(140), fit_mode="contain")
                    bubble.add_widget(img_preview)
                btn_m = Factory.SecondaryButton(
                    text=f"{tag} {os.path.basename(mpath)}",
                    size_hint_y=None, height=dp(30)
                )
                btn_m.font_size = "10sp"
                btn_m.bind(on_release=lambda _b, p=mpath: _open_media_file(p))
                bubble.add_widget(btn_m)
            elif mtype != "text":
                btn_dl = Factory.SecondaryButton(
                    text=f"{tag} Tap to Download",
                    size_hint_y=None, height=dp(30)
                )
                btn_dl.font_size = "10sp"
                btn_dl.bind(on_release=lambda _b, mid=msg.get("id", ""): self.root.send("/download_msg_media", self.current_jid, mid))
                bubble.add_widget(btn_dl)

        # 5. Message Body
        body = str(msg.get("body") or "")
        if body and not (msg.get("is_deleted") and body == msg.get("original_body")):
            text_color = "#ECFDF5" if from_me else "#F8FAFC"
            bubble.add_widget(_fit_lbl(Label(
                text=body, font_size="13sp",
                color=get_color_from_hex(text_color),
                size_hint_y=None, halign="left", valign="top"
            )))

        # 6. Reactions summary
        rx_raw = str(msg.get("reactions_json") or "{}")
        try:
            rx_map = json.loads(rx_raw) if rx_raw else {}
            if isinstance(rx_map, dict) and rx_map:
                counts = {}
                for em in rx_map.values():
                    if em:
                        counts[em] = counts.get(em, 0) + 1
                if counts:
                    rx_txt = "  ".join(f"{em} {cnt}" if cnt > 1 else em for em, cnt in counts.items())
                    bubble.add_widget(_fit_lbl(Label(
                        text=rx_txt, font_size="11sp",
                        color=get_color_from_hex("#CBD5E1"),
                        size_hint_y=None, halign="left", valign="top"
                    )))
        except Exception:
            pass

        # 7. Timestamp & Delivery Ticks Footer
        foot = BoxLayout(size_hint_y=None, height=dp(16), spacing=dp(4))
        foot.add_widget(Widget())
        ts_lbl = Label(
            text=_fmt_ts(msg.get("ts") or 0), font_size="9sp",
            color=get_color_from_hex("#A7F3D0" if from_me else "#94A3B8"),
            size_hint=(None, 1), width=dp(48), halign="right", valign="middle"
        )
        ts_lbl.bind(size=ts_lbl.setter("text_size"))
        foot.add_widget(ts_lbl)

        if from_me:
            st = str(msg.get("receipt_status") or "sent").lower()
            if st in ("read", "played"):
                tick_txt, tick_col = "✓✓", "#38BDF8"
            elif st == "delivered":
                tick_txt, tick_col = "✓✓", "#CBD5E1"
            elif st == "pending":
                tick_txt, tick_col = "...", "#94A3B8"
            else:
                tick_txt, tick_col = "✓", "#CBD5E1"
            lbl_tick = Label(
                text=tick_txt, font_size="10sp", bold=True,
                color=get_color_from_hex(tick_col),
                size_hint=(None, 1), width=dp(22), halign="right", valign="middle"
            )
            lbl_tick.bind(size=lbl_tick.setter("text_size"))
            foot.add_widget(lbl_tick)

        bubble.add_widget(foot)

        if from_me:
            outer.add_widget(Widget(size_hint_x=0.18))
            outer.add_widget(bubble)
        else:
            outer.add_widget(bubble)
            outer.add_widget(Widget(size_hint_x=0.18))
        return outer

    def set_reply(self, msg: dict):
        self.reply_to_msg = msg
        sender = "You" if msg.get("from_me") else self._resolve_sender_label(msg.get("sender_id") or "")
        snippet = str(msg.get("body") or msg.get("original_body") or f"[{msg.get('msg_type', 'media')}]")
        snippet = snippet.replace("\n", " ").strip()
        if len(snippet) > 64:
            snippet = snippet[:61] + "..."
        self.lbl_reply_preview.text = f"Replying to {sender}: {snippet}"
        self.reply_bar.height = dp(40)
        self.reply_bar.opacity = 1

    def clear_reply(self):
        self.reply_to_msg = None
        self.lbl_reply_preview.text = ""
        self.reply_bar.height = 0
        self.reply_bar.opacity = 0

    def open_bubble_actions(self, msg: dict):
        msg_id = str(msg.get("id") or "")
        body = str(msg.get("body") or msg.get("original_body") or "")
        mpath = str(msg.get("media_path") or "")
        mtype = str(msg.get("msg_type") or "text").lower()

        has_media_btn = (bool(mpath) and os.path.exists(mpath)) or (mtype != "text")
        modal_h = dp(292 if has_media_btn else 248)
        modal = ModalView(size_hint=(0.82, None), height=modal_h, auto_dismiss=True)
        modal.background_color = (0, 0, 0, 0.80)
        card = Factory.DialogCard()
        card.padding = dp(14)
        card.spacing = dp(8)

        preview = (body or f"[{mtype}]").replace("\n", " ").strip()
        if len(preview) > 56:
            preview = preview[:53] + "..."
        lbl_h = Label(
            text=preview, font_size="12sp", bold=True, color=get_color_from_hex("#F8FAFC"),
            size_hint_y=None, height=dp(24), halign="left", valign="middle", shorten=True
        )
        lbl_h.bind(size=lbl_h.setter("text_size"))
        card.add_widget(lbl_h)

        # Quick React row
        react_row = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(6))
        for em in ("👍", "❤️", "😂"):
            br = Factory.SecondaryButton(text=f"React {em}", size_hint=(1, 1))
            br.font_size = "11sp"
            def _react(_b, e=em):
                self.root.send("/send_reaction", self.current_jid, msg_id, e)
                modal.dismiss()
            br.bind(on_release=_react)
            react_row.add_widget(br)
        card.add_widget(react_row)

        btn_rep = Factory.PrimaryButton(text="Reply", size_hint_y=None, height=dp(36))
        btn_rep.font_size = "12sp"
        btn_rep.bind(on_release=lambda *_: (self.set_reply(msg), modal.dismiss()))
        card.add_widget(btn_rep)

        btn_cpy = Factory.SecondaryButton(text="Copy Text", size_hint_y=None, height=dp(36))
        btn_cpy.font_size = "12sp"
        btn_cpy.bind(on_release=lambda *_: (Clipboard.put(body), modal.dismiss()))
        card.add_widget(btn_cpy)

        if mpath and os.path.exists(mpath):
            btn_open = Factory.SecondaryButton(text="Open Media", size_hint_y=None, height=dp(36))
            btn_open.font_size = "12sp"
            btn_open.bind(on_release=lambda *_: (modal.dismiss(), _open_media_file(mpath)))
            card.add_widget(btn_open)
        elif mtype != "text":
            btn_dl = Factory.SecondaryButton(text="Download Media", size_hint_y=None, height=dp(36))
            btn_dl.font_size = "12sp"
            btn_dl.bind(on_release=lambda *_: (self.root.send("/download_msg_media", self.current_jid, msg_id), modal.dismiss()))
            card.add_widget(btn_dl)

        btn_cls = Factory.SecondaryButton(text="Close", size_hint_y=None, height=dp(32))
        btn_cls.bind(on_release=lambda *_: modal.dismiss())
        card.add_widget(btn_cls)

        modal.add_widget(card)
        modal.open()

    def on_send_press(self, *_):
        if not self.current_jid:
            return
        text = self.input_msg.text.strip()
        if not text:
            return
        quoted_id = str(self.reply_to_msg.get("id") or "") if self.reply_to_msg else ""
        self.root.send("/send_chat", self.current_jid, text, quoted_id)
        self.input_msg.text = ""
        self.clear_reply()
        Clock.schedule_once(lambda _dt: self.refresh(scroll_to_bottom=True), 0.25)

    def on_attach_press(self, *_):
        if not self.current_jid:
            return
        _pick_attachment_file(self._on_attachment_picked)

    def _on_attachment_picked(self, local_path: str):
        if not local_path or not os.path.exists(local_path):
            return
        kinds = ["image", "video", "audio", "document"]
        sel_kind = [_infer_media_kind(local_path)]

        modal = ModalView(size_hint=(0.88, None), height=dp(260), auto_dismiss=True)
        modal.background_color = (0, 0, 0, 0.82)
        card = Factory.DialogCard()
        card.padding = dp(14)
        card.spacing = dp(8)

        lbl_t = Label(
            text=f"Send Attachment: {os.path.basename(local_path)}",
            font_size="13sp", bold=True, color=get_color_from_hex("#F8FAFC"),
            size_hint_y=None, height=dp(24), halign="left", valign="middle", shorten=True
        )
        lbl_t.bind(size=lbl_t.setter("text_size"))

        btn_kind = Factory.SecondaryButton(text=f"Type: {sel_kind[0]}", size_hint_y=None, height=dp(34))
        btn_kind.font_size = "11sp"
        def _cycle_kind(*_):
            idx = (kinds.index(sel_kind[0]) + 1) % len(kinds) if sel_kind[0] in kinds else 0
            sel_kind[0] = kinds[idx]
            btn_kind.text = f"Type: {sel_kind[0]}"
        btn_kind.bind(on_release=_cycle_kind)

        cap_input = Factory.ModernInput(size_hint_y=None, height=dp(40))
        cap_input.font_size = "12sp"
        cap_input.hint_text = "Caption (optional)..."
        cap_input.text = self.input_msg.text.strip()

        row_btns = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(8))
        btn_cancel = Factory.SecondaryButton(text="Cancel", size_hint=(0.4, 1))
        btn_cancel.bind(on_release=lambda *_: modal.dismiss())
        btn_go = Factory.PrimaryButton(text="Send Media", size_hint=(0.6, 1))
        def _send_m(*_):
            caption = cap_input.text.strip()
            self.root.send("/send_media", self.current_jid, sel_kind[0], local_path, caption)
            if caption and caption == self.input_msg.text.strip():
                self.input_msg.text = ""
            modal.dismiss()
            Clock.schedule_once(lambda _dt: self.refresh(scroll_to_bottom=True), 0.3)
        btn_go.bind(on_release=_send_m)
        row_btns.add_widget(btn_cancel)
        row_btns.add_widget(btn_go)

        card.add_widget(lbl_t)
        card.add_widget(btn_kind)
        card.add_widget(cap_input)
        card.add_widget(row_btns)
        modal.add_widget(card)
        modal.open()

    def open_yt_modal(self):
        if not self.current_jid:
            return
        name = str(self.chat_info.get("display_name") or _short_jid(self.current_jid))
        vq = ["480p"]
        bm = ["normal"]

        modal = ModalView(size_hint=(0.90, None), height=dp(265), auto_dismiss=True)
        modal.background_color = (0, 0, 0, 0.82)
        card = Factory.DialogCard()
        card.padding = dp(14)
        card.spacing = dp(8)

        lbl_t = Label(
            text=f"YouTube Live Stream -> {name}",
            font_size="14sp", bold=True, color=get_color_from_hex("#F8FAFC"),
            size_hint_y=None, height=dp(24), halign="left", valign="middle", shorten=True
        )
        lbl_t.bind(size=lbl_t.setter("text_size"))

        inp_q = Factory.ModernInput(size_hint_y=None, height=dp(40))
        inp_q.font_size = "12sp"
        inp_q.hint_text = "YouTube URL or song/video title..."

        row_opts = BoxLayout(size_hint_y=None, height=dp(34), spacing=dp(8))
        btn_vq = Factory.SecondaryButton(text="Quality: 480p", size_hint=(0.5, 1))
        btn_vq.font_size = "11sp"
        btn_vq.bind(on_release=lambda *_: (
            vq.__setitem__(0, "360p" if vq[0] == "480p" else "480p"),
            setattr(btn_vq, "text", f"Quality: {vq[0]}")
        ))
        btn_bm = Factory.SecondaryButton(text="Buffer: normal", size_hint=(0.5, 1))
        btn_bm.font_size = "11sp"
        modes = ["normal", "slow", "fast"]
        btn_bm.bind(on_release=lambda *_: (
            bm.__setitem__(0, modes[(modes.index(bm[0]) + 1) % len(modes)]),
            setattr(btn_bm, "text", f"Buffer: {bm[0]}")
        ))
        row_opts.add_widget(btn_vq)
        row_opts.add_widget(btn_bm)

        row_go = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(8))
        btn_voice = Factory.PrimaryButton(text="Live Voice", size_hint=(0.5, 1))
        btn_voice.font_size = "11sp"
        btn_video = Factory.SecondaryButton(text="Live Video", size_hint=(0.5, 1))
        btn_video.font_size = "11sp"

        def _launch(is_vid: int):
            q_txt = inp_q.text.strip()
            if not q_txt:
                return
            self.root.send("/yt_call_stream", self.current_jid, q_txt, is_vid, vq[0], bm[0])
            modal.dismiss()

        btn_voice.bind(on_release=lambda *_: _launch(0))
        btn_video.bind(on_release=lambda *_: _launch(1))
        row_go.add_widget(btn_voice)
        row_go.add_widget(btn_video)

        btn_cls = Factory.SecondaryButton(text="Cancel", size_hint_y=None, height=dp(32))
        btn_cls.bind(on_release=lambda *_: modal.dismiss())

        card.add_widget(lbl_t)
        card.add_widget(inp_q)
        card.add_widget(row_opts)
        card.add_widget(row_go)
        card.add_widget(btn_cls)
        modal.add_widget(card)
        modal.open()


class ContactsScreen(Screen):
    """Contacts & Groups directory with live number verification and 1-tap Chat/Voice/Video."""

    FILTERS = [(None, "All"), (False, "People"), (True, "Groups")]

    def __init__(self, app_root, **kw):
        super().__init__(name="contacts", **kw)
        self.root = app_root
        self.groups_only = None
        self.render_limit = 120
        self._refresh_ev = None
        self.filter_btns = {}
        self._checked_jid = ""

        layout = BoxLayout(orientation="vertical", padding=[dp(12), dp(8), dp(12), dp(8)], spacing=dp(8))

        # Search + Resync row
        top_row = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(6))
        self.search_input = Factory.ModernInput(size_hint_x=1, height=dp(38))
        self.search_input.font_size = "12sp"
        self.search_input.hint_text = "Search contacts, numbers, groups..."
        self.search_input.bind(text=lambda *_: self.schedule_refresh(0.08))

        btn_resync = Factory.SecondaryButton(text="Resync", size_hint=(None, None), size=(dp(72), dp(38)))
        btn_resync.font_size = "11sp"
        btn_resync.bind(on_release=lambda *_: (self.root.send("/resync_contacts"), self.root.on_log("Resyncing contacts...")))

        top_row.add_widget(self.search_input)
        top_row.add_widget(btn_resync)
        layout.add_widget(top_row)

        # Filter pills: All | People | Groups
        filter_row = BoxLayout(size_hint_y=None, height=dp(32), spacing=dp(6))
        for g_opt, flabel in self.FILTERS:
            btn = FilterPill(text=flabel, size_hint=(1, None), height=dp(30))
            btn.bind(on_release=lambda _, opt=g_opt: self.set_filter(opt))
            self.filter_btns[g_opt] = btn
            filter_row.add_widget(btn)
        layout.add_widget(filter_row)

        # Check Number Bar
        chk_row = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(6))
        self.input_check = Factory.ModernInput(size_hint_x=1, height=dp(38))
        self.input_check.font_size = "12sp"
        self.input_check.hint_text = "Check phone on WhatsApp (e.g. 9198xxxxxxxx)"
        btn_check = Factory.SecondaryButton(text="Check", size_hint=(None, None), size=(dp(68), dp(38)))
        btn_check.font_size = "11sp"
        btn_check.bind(on_release=self.on_check_number)
        chk_row.add_widget(self.input_check)
        chk_row.add_widget(btn_check)
        layout.add_widget(chk_row)

        # Check Number Result Banner (collapsible)
        self.chk_banner = BoxLayout(size_hint_y=None, height=0, opacity=0, padding=[dp(10), dp(4), dp(10), dp(4)], spacing=dp(8))
        with self.chk_banner.canvas.before:
            Color(*get_color_from_hex("#0F172A"))
            self._chk_bg = RoundedRectangle(pos=self.chk_banner.pos, size=self.chk_banner.size, radius=[dp(8)])
        self.chk_banner.bind(
            pos=lambda w, *_: setattr(self._chk_bg, "pos", w.pos),
            size=lambda w, *_: setattr(self._chk_bg, "size", w.size),
        )
        self.lbl_chk_res = Label(
            text="", font_size="11sp", color=get_color_from_hex("#CBD5E1"),
            halign="left", valign="middle", shorten=True
        )
        self.lbl_chk_res.bind(size=self.lbl_chk_res.setter("text_size"))
        self.btn_chk_chat = Factory.PrimaryButton(text="Chat", size_hint=(None, None), size=(dp(56), dp(28)))
        self.btn_chk_chat.font_size = "10sp"
        self.btn_chk_chat.bind(on_release=lambda *_: (self.root.open_chat(self._checked_jid) if self._checked_jid else None))
        btn_chk_hide = Factory.SecondaryButton(text=ICON_CLOSE, size_hint=(None, None), size=(dp(28), dp(28)))
        btn_chk_hide.font_size = "10sp"
        btn_chk_hide.bind(on_release=lambda *_: self._hide_check_banner())
        self.chk_banner.add_widget(self.lbl_chk_res)
        self.chk_banner.add_widget(self.btn_chk_chat)
        self.chk_banner.add_widget(btn_chk_hide)
        layout.add_widget(self.chk_banner)

        # Contacts list
        self.scroll = ScrollView(size_hint=(1, 1))
        self.list_box = BoxLayout(orientation="vertical", spacing=dp(6), size_hint_y=None)
        self.list_box.bind(minimum_height=self.list_box.setter("height"))
        self.scroll.add_widget(self.list_box)
        layout.add_widget(self.scroll)

        self.add_widget(layout)
        self._update_filter_pills()

    def _update_filter_pills(self):
        for opt, btn in self.filter_btns.items():
            btn.active = (opt is self.groups_only)

    def set_filter(self, groups_only):
        self.groups_only = groups_only
        self.render_limit = 120
        self._update_filter_pills()
        self.refresh(immediate=True)

    def _hide_check_banner(self):
        self.chk_banner.height = 0
        self.chk_banner.opacity = 0
        self._checked_jid = ""

    def on_check_number(self, *_):
        phone = self.input_check.text.strip().lstrip("+")
        if not phone:
            return
        self._checked_jid = ""
        self.btn_chk_chat.width = 0
        self.btn_chk_chat.opacity = 0
        self.btn_chk_chat.disabled = True
        self.lbl_chk_res.text = f"Checking +{phone}..."
        self.lbl_chk_res.color = get_color_from_hex("#38BDF8")
        self.chk_banner.height = dp(38)
        self.chk_banner.opacity = 1
        self.root.send("/check_number", phone)

    def on_check_number_result(self, payload_args):
        raw = payload_args[0] if payload_args else ""
        registered = False
        jid = ""
        phone = ""
        err = ""
        try:
            if isinstance(raw, str) and raw.strip().startswith("{"):
                d = json.loads(raw)
                registered = bool(d.get("registered") or d.get("is_in") or d.get("exists"))
                jid = normalize_jid(d.get("jid") or "")
                phone = str(d.get("phone") or d.get("query") or "")
                err = str(d.get("error") or "")
            else:
                txt = str(raw)
                registered = ("true" in txt.lower() or "registered" in txt.lower()) and "not" not in txt.lower()
                if len(payload_args) > 1:
                    jid = normalize_jid(str(payload_args[1]))
                phone = txt
        except Exception:
            err = str(raw)

        self.chk_banner.height = dp(38)
        self.chk_banner.opacity = 1
        if err:
            self._checked_jid = ""
            self.lbl_chk_res.text = f"Check error: {err}"
            self.lbl_chk_res.color = get_color_from_hex("#EF4444")
            self.btn_chk_chat.width = 0
            self.btn_chk_chat.opacity = 0
            self.btn_chk_chat.disabled = True
        elif registered and jid:
            self._checked_jid = jid
            self.lbl_chk_res.text = f"On WhatsApp: {phone or _short_jid(jid)}"
            self.lbl_chk_res.color = get_color_from_hex("#10B981")
            self.btn_chk_chat.width = dp(56)
            self.btn_chk_chat.opacity = 1
            self.btn_chk_chat.disabled = False
        else:
            self._checked_jid = ""
            self.lbl_chk_res.text = f"Not registered on WhatsApp ({phone or 'unknown'})"
            self.lbl_chk_res.color = get_color_from_hex("#F59E0B")
            self.btn_chk_chat.width = 0
            self.btn_chk_chat.opacity = 0
            self.btn_chk_chat.disabled = True
        self.schedule_refresh(0.15)

    def schedule_refresh(self, delay: float = 0.12):
        if self._refresh_ev is not None:
            self._refresh_ev.cancel()
        self._refresh_ev = Clock.schedule_once(lambda _dt: self.refresh(immediate=True), delay)

    def refresh(self, *_args, immediate: bool = False):
        if self._refresh_ev is not None:
            self._refresh_ev.cancel()
            self._refresh_ev = None
        try:
            db = ClientDB.get(private_dir())
            q = self.search_input.text.strip()
            rows = db.get_contacts(search=q, groups_only=self.groups_only, limit=self.render_limit + 1)
        except Exception as e:
            log_exception(f"ContactsScreen.refresh error: {e}")
            rows = []

        has_more = len(rows) > self.render_limit
        contacts = rows[: self.render_limit]

        self.list_box.clear_widgets()
        if not contacts:
            empty = Factory.Card()
            lbl = Label(
                text="No contacts found. Tap 'Resync' or verify a phone number above.",
                font_size="12sp", color=get_color_from_hex("#94A3B8"),
                size_hint_y=None, height=dp(44), halign="center", valign="middle"
            )
            lbl.bind(size=lbl.setter("text_size"))
            empty.add_widget(lbl)
            self.list_box.add_widget(empty)
            return

        for ct in contacts:
            self.list_box.add_widget(self._build_contact_row(ct))

        if has_more:
            btn_more = Factory.SecondaryButton(text="Load More Contacts", size_hint_y=None, height=dp(36))
            btn_more.font_size = "11sp"
            btn_more.bind(on_release=lambda *_: (setattr(self, "render_limit", self.render_limit + 120), self.refresh(immediate=True)))
            self.list_box.add_widget(btn_more)

    def _build_contact_row(self, ct: dict) -> BoxLayout:
        jid = ct.get("jid", "")
        name = str(ct.get("display_name") or _short_jid(jid) or jid)
        is_group = bool(ct.get("is_group")) or jid.endswith("@g.us")
        sub = str(ct.get("about") or ct.get("phone") or _short_jid(jid)).replace("\n", " ").strip()

        card = BoxLayout(size_hint_y=None, height=dp(62), padding=[dp(10), dp(6), dp(10), dp(6)], spacing=dp(8))
        with card.canvas.before:
            Color(*get_color_from_hex("#0F172A"))
            bg = RoundedRectangle(pos=card.pos, size=card.size, radius=[dp(10)])
            Color(*get_color_from_hex("#1E293B"))
            ln = Line(rounded_rectangle=[card.x, card.y, card.width, card.height, dp(10)], width=dp(1))
        card.bind(
            pos=lambda w, *_: (
                setattr(bg, "pos", w.pos),
                setattr(ln, "rounded_rectangle", [w.x, w.y, w.width, w.height, dp(10)]),
            ),
            size=lambda w, *_: (
                setattr(bg, "size", w.size),
                setattr(ln, "rounded_rectangle", [w.x, w.y, w.width, w.height, dp(10)]),
            ),
        )

        av_wrap = BoxLayout(orientation="vertical", size_hint=(None, 1), width=dp(42))
        av_wrap.add_widget(Widget())
        av_wrap.add_widget(AvatarBadge(text=_initials(name, jid), is_group=is_group, is_online=bool(ct.get("is_online"))))
        av_wrap.add_widget(Widget())

        mid = BoxLayout(orientation="vertical", spacing=dp(2))
        lbl_n = Label(
            text=name, font_size="13sp", bold=True, color=get_color_from_hex("#F8FAFC"),
            halign="left", valign="middle", shorten=True, shorten_from="right"
        )
        lbl_n.bind(size=lbl_n.setter("text_size"))
        lbl_s = Label(
            text=sub, font_size="10sp", color=get_color_from_hex("#94A3B8"),
            halign="left", valign="middle", shorten=True, shorten_from="right"
        )
        lbl_s.bind(size=lbl_s.setter("text_size"))
        mid.add_widget(lbl_n)
        mid.add_widget(lbl_s)

        btn_chat = Factory.PrimaryButton(text="Chat", size_hint=(None, None), size=(dp(48), dp(32)))
        btn_chat.font_size = "10sp"
        btn_chat.bind(on_release=lambda _b, j=jid: self.root.open_chat(j))

        btn_voice = Factory.SecondaryButton(text="Voice", size_hint=(None, None), size=(dp(44), dp(32)))
        btn_voice.font_size = "10sp"
        btn_voice.bind(on_release=lambda _b, j=jid: self.root.send("/call_place", j, 0))

        btn_video = Factory.SecondaryButton(text="Video", size_hint=(None, None), size=(dp(44), dp(32)))
        btn_video.font_size = "10sp"
        btn_video.bind(on_release=lambda _b, j=jid: self.root.send("/call_place", j, 1))

        def _vwrap(btn):
            vb = BoxLayout(orientation="vertical", size_hint=(None, 1), width=btn.width)
            vb.add_widget(Widget())
            vb.add_widget(btn)
            vb.add_widget(Widget())
            return vb

        card.add_widget(av_wrap)
        card.add_widget(mid)
        card.add_widget(_vwrap(btn_chat))
        card.add_widget(_vwrap(btn_voice))
        card.add_widget(_vwrap(btn_video))
        return card


class CallsScreen(Screen):
    """Voice & Video Call Controller, In-Call Music/Video DJ Deck, Auto-Policy, and Call History."""

    VOICE_MODES = ["off", "music", "reject", "vip"]
    VIDEO_MODES = ["same", "music", "reject", "off"]
    YT_QUALITIES = ["480p", "360p"]
    YT_BUFFERS = ["normal", "slow", "fast"]

    def __init__(self, app_root, **kw):
        super().__init__(name="calls", **kw)
        self.root = app_root
        self.active_calls = []
        self.selected_call_id = ""
        self.selected_track = ""
        self.loop_enabled = True
        self.tracks = {"audio": [], "video": []}
        self.voice_mode = "off"
        self.video_mode = "same"
        self.yt_vquality = "480p"
        self.yt_buffer_mode = "normal"

        scroll = ScrollView(size_hint=(1, 1))
        content = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(12), size_hint_y=None)
        content.bind(minimum_height=content.setter("height"))

        # ------------------------------------------------ Card 1: Live Call & Music Deck
        card_live = Factory.Card()
        card_live.padding = [dp(14), dp(12), dp(14), dp(12)]
        card_live.spacing = dp(8)

        row_head = BoxLayout(size_hint_y=None, height=dp(26), spacing=dp(8))
        lbl_live_title = Label(
            text="Live Call & Music Deck", font_size="14sp", bold=True,
            color=get_color_from_hex("#F8FAFC"), halign="left", valign="middle"
        )
        lbl_live_title.bind(size=lbl_live_title.setter("text_size"))
        self.lbl_voip_badge = Label(
            text="Checking...", font_size="10sp", bold=True,
            color=get_color_from_hex("#F59E0B"), size_hint_x=None, width=dp(100), halign="right", valign="middle"
        )
        self.lbl_voip_badge.bind(size=self.lbl_voip_badge.setter("text_size"))
        row_head.add_widget(lbl_live_title)
        row_head.add_widget(self.lbl_voip_badge)

        self.lbl_active_call = Label(
            text="No active calls right now.", font_size="12sp",
            color=get_color_from_hex("#94A3B8"), size_hint_y=None, height=dp(36),
            halign="left", valign="middle"
        )
        self.lbl_active_call.bind(size=self.lbl_active_call.setter("text_size"))

        # Call Actions: [Answer] [Decline] [Hangup] [Record]
        row_call_btns = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(6))
        btn_ans = Factory.PrimaryButton(text="Answer", size_hint=(0.25, 1))
        btn_ans.font_size = "11sp"
        btn_ans.bind(on_release=lambda *_: self.root.send("/call_answer", self.selected_call_id))

        btn_dec = Factory.SecondaryButton(text="Decline", size_hint=(0.25, 1))
        btn_dec.font_size = "11sp"
        btn_dec.bind(on_release=lambda *_: self.root.send("/call_decline", self.selected_call_id))

        btn_hup = Factory.DangerButton(text="Hangup", size_hint=(0.25, 1))
        btn_hup.font_size = "11sp"
        btn_hup.bind(on_release=lambda *_: self.root.send("/call_hangup", self.selected_call_id))

        btn_rec = Factory.SecondaryButton(text="Rec Audio", size_hint=(0.25, 1))
        btn_rec.font_size = "11sp"
        btn_rec.bind(on_release=lambda *_: self.root.send("/call_record", self.selected_call_id, 1))

        for b in (btn_ans, btn_dec, btn_hup, btn_rec):
            row_call_btns.add_widget(b)

        # Selected Track Row
        row_trk = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(6))
        self.lbl_track = Label(
            text="Track: hold_chime.wav (Default)", font_size="11sp",
            color=get_color_from_hex("#CBD5E1"), halign="left", valign="middle"
        )
        self.lbl_track.bind(size=self.lbl_track.setter("text_size"))
        btn_pick_trk = Factory.SecondaryButton(text="Library", size_hint=(None, 1), width=dp(66))
        btn_pick_trk.font_size = "11sp"
        btn_pick_trk.bind(on_release=self.open_track_picker)

        btn_add_trk = Factory.SecondaryButton(text="+ File", size_hint=(None, 1), width=dp(62))
        btn_add_trk.font_size = "11sp"
        btn_add_trk.bind(on_release=self.browse_media_file)

        row_trk.add_widget(self.lbl_track)
        row_trk.add_widget(btn_pick_trk)
        row_trk.add_widget(btn_add_trk)

        # Audio / Video Streaming Transport Controls
        row_player = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(6))
        btn_play = Factory.PrimaryButton(text=f"{ICON_PLAY} Play", size_hint=(0.24, 1))
        btn_play.font_size = "11sp"
        btn_play.bind(on_release=lambda *_: self.play_selected_audio())

        btn_pause = Factory.SecondaryButton(text=f"{ICON_PAUSE}", size_hint=(0.15, 1))
        btn_pause.font_size = "11sp"
        btn_pause.bind(on_release=lambda *_: self.root.send("/call_pause", self.selected_call_id))

        btn_resume = Factory.SecondaryButton(text="Resume", size_hint=(0.21, 1))
        btn_resume.font_size = "11sp"
        btn_resume.bind(on_release=lambda *_: self.root.send("/call_resume", self.selected_call_id))

        btn_stop_a = Factory.DangerButton(text=f"{ICON_STOP}", size_hint=(0.15, 1))
        btn_stop_a.font_size = "11sp"
        btn_stop_a.bind(on_release=lambda *_: self.root.send("/call_stop_audio", self.selected_call_id))

        self.btn_loop = SwitchBtn(text="Loop", is_active=True, size_hint=(0.25, 1))
        self.btn_loop.font_size = "11sp"
        self.btn_loop.bind(on_release=self.toggle_loop)

        for b in (btn_play, btn_pause, btn_resume, btn_stop_a, self.btn_loop):
            row_player.add_widget(b)

        card_live.add_widget(row_head)
        card_live.add_widget(self.lbl_active_call)
        card_live.add_widget(row_call_btns)
        card_live.add_widget(row_trk)
        card_live.add_widget(row_player)

        # ------------------------------------------------ Card 2: YouTube Live Stream Deck
        card_yt = Factory.Card()
        card_yt.padding = [dp(14), dp(12), dp(14), dp(12)]
        card_yt.spacing = dp(8)

        lbl_yt_title = Label(
            text="YouTube Live Stream Deck", font_size="14sp", bold=True,
            color=get_color_from_hex("#F8FAFC"), size_hint_y=None, height=dp(24), halign="left"
        )
        lbl_yt_title.bind(size=lbl_yt_title.setter("text_size"))

        self.input_yt_query = Factory.ModernInput()
        self.input_yt_query.hint_text = "YouTube URL or song/video search..."

        self.input_yt_target = Factory.ModernInput()
        self.input_yt_target.hint_text = "Target number (blank = active call)"

        row_yt_opts = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(8))
        self.btn_yt_vquality = Factory.SecondaryButton(text="Video Quality: 480p", size_hint=(0.5, 1))
        self.btn_yt_vquality.font_size = "11sp"
        self.btn_yt_vquality.bind(on_release=self.cycle_yt_vquality)

        self.btn_yt_buffer = Factory.SecondaryButton(text="Buffer: normal", size_hint=(0.5, 1))
        self.btn_yt_buffer.font_size = "11sp"
        self.btn_yt_buffer.bind(on_release=self.cycle_yt_buffer)

        row_yt_opts.add_widget(self.btn_yt_vquality)
        row_yt_opts.add_widget(self.btn_yt_buffer)

        row_yt_btns = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(8))
        btn_yt_voice = Factory.PrimaryButton(text="Live Voice Stream", size_hint=(0.5, 1))
        btn_yt_voice.font_size = "11sp"
        btn_yt_voice.bind(on_release=lambda *_: self.start_yt_stream(is_video=0))

        btn_yt_video = Factory.SecondaryButton(text="Live Video Stream", size_hint=(0.5, 1))
        btn_yt_video.font_size = "11sp"
        btn_yt_video.bind(on_release=lambda *_: self.start_yt_stream(is_video=1))

        row_yt_btns.add_widget(btn_yt_voice)
        row_yt_btns.add_widget(btn_yt_video)

        self.lbl_yt_status = Label(
            text="Idle. Ready to stream YouTube audio/video.",
            font_size="11sp", color=get_color_from_hex("#94A3B8"),
            size_hint_y=None, height=dp(26), halign="left", valign="middle"
        )
        self.lbl_yt_status.bind(size=self.lbl_yt_status.setter("text_size"))

        card_yt.add_widget(lbl_yt_title)
        card_yt.add_widget(self.input_yt_query)
        card_yt.add_widget(self.input_yt_target)
        card_yt.add_widget(row_yt_opts)
        card_yt.add_widget(row_yt_btns)
        card_yt.add_widget(self.lbl_yt_status)

        # ------------------------------------------------ Card 3: Place Voice / Video Call
        card_dial = Factory.Card()
        card_dial.padding = [dp(14), dp(12), dp(14), dp(12)]
        card_dial.spacing = dp(8)

        lbl_dial = Label(
            text="Place Voice or Video Call", font_size="14sp", bold=True,
            color=get_color_from_hex("#F8FAFC"), size_hint_y=None, height=dp(24), halign="left"
        )
        lbl_dial.bind(size=lbl_dial.setter("text_size"))

        self.input_call_target = Factory.ModernInput()
        self.input_call_target.hint_text = "Phone number with country code (e.g. 9198xxxxxxxx)"

        row_dial_btns = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(8))
        btn_vcall_audio = Factory.PrimaryButton(text="Voice Call", size_hint=(0.5, 1))
        btn_vcall_audio.font_size = "12sp"
        btn_vcall_audio.bind(on_release=lambda *_: self.dial_call(is_video=False))

        btn_vcall_video = Factory.SecondaryButton(text="Video Call", size_hint=(0.5, 1))
        btn_vcall_video.font_size = "12sp"
        btn_vcall_video.bind(on_release=lambda *_: self.dial_call(is_video=True))

        row_dial_btns.add_widget(btn_vcall_audio)
        row_dial_btns.add_widget(btn_vcall_video)

        lbl_dial_hint = Label(
            text="Tip: Use !call dial <num> <song> or !call vcall <num> <clip> in WhatsApp chat.",
            font_size="10sp", color=get_color_from_hex("#64748B"),
            size_hint_y=None, height=dp(18), halign="left", valign="middle"
        )
        lbl_dial_hint.bind(size=lbl_dial_hint.setter("text_size"))

        card_dial.add_widget(lbl_dial)
        card_dial.add_widget(self.input_call_target)
        card_dial.add_widget(row_dial_btns)
        card_dial.add_widget(lbl_dial_hint)

        # ------------------------------------------------ Card 3: Call Guard & Auto-Music Policy
        card_policy = Factory.Card()
        card_policy.padding = [dp(14), dp(12), dp(14), dp(12)]
        card_policy.spacing = dp(8)

        lbl_pol = Label(
            text="Call Guard & Auto-Music Rules", font_size="14sp", bold=True,
            color=get_color_from_hex("#F8FAFC"), size_hint_y=None, height=dp(24), halign="left"
        )
        lbl_pol.bind(size=lbl_pol.setter("text_size"))

        row_vm = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(8))
        lbl_vm = Label(text="Incoming Voice Calls:", font_size="12sp", color=get_color_from_hex("#CBD5E1"), halign="left", valign="middle")
        lbl_vm.bind(size=lbl_vm.setter("text_size"))
        self.btn_vmode = Factory.SecondaryButton(text="Mode: off", size_hint=(None, 1), width=dp(120))
        self.btn_vmode.font_size = "11sp"
        self.btn_vmode.bind(on_release=self.cycle_voice_mode)
        row_vm.add_widget(lbl_vm)
        row_vm.add_widget(self.btn_vmode)

        row_vidm = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(8))
        lbl_vidm = Label(text="Incoming Video Calls:", font_size="12sp", color=get_color_from_hex("#CBD5E1"), halign="left", valign="middle")
        lbl_vidm.bind(size=lbl_vidm.setter("text_size"))
        self.btn_vidmode = Factory.SecondaryButton(text="Video: same", size_hint=(None, 1), width=dp(120))
        self.btn_vidmode.font_size = "11sp"
        self.btn_vidmode.bind(on_release=self.cycle_video_mode)
        row_vidm.add_widget(lbl_vidm)
        row_vidm.add_widget(self.btn_vidmode)

        self.lbl_music_dir = Label(
            text=f"Music Folder: {call_media.call_music_dir()}",
            font_size="10sp", color=get_color_from_hex("#64748B"),
            size_hint_y=None, height=dp(26), halign="left", valign="middle"
        )
        self.lbl_music_dir.bind(size=self.lbl_music_dir.setter("text_size"))

        card_policy.add_widget(lbl_pol)
        card_policy.add_widget(row_vm)
        card_policy.add_widget(row_vidm)
        card_policy.add_widget(self.lbl_music_dir)

        # ------------------------------------------------ Card 4: Recent Call History
        card_hist = Factory.Card()
        card_hist.padding = [dp(14), dp(12), dp(14), dp(12)]
        card_hist.spacing = dp(8)

        row_hh = BoxLayout(size_hint_y=None, height=dp(30), spacing=dp(8))
        lbl_ht = Label(text="Recent Call Log", font_size="14sp", bold=True, color=get_color_from_hex("#F8FAFC"), halign="left", valign="middle")
        lbl_ht.bind(size=lbl_ht.setter("text_size"))
        btn_href = Factory.SecondaryButton(text=ICON_REFRESH, size_hint=(None, 1), width=dp(36))
        btn_href.font_size = "12sp"
        btn_href.bind(on_release=lambda *_: self.root.send("/call_refresh"))
        btn_hclr = Factory.DangerButton(text="Clear", size_hint=(None, 1), width=dp(58))
        btn_hclr.font_size = "11sp"
        btn_hclr.bind(on_release=lambda *_: self.root.send("/call_clear_history"))
        row_hh.add_widget(lbl_ht)
        row_hh.add_widget(btn_href)
        row_hh.add_widget(btn_hclr)

        self.hist_box = BoxLayout(orientation="vertical", spacing=dp(4), size_hint_y=None)
        self.hist_box.bind(minimum_height=self.hist_box.setter("height"))
        self.hist_box.add_widget(Label(
            text="No calls recorded yet.", font_size="11sp",
            color=get_color_from_hex("#64748B"), size_hint_y=None, height=dp(26)
        ))

        card_hist.add_widget(row_hh)
        card_hist.add_widget(self.hist_box)

        content.add_widget(card_live)
        content.add_widget(card_yt)
        content.add_widget(card_dial)
        content.add_widget(card_policy)
        content.add_widget(card_hist)
        scroll.add_widget(content)
        self.add_widget(scroll)

        self._load_policy_from_disk()

    def _policy_path(self) -> str:
        return os.path.join(plugin_data_dir("callmusic"), "config.json")

    def _load_policy_from_disk(self):
        try:
            p = self._policy_path()
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                self.voice_mode = str(cfg.get("mode") or "off")
                self.video_mode = str(cfg.get("video_mode") or "same")
                self.loop_enabled = bool(cfg.get("loop", True))
                self.btn_loop.is_active = self.loop_enabled
                self.btn_loop.text = "Loop ON" if self.loop_enabled else "Loop OFF"
                if cfg.get("default_track"):
                    self.selected_track = str(cfg["default_track"])
                    self.lbl_track.text = f"Track: {os.path.basename(self.selected_track)}"
        except Exception:
            pass
        self._refresh_policy_labels()

    def _save_policy_to_disk(self):
        try:
            p = self._policy_path()
            cfg = {}
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            cfg["mode"] = self.voice_mode
            cfg["video_mode"] = self.video_mode
            cfg["loop"] = self.loop_enabled
            if self.selected_track:
                cfg["default_track"] = self.selected_track
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
            self.root.send("/reload")
        except Exception as e:
            self.root.on_log(f"Call policy save failed: {e}")

    def _refresh_policy_labels(self):
        self.btn_vmode.text = f"Mode: {self.voice_mode}"
        self.btn_vidmode.text = f"Video: {self.video_mode}"

    def cycle_voice_mode(self, *_):
        idx = (self.VOICE_MODES.index(self.voice_mode) + 1) % len(self.VOICE_MODES) if self.voice_mode in self.VOICE_MODES else 0
        self.voice_mode = self.VOICE_MODES[idx]
        self._refresh_policy_labels()
        self._save_policy_to_disk()

    def cycle_video_mode(self, *_):
        idx = (self.VIDEO_MODES.index(self.video_mode) + 1) % len(self.VIDEO_MODES) if self.video_mode in self.VIDEO_MODES else 0
        self.video_mode = self.VIDEO_MODES[idx]
        self._refresh_policy_labels()
        self._save_policy_to_disk()

    def toggle_loop(self, *_):
        self.loop_enabled = not self.loop_enabled
        self.btn_loop.is_active = self.loop_enabled
        self.btn_loop.text = "Loop ON" if self.loop_enabled else "Loop OFF"
        self._save_policy_to_disk()

    def play_selected_audio(self):
        lp = 1 if self.loop_enabled else 0
        if self.selected_track.lower().endswith((".mp4", ".h264", ".mkv")):
            self.root.send("/call_play_video", self.selected_call_id, self.selected_track, 15, lp)
        else:
            self.root.send("/call_play", self.selected_call_id, self.selected_track, lp)

    def dial_call(self, is_video=False):
        tgt = self.input_call_target.text.strip()
        if not tgt:
            InfoDialog("Target Required", "Enter a WhatsApp phone number with country code.").open()
            return
        self.root.send("/call_place", tgt, 1 if is_video else 0)
        self.root.on_log(f"Dialing {tgt} ({'Video' if is_video else 'Voice'})...")

    def open_track_picker(self, *_):
        self.tracks = call_media.list_media_tracks()
        items = self.tracks.get("audio", []) + self.tracks.get("video", [])
        modal = ModalView(size_hint=(0.92, 0.72), auto_dismiss=True)
        card = Factory.DialogCard()
        card.padding = dp(12)
        card.spacing = dp(8)
        card.add_widget(Label(
            text=f"Select Call Music / Video ({len(items)} files)",
            font_size="14sp", bold=True, color=get_color_from_hex("#F8FAFC"),
            size_hint_y=None, height=dp(26)
        ))
        sc = ScrollView(size_hint=(1, 1))
        box = BoxLayout(orientation="vertical", spacing=dp(6), size_hint_y=None)
        box.bind(minimum_height=box.setter("height"))
        if not items:
            box.add_widget(Label(text="No media files found.", color=get_color_from_hex("#94A3B8"), size_hint_y=None, height=dp(40)))
        for p in items:
            tag = "[Video]" if p.lower().endswith((".mp4", ".h264", ".mkv")) else "[Audio]"
            b = Factory.SecondaryButton(text=f"{tag} {os.path.basename(p)}", size_hint_y=None, height=dp(36))
            b.font_size = "11sp"
            def _choose(_, path=p):
                self.selected_track = path
                self.lbl_track.text = f"Track: {os.path.basename(path)}"
                self._save_policy_to_disk()
                modal.dismiss()
            b.bind(on_release=_choose)
            box.add_widget(b)
        sc.add_widget(box)
        card.add_widget(sc)
        btn_close = Factory.SecondaryButton(text="Close", size_hint_y=None, height=dp(36))
        btn_close.bind(on_release=lambda *_: modal.dismiss())
        card.add_widget(btn_close)
        modal.add_widget(card)
        modal.open()

    def browse_media_file(self, *_):
        if platform != "android":
            self.open_track_picker()
            return
        try:
            from jnius import autoclass
            from android.activity import bind as android_bind, unbind as android_unbind
            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            Intent = autoclass("android.content.Intent")
            intent = Intent(Intent.ACTION_GET_CONTENT)
            intent.setType("*/*")
            intent.addCategory(Intent.CATEGORY_OPENABLE)
            REQ_MEDIA = 8809

            def _on_res(req, res, data_intent):
                if req == REQ_MEDIA:
                    try:
                        android_unbind(on_activity_result=_on_res)
                    except Exception:
                        pass
                    if res == -1 and data_intent is not None:
                        try:
                            uri = data_intent.getData()
                            if uri:
                                saved = copy_content_uri_to_folder(activity, uri, call_media.call_music_dir(), fallback_name="call_track.mp3")
                                def _apply(dt):
                                    self.selected_track = saved
                                    self.lbl_track.text = f"Track: {os.path.basename(saved)}"
                                    self._save_policy_to_disk()
                                    self.root.send("/call_refresh")
                                Clock.schedule_once(_apply, 0)
                        except Exception as e:
                            Clock.schedule_once(lambda dt, err=e: InfoDialog("Import Failed", str(err)).open(), 0)

            android_bind(on_activity_result=_on_res)
            activity.startActivityForResult(intent, REQ_MEDIA)
        except Exception as e:
            self.root.on_log(f"Media browse fallback: {e}")
            self.open_track_picker()

    def on_call_event(self, payload_str: str):
        try:
            data = json.loads(payload_str)
        except Exception:
            return
        if "supported" in data:
            ok = bool(data["supported"])
            self.lbl_voip_badge.text = "VoIP Ready" if ok else "Build .so Needed"
            self.lbl_voip_badge.color = get_color_from_hex("#10B981" if ok else "#EF4444")

        calls = data.get("active_calls")
        if isinstance(calls, list):
            self.active_calls = calls
            if calls:
                c = calls[-1]
                self.selected_call_id = str(c.get("call_id") or "")
                kind = "Video" if c.get("is_video") else "Voice"
                trk = c.get("audio_path") or "Idle"
                self.lbl_active_call.text = (
                    f"{kind} Call ({c.get('direction', '').upper()}) • {c.get('peer_number') or c.get('peer')}\n"
                    f"Phase: {c.get('phase')}  |  Audio: {trk}"
                )
                self.lbl_active_call.color = get_color_from_hex("#10B981")
            else:
                self.selected_call_id = ""
                self.lbl_active_call.text = "No active calls right now."
                self.lbl_active_call.color = get_color_from_hex("#94A3B8")

        if data.get("event") in ("end", "incoming", "outgoing"):
            self.root.send("/call_refresh")

    def on_call_history(self, payload_str: str):
        try:
            rows = json.loads(payload_str)
        except Exception:
            return
        self.hist_box.clear_widgets()
        if not rows:
            self.hist_box.add_widget(Label(
                text="No calls recorded yet.", font_size="11sp",
                color=get_color_from_hex("#64748B"), size_hint_y=None, height=dp(26)
            ))
            return
        for r in rows[:15]:
            kind = "Video" if r.get("is_video") else "Voice"
            arrow = "IN" if r.get("direction") == "in" else "OUT"
            ts_str = time.strftime("%d/%m %H:%M", time.localtime(r.get("started_at") or 0))
            trk = f" | {r['track_played']}" if r.get("track_played") else ""
            txt = f"[{arrow} {kind}] {r.get('peer_number') or r.get('peer')} • {r.get('status')} ({ts_str}){trk}"
            lbl = Label(
                text=txt, font_size="11sp", color=get_color_from_hex("#CBD5E1"),
                size_hint_y=None, height=dp(22), halign="left", valign="middle"
            )
            lbl.bind(size=lbl.setter("text_size"))
            self.hist_box.add_widget(lbl)

    def on_call_tracks(self, payload_str: str):
        try:
            data = json.loads(payload_str)
            if isinstance(data, dict):
                self.tracks = data
        except Exception:
            pass

    def cycle_yt_vquality(self, *_):
        idx = (self.YT_QUALITIES.index(self.yt_vquality) + 1) % len(self.YT_QUALITIES) if self.yt_vquality in self.YT_QUALITIES else 0
        self.yt_vquality = self.YT_QUALITIES[idx]
        self.btn_yt_vquality.text = f"Video Quality: {self.yt_vquality}"

    def cycle_yt_buffer(self, *_):
        idx = (self.YT_BUFFERS.index(self.yt_buffer_mode) + 1) % len(self.YT_BUFFERS) if self.yt_buffer_mode in self.YT_BUFFERS else 0
        self.yt_buffer_mode = self.YT_BUFFERS[idx]
        self.btn_yt_buffer.text = f"Buffer: {self.yt_buffer_mode}"

    def start_yt_stream(self, is_video: int = 0):
        query = self.input_yt_query.text.strip()
        if not query:
            InfoDialog("YouTube Query Required", "Enter a YouTube URL or song/video title.").open()
            return
        target = self.input_yt_target.text.strip()
        if not target:
            if self.selected_call_id:
                target = self.selected_call_id
            elif self.active_calls:
                target = str(self.active_calls[-1].get("call_id") or "")
            else:
                target = self.input_call_target.text.strip()
        self.lbl_yt_status.text = f"Starting {'Video' if is_video else 'Voice'} stream for '{query}'..."
        self.lbl_yt_status.color = get_color_from_hex("#38BDF8")
        self.root.send("/yt_call_stream", target, query, 1 if is_video else 0, self.yt_vquality, self.yt_buffer_mode)

    def on_yt_stream_status(self, payload_str: str):
        txt = str(payload_str or "").strip()
        if not txt:
            return
        color = "#10B981"
        try:
            if txt.startswith("{"):
                d = json.loads(txt)
                err = str(d.get("error") or "").strip()
                title = str(d.get("title") or "").strip()
                state = str(d.get("status") or d.get("state") or "").strip()
                msg = str(d.get("message") or "").strip()
                if err:
                    txt = f"Error: {err}"
                    color = "#EF4444"
                else:
                    parts = [p for p in (state, title, msg) if p]
                    txt = " • ".join(parts) if parts else txt
                    if any(w in state.lower() for w in ("stop", "idle", "end")):
                        color = "#94A3B8"
                    elif any(w in state.lower() for w in ("load", "fetch", "buffer", "resolv", "start")):
                        color = "#38BDF8"
            else:
                low = txt.lower()
                if "error" in low or "fail" in low:
                    color = "#EF4444"
                elif any(w in low for w in ("fetch", "resolv", "buffer", "start")):
                    color = "#38BDF8"
                elif any(w in low for w in ("stop", "idle", "end")):
                    color = "#94A3B8"
        except Exception:
            pass
        self.lbl_yt_status.text = txt
        self.lbl_yt_status.color = get_color_from_hex(color)


class QuickSendScreen(Screen):
    """Direct message dispatch to test bot delivery and anti-spam pacing."""

    def __init__(self, app_root, **kw):
        super().__init__(name="send", **kw)
        self.root = app_root

        scroll = ScrollView(size_hint=(1, 1))
        content = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12), size_hint_y=None)
        content.bind(minimum_height=content.setter("height"))

        card = Factory.Card()
        lbl_title = Label(
            text="Direct Dispatch", font_size="15sp", bold=True,
            color=get_color_from_hex("#F8FAFC"), size_hint_y=None, height=dp(26), halign="left", valign="middle"
        )
        lbl_title.bind(size=lbl_title.setter('text_size'))

        lbl_desc = Label(
            text="Send a message directly through the bot's anti-spam queue to test connectivity.",
            font_size="11sp", color=get_color_from_hex("#94A3B8"),
            size_hint_y=None, height=dp(24), halign="left", valign="middle"
        )
        lbl_desc.bind(size=lbl_desc.setter('text_size'))

        self.input_target = Factory.ModernInput()
        self.input_target.hint_text = "Target number (e.g. 9198xxxxxxxx or self)"

        self.input_msg = Factory.ModernInput()
        self.input_msg.hint_text = "Message text..."
        self.input_msg.height = dp(76)
        self.input_msg.multiline = True

        btn_send = Factory.PrimaryButton(text="Queue Outbound Message")
        btn_send.bind(on_release=self.on_send)

        btn_ping_self = Factory.SecondaryButton(text="Ping Myself (Test Response)")
        btn_ping_self.bind(on_release=lambda *_: self.root.send("/send", "self", "!ping"))

        self.lbl_feedback = Label(
            text="", font_size="11sp", color=get_color_from_hex("#10B981"),
            size_hint_y=None, height=dp(20)
        )

        card.add_widget(lbl_title)
        card.add_widget(lbl_desc)
        card.add_widget(self.input_target)
        card.add_widget(self.input_msg)
        card.add_widget(btn_send)
        card.add_widget(btn_ping_self)
        card.add_widget(self.lbl_feedback)

        content.add_widget(card)
        scroll.add_widget(content)
        self.add_widget(scroll)

    def on_send(self, *_):
        target = self.input_target.text.strip()
        msg = self.input_msg.text.strip()
        if target and msg:
            self.lbl_feedback.color = get_color_from_hex("#10B981")
            self.root.send("/send", target, msg)
            self.lbl_feedback.text = f"Dispatched to outbound queue for {target}"
            self.input_msg.text = ""
            Clock.schedule_once(lambda dt: setattr(self.lbl_feedback, "text", ""), 4)
        else:
            self.lbl_feedback.color = get_color_from_hex("#EF4444")
            self.lbl_feedback.text = "Please enter both target number and message."
            Clock.schedule_once(lambda dt: setattr(self.lbl_feedback, "text", ""), 3)


class ConsoleScreen(Screen):
    """Live terminal logs with pagination, log file persistence, and storage export."""

    def __init__(self, app_root, **kw):
        super().__init__(name="console", **kw)
        self.root = app_root
        self.auto_scroll = True
        self.all_logs = []
        self.current_page = 1
        self.page_size = 50

        layout = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(10))

        # Header - Pagination & Export Toolbar (2-row responsive card)
        header = Factory.Card()
        header.padding = [dp(12), dp(8), dp(12), dp(8)]
        header.spacing = dp(6)

        # Row 1: Title & Pagination Controls
        row_top = BoxLayout(size_hint_y=None, height=dp(32), spacing=dp(6))
        title = Label(
            text="Console Logs", font_size="14sp", bold=True,
            color=get_color_from_hex("#F8FAFC"), halign="left", valign="middle"
        )
        title.bind(size=title.setter('text_size'))

        btn_prev = Factory.SecondaryButton(text="<", size_hint=(None, None), size=(dp(36), dp(32)))
        btn_prev.bind(on_release=self.prev_page)

        self.lbl_page = Label(text="1/1", font_size="11sp", bold=True,
                              color=get_color_from_hex("#64748B"), size_hint_x=None, width=dp(44), halign="center")

        btn_next = Factory.SecondaryButton(text=">", size_hint=(None, None), size=(dp(36), dp(32)))
        btn_next.bind(on_release=self.next_page)

        row_top.add_widget(title)
        row_top.add_widget(btn_prev)
        row_top.add_widget(self.lbl_page)
        row_top.add_widget(btn_next)

        # Row 2: Action Buttons
        row_actions = BoxLayout(size_hint_y=None, height=dp(32), spacing=dp(6))
        self.btn_scroll = Factory.SecondaryButton(text="Auto: ON", size_hint=(0.34, None), height=dp(32))
        self.btn_scroll.font_size = "11sp"
        self.btn_scroll.bind(on_release=self.toggle_scroll)

        btn_export = Factory.SecondaryButton(text="Export Logs", size_hint=(0.33, None), height=dp(32))
        btn_export.font_size = "11sp"
        btn_export.bind(on_release=self.export_logs)

        btn_clear = Factory.SecondaryButton(text="Clear", size_hint=(0.33, None), height=dp(32))
        btn_clear.font_size = "11sp"
        btn_clear.bind(on_release=self.clear_logs)

        row_actions.add_widget(self.btn_scroll)
        row_actions.add_widget(btn_export)
        row_actions.add_widget(btn_clear)

        header.add_widget(row_top)
        header.add_widget(row_actions)
        layout.add_widget(header)

        # Monospace Terminal Card
        terminal_card = Factory.TerminalCard()
        self.scroll = ScrollView(size_hint=(1, 1))
        self.log_label = Label(
            text="[System initialized. Listening for engine events...]",
            size_hint_y=None, font_size="11sp",
            color=get_color_from_hex("#94A3B8"),
            halign="left", valign="top"
        )
        self.log_label.bind(
            texture_size=lambda w, s: setattr(w, "height", max(s[1], self.scroll.height)),
            width=lambda w, v: setattr(w, "text_size", (v, None))
        )
        self.scroll.add_widget(self.log_label)
        terminal_card.add_widget(self.scroll)
        layout.add_widget(terminal_card)

        self.add_widget(layout)

    def total_pages(self):
        return max(1, (len(self.all_logs) + self.page_size - 1) // self.page_size)

    def render_page(self):
        tot = self.total_pages()
        self.current_page = max(1, min(self.current_page, tot))
        self.lbl_page.text = f"{self.current_page}/{tot}"
        start = (self.current_page - 1) * self.page_size
        page_lines = self.all_logs[start : start + self.page_size]
        self.log_label.text = "\n".join(page_lines) if page_lines else "[No logs on this page]"
        if self.auto_scroll and self.current_page == tot:
            Clock.schedule_once(lambda dt: setattr(self.scroll, "scroll_y", 0), 0.05)

    def prev_page(self, *_):
        if self.current_page > 1:
            self.current_page -= 1
            self.auto_scroll = False
            self.btn_scroll.text = "Auto: OFF"
            self.render_page()

    def next_page(self, *_):
        if self.current_page < self.total_pages():
            self.current_page += 1
            self.render_page()

    def toggle_scroll(self, *_):
        self.auto_scroll = not self.auto_scroll
        self.btn_scroll.text = f"Auto: {'ON' if self.auto_scroll else 'OFF'}"
        if self.auto_scroll:
            self.current_page = self.total_pages()
            self.render_page()

    def clear_logs(self, *_):
        self.all_logs.clear()
        self.current_page = 1
        self.render_page()

    def export_logs(self, *_):
        ok, res = export_activity_log()
        title = "Logs Exported" if ok else "Export Failed"
        msg = f"Saved to:\n{res}" if ok else f"Error: {res}"
        InfoDialog(title, msg).open()

    def append_log(self, text):
        t_str = time.strftime("%H:%M:%S")
        line = f"[{t_str}] {text}"
        self.all_logs.append(line)
        if self.auto_scroll:
            self.current_page = self.total_pages()
        self.render_page()


def copy_content_uri_to_folder(activity, uri, dest_dir: str, fallback_name: str = "imported_file") -> str:
    """Stream bytes from a content:// URI into dest_dir using ParcelFileDescriptor or FileUtils."""
    from jnius import autoclass
    cr = activity.getContentResolver()

    filename = fallback_name
    try:
        OpenableColumns = autoclass("android.provider.OpenableColumns")
        cursor = cr.query(uri, None, None, None, None)
        if cursor and cursor.moveToFirst():
            name_idx = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if name_idx >= 0:
                val = cursor.getString(name_idx)
                if val:
                    filename = str(val)
            cursor.close()
    except Exception:
        pass

    if "." not in filename:
        try:
            last_seg = uri.getLastPathSegment()
            if last_seg and "." in last_seg:
                filename = last_seg.split("/")[-1]
        except Exception:
            pass

    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(filename))

    copied = False
    try:
        pfd = cr.openFileDescriptor(uri, "r")
        if pfd:
            fd = pfd.getFd()
            with open(fd, "rb", closefd=False) as src_f, open(dest, "wb") as dst_f:
                shutil.copyfileobj(src_f, dst_f, length=65536)
            pfd.close()
            if os.path.exists(dest) and os.path.getsize(dest) > 0:
                copied = True
    except Exception:
        copied = False

    if not copied:
        try:
            FileUtils = autoclass("android.os.FileUtils")
            FileOutputStream = autoclass("java.io.FileOutputStream")
            in_stream = cr.openInputStream(uri)
            out_stream = FileOutputStream(dest)
            FileUtils.copy(in_stream, out_stream)
            try:
                in_stream.close()
            except Exception:
                pass
            try:
                out_stream.close()
            except Exception:
                pass
            if os.path.exists(dest) and os.path.getsize(dest) > 0:
                copied = True
        except Exception:
            copied = False

    if not os.path.exists(dest) or os.path.getsize(dest) == 0:
        raise RuntimeError("Selected file could not be read or is empty (0 bytes).")

    return dest


class SettingsScreen(Screen):
    """Configuration, Ghost Mode, Auto-Wipe, Service Shutdown, Sync & Session Import/Export."""

    def __init__(self, app_root, **kw):
        super().__init__(name="settings", **kw)
        self.root = app_root
        self.ghost_cfg = getattr(app_root, "ghost_config", None) or GhostConfig.load(private_dir())
        self.auto_wipe_active = self.ghost_cfg.auto_wipe
        self.stealth_read_active = self.ghost_cfg.ghost_read
        self.auto_forward_active = self.ghost_cfg.anti_delete_relay
        self._privacy_btns = {}

        scroll = ScrollView(size_hint=(1, 1))
        content = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(12), size_hint_y=None)
        content.bind(minimum_height=content.setter("height"))

        # ---------------------------------------------------- Card 1: Privacy & Stealth (Ghost Mode)
        card_privacy = Factory.Card()
        card_privacy.padding = [dp(14), dp(12), dp(14), dp(12)]
        card_privacy.spacing = dp(8)

        lbl_p_title = Label(
            text="Privacy & Stealth", font_size="14sp", bold=True,
            color=get_color_from_hex("#F8FAFC"), size_hint_y=None, height=dp(24), halign="left"
        )
        lbl_p_title.bind(size=lbl_p_title.setter('text_size'))

        lbl_p_sub = Label(
            text="Wire-level Ghost Mode controls, receipt suppression, and cache hygiene.",
            font_size="11sp", color=get_color_from_hex("#64748B"), size_hint_y=None, height=dp(20), halign="left"
        )
        lbl_p_sub.bind(size=lbl_p_sub.setter('text_size'))

        card_privacy.add_widget(lbl_p_title)
        card_privacy.add_widget(lbl_p_sub)

        privacy_rows = [
            ("ghost_read", "Ghost Read (Hide blue ticks)"),
            ("ghost_presence", "Ghost Presence (Hide online status)"),
            ("ghost_typing", "Ghost Typing (Hide typing indicator)"),
            ("ghost_viewonce_inline", "View-Once Ghost (Save silently)"),
            ("auto_wipe", "Auto-Wipe RAM Cache (24h retention)"),
            ("anti_delete_relay", "Anti-Delete Relay to Saved Messages"),
        ]
        for cfg_key, label_txt in privacy_rows:
            row = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(8))
            lbl = Label(
                text=label_txt, font_size="12sp",
                color=get_color_from_hex("#F8FAFC"), halign="left", valign="middle"
            )
            lbl.bind(size=lbl.setter('text_size'))
            val = bool(getattr(self.ghost_cfg, cfg_key, False))
            sw = SwitchBtn(text="ON" if val else "OFF", is_active=val)
            sw.bind(on_release=lambda _b, k=cfg_key: self._toggle_privacy(k))
            self._privacy_btns[cfg_key] = sw
            row.add_widget(lbl)
            row.add_widget(sw)
            card_privacy.add_widget(row)

        self.btn_stealth = self._privacy_btns["ghost_read"]
        self.btn_auto_wipe = self._privacy_btns["auto_wipe"]
        self.btn_forward = self._privacy_btns["anti_delete_relay"]

        # Action: Flush cache
        btn_flush = Factory.SecondaryButton(text="Flush Intercepted Message Cache Now")
        btn_flush.bind(on_release=self.on_flush_cache)
        card_privacy.add_widget(btn_flush)

        # ---------------------------------------------------- Card 2: Session Backup & Sync
        card_session = Factory.Card()
        card_session.padding = [dp(14), dp(12), dp(14), dp(12)]
        card_session.spacing = dp(8)

        lbl_s_title = Label(
            text="Session Backup & Transfer", font_size="14sp", bold=True,
            color=get_color_from_hex("#F8FAFC"), size_hint_y=None, height=dp(24), halign="left"
        )
        lbl_s_title.bind(size=lbl_s_title.setter('text_size'))

        lbl_s_sub = Label(
            text="Export or restore authenticated WhatsApp session credentials.",
            font_size="11sp", color=get_color_from_hex("#64748B"), size_hint_y=None, height=dp(20), halign="left"
        )
        lbl_s_sub.bind(size=lbl_s_sub.setter('text_size'))

        btn_pick = Factory.PrimaryButton(text="Select Backup File (.zip / .sqlite3)")
        btn_pick.bind(on_release=self.pick_backup_file)

        btn_export = Factory.SecondaryButton(text="Export Session Archive (.zip)")
        btn_export.bind(on_release=self.export_session)

        lbl_manual = Label(
            text="Or restore manually from file path / name:", font_size="11sp",
            color=get_color_from_hex("#94A3B8"), size_hint_y=None, height=dp(20), halign="left"
        )
        lbl_manual.bind(size=lbl_manual.setter('text_size'))

        row_manual = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8))
        self.input_import = Factory.ModernInput(size_hint_x=0.65)
        self.input_import.hint_text = "WA_Session_...zip or full path"
        btn_import = Factory.SecondaryButton(text="Restore", size_hint=(0.35, 1))
        btn_import.bind(on_release=self.import_session)
        row_manual.add_widget(self.input_import)
        row_manual.add_widget(btn_import)

        btn_perm = Factory.SecondaryButton(text="Manage Storage Permissions")
        btn_perm.bind(on_release=lambda *_: ask_all_files_permission())

        # WhatsApp Sync & Recovery subsection
        lbl_sync_head = Label(
            text="WhatsApp Sync & Recovery", font_size="12sp", bold=True,
            color=get_color_from_hex("#CBD5E1"), size_hint_y=None, height=dp(22), halign="left"
        )
        lbl_sync_head.bind(size=lbl_sync_head.setter('text_size'))

        self.lbl_sync_info = Label(
            text="Sync Status: Idle", font_size="10sp",
            color=get_color_from_hex("#64748B"), size_hint_y=None, height=dp(18), halign="left"
        )
        self.lbl_sync_info.bind(size=self.lbl_sync_info.setter('text_size'))

        row_sync_btns = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(8))
        btn_resync_cg = Factory.SecondaryButton(text="Resync Contacts & Groups", size_hint=(0.5, 1))
        btn_resync_cg.font_size = "11sp"
        btn_resync_cg.bind(on_release=lambda *_: (self.root.send("/resync_contacts"), self.root.on_log("Requested contacts & groups resync...")))

        btn_hist_sync = Factory.SecondaryButton(text="Trigger History Resync", size_hint=(0.5, 1))
        btn_hist_sync.font_size = "11sp"
        btn_hist_sync.bind(on_release=lambda *_: (self.root.send("/history_sync"), self.root.on_log("Triggered WhatsApp history resync...")))

        row_sync_btns.add_widget(btn_resync_cg)
        row_sync_btns.add_widget(btn_hist_sync)

        card_session.add_widget(lbl_s_title)
        card_session.add_widget(lbl_s_sub)
        card_session.add_widget(btn_pick)
        card_session.add_widget(btn_export)
        card_session.add_widget(lbl_manual)
        card_session.add_widget(row_manual)
        card_session.add_widget(btn_perm)
        card_session.add_widget(lbl_sync_head)
        card_session.add_widget(self.lbl_sync_info)
        card_session.add_widget(row_sync_btns)

        # ---------------------------------------------------- Card 3: Service & Power
        card_engine = Factory.Card()
        card_engine.padding = [dp(14), dp(12), dp(14), dp(12)]
        card_engine.spacing = dp(8)

        lbl_e_title = Label(
            text="Service & Battery Policy", font_size="14sp", bold=True,
            color=get_color_from_hex("#F8FAFC"), size_hint_y=None, height=dp(24), halign="left"
        )
        lbl_e_title.bind(size=lbl_e_title.setter('text_size'))

        lbl_e_sub = Label(
            text="Keep the background worker alive when device is idle or sleeping.",
            font_size="11sp", color=get_color_from_hex("#64748B"), size_hint_y=None, height=dp(20), halign="left"
        )
        lbl_e_sub.bind(size=lbl_e_sub.setter('text_size'))

        btn_battery = Factory.SecondaryButton(text="Request Battery Optimization Exemption")
        btn_battery.bind(on_release=lambda *_: ask_ignore_battery_optimizations())

        btn_kill_svc = Factory.DangerButton(text="Terminate Background Service")
        btn_kill_svc.bind(on_release=self.terminate_service)

        card_engine.add_widget(lbl_e_title)
        card_engine.add_widget(lbl_e_sub)
        card_engine.add_widget(btn_battery)
        card_engine.add_widget(btn_kill_svc)

        # ---------------------------------------------------- Card 4: Danger Zone
        card_danger = Factory.Card()
        card_danger.padding = [dp(14), dp(12), dp(14), dp(12)]
        card_danger.spacing = dp(8)

        lbl_d_title = Label(
            text="Danger Zone", font_size="14sp", bold=True,
            color=get_color_from_hex("#EF4444"), size_hint_y=None, height=dp(24), halign="left"
        )
        lbl_d_title.bind(size=lbl_d_title.setter('text_size'))

        lbl_d_sub = Label(
            text="Permanently clear session keys and disconnect from WhatsApp network.",
            font_size="11sp", color=get_color_from_hex("#64748B"), size_hint_y=None, height=dp(20), halign="left"
        )
        lbl_d_sub.bind(size=lbl_d_sub.setter('text_size'))

        btn_logout = Factory.DangerButton(text="Logout & Disconnect Session")
        btn_logout.bind(on_release=self.on_logout)

        card_danger.add_widget(lbl_d_title)
        card_danger.add_widget(lbl_d_sub)
        card_danger.add_widget(btn_logout)

        # ---------------------------------------------------- Card 5: About & Telemetry
        card_about = Factory.Card()
        card_about.padding = [dp(14), dp(12), dp(14), dp(12)]
        card_about.spacing = dp(6)

        lbl_a_title = Label(
            text="About WA Core", font_size="14sp", bold=True,
            color=get_color_from_hex("#F8FAFC"), size_hint_y=None, height=dp(24), halign="left"
        )
        lbl_a_title.bind(size=lbl_a_title.setter('text_size'))

        lbl_meta1 = Label(
            text="Engine: Neonize Go / WhatsApp Web MD", font_size="11sp",
            color=get_color_from_hex("#94A3B8"), size_hint_y=None, height=dp(18), halign="left"
        )
        lbl_meta1.bind(size=lbl_meta1.setter('text_size'))

        lbl_meta2 = Label(
            text=f"Engine Version: {APP_VERSION}  •  Arch: arm64-v8a", font_size="11sp",
            color=get_color_from_hex("#64748B"), size_hint_y=None, height=dp(18), halign="left"
        )
        lbl_meta2.bind(size=lbl_meta2.setter('text_size'))

        lbl_meta3 = Label(
            text="Runtime: Python 3.11  •  Kivy 2.3.0", font_size="11sp",
            color=get_color_from_hex("#64748B"), size_hint_y=None, height=dp(18), halign="left"
        )
        lbl_meta3.bind(size=lbl_meta3.setter('text_size'))

        row_about_btns = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(8))

        btn_about_logs = Factory.SecondaryButton(text="Export Logs (.log)", size_hint=(0.48, 1))
        btn_about_logs.font_size = "11sp"
        btn_about_logs.bind(on_release=self.export_logs_from_about)

        self.btn_check_update = Factory.PrimaryButton(text="Check Updates", size_hint=(0.52, 1))
        self.btn_check_update.font_size = "11sp"
        self.btn_check_update.bind(on_release=self.on_check_update_press)

        row_about_btns.add_widget(btn_about_logs)
        row_about_btns.add_widget(self.btn_check_update)

        card_about.add_widget(lbl_a_title)
        card_about.add_widget(lbl_meta1)
        card_about.add_widget(lbl_meta2)
        card_about.add_widget(lbl_meta3)
        card_about.add_widget(row_about_btns)

        content.add_widget(card_privacy)
        content.add_widget(card_session)
        content.add_widget(card_engine)
        content.add_widget(card_danger)
        content.add_widget(card_about)

        scroll.add_widget(content)
        self.add_widget(scroll)

    def _refresh_privacy_ui(self):
        for k, sw in self._privacy_btns.items():
            val = bool(getattr(self.ghost_cfg, k, False))
            sw.is_active = val
            sw.text = "ON" if val else "OFF"
        self.auto_wipe_active = bool(self.ghost_cfg.auto_wipe)
        self.stealth_read_active = bool(self.ghost_cfg.ghost_read)
        self.auto_forward_active = bool(self.ghost_cfg.anti_delete_relay)

    def _toggle_privacy(self, key: str):
        cur = bool(getattr(self.ghost_cfg, key, False))
        new_val = not cur
        setattr(self.ghost_cfg, key, new_val)
        self.root.ghost_config = self.ghost_cfg
        self._refresh_privacy_ui()
        self.root.send("/privacy_update", json.dumps(self.ghost_cfg.to_dict()))
        self.root.on_log(f"Privacy '{key}' {'enabled' if new_val else 'disabled'}.")
        if key == "ghost_read":
            if hasattr(self.root, "screen_chats"):
                self.root.screen_chats.schedule_refresh(0.05)
            if hasattr(self.root, "screen_chatroom") and self.root.sm.current == "chatroom":
                self.root.screen_chatroom._update_header_meta()

    def apply_privacy_state(self, state_dict: dict):
        if not isinstance(state_dict, dict):
            return
        self.ghost_cfg.update_from_dict(state_dict)
        self.root.ghost_config = self.ghost_cfg
        self._refresh_privacy_ui()

    def on_sync_state(self, detail: str):
        if hasattr(self, "lbl_sync_info"):
            self.lbl_sync_info.text = f"Sync Status: {detail}" if detail else "Sync Status: Idle"

    def toggle_auto_wipe(self, *_):
        self._toggle_privacy("auto_wipe")

    def toggle_stealth(self, *_):
        self._toggle_privacy("ghost_read")

    def toggle_forward(self, *_):
        self._toggle_privacy("anti_delete_relay")

    def on_flush_cache(self, *_):
        self.root.send("/wipe_cache")
        self.root.on_log("Flushing intercepted message cache...")
        InfoDialog("Cache Cleared", "Intercepted message cache has been flushed.").open()

    def export_logs_from_about(self, *_):
        ok, res = export_activity_log()
        title = "Logs Exported" if ok else "Export Failed"
        msg = f"Saved to:\n{res}" if ok else f"Error: {res}"
        InfoDialog(title, msg).open()

    def terminate_service(self, *_):
        self.root.engine_state = "stopped"
        self.root.is_connected = False
        self.root.btn_master.text = ICON_PLAY
        self.root.btn_master.is_running = False
        self.root.lbl_pill.text = f"{ICON_DOT_OFF} OFFLINE"
        self.root.lbl_pill.color = get_color_from_hex("#EF4444")
        self.root.send("/kill_service")
        stop_android_service()
        self.root.on_log("Background service terminated by user request.")

    def export_session(self, *_):
        ok, res = export_session_zip()
        title = "Session Exported" if ok else "Export Failed"
        msg = f"Archive saved to:\n{res}" if ok else f"Error: {res}"
        InfoDialog(title, msg).open()

    def import_session(self, *_):
        path = self.input_import.text.strip()
        ok, res = import_session_zip(path)
        title = "Session Restored" if ok else "Restore Failed"
        msg = res if ok else f"Error: {res}"
        InfoDialog(title, msg).open()
        if ok:
            self.root.send("/start_bot")

    def pick_backup_file(self, *_):
        """Open native Android file chooser to directly select a backup file without permission errors."""
        if platform != "android":
            self.open_kivy_file_chooser()
            return

        try:
            from jnius import autoclass
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            activity = PythonActivity.mActivity
            if not activity:
                self.open_kivy_file_chooser()
                return

            Intent = autoclass("android.content.Intent")
            intent = Intent(Intent.ACTION_GET_CONTENT)
            intent.setType("*/*")
            intent.addCategory(Intent.CATEGORY_OPENABLE)

            REQUEST_CODE_PICK_FILE = 8801

            try:
                from android.activity import bind as android_bind, unbind as android_unbind

                def _on_activity_result(request_code, result_code, data_intent):
                    if request_code == REQUEST_CODE_PICK_FILE:
                        try:
                            android_unbind(on_activity_result=_on_activity_result)
                        except Exception:
                            pass
                        # RESULT_OK is -1
                        if result_code == -1 and data_intent is not None:
                            try:
                                uri = data_intent.getData()
                                if uri:
                                    local_path = self.copy_content_uri_to_private(activity, uri)
                                    Clock.schedule_once(lambda dt: self.apply_restored_file(local_path), 0)
                                    return
                            except Exception as e:
                                self.root.on_log(f"Failed to read selected file: {e}")
                                Clock.schedule_once(lambda dt: InfoDialog("Restore Error", f"Failed to read file:\n{e}").open(), 0)
                                return
                        self.root.on_log("File selection cancelled.")

                android_bind(on_activity_result=_on_activity_result)
                activity.startActivityForResult(intent, REQUEST_CODE_PICK_FILE)
            except Exception as e:
                self.root.on_log(f"Native picker error: {e}, using in-app chooser")
                self.open_kivy_file_chooser()
        except Exception as e:
            self.root.on_log(f"File picker error: {e}")
            self.open_kivy_file_chooser()

    def copy_content_uri_to_private(self, activity, uri) -> str:
        """Stream bytes from content:// URI into private storage using shared native copier."""
        return copy_content_uri_to_folder(activity, uri, private_dir(), fallback_name="imported_session_backup")

    def apply_restored_file(self, local_path):
        """Perform restore with chosen local file."""
        self.input_import.text = os.path.basename(local_path)
        ok, res = import_session_zip(local_path)
        title = "Session Restored" if ok else "Restore Failed"
        msg = res if ok else f"Error: {res}"
        InfoDialog(title, msg).open()
        if ok:
            self.root.on_log(f"Session restored from {os.path.basename(local_path)}. Reconnecting...")
            self.root.send("/start_bot")

    def open_kivy_file_chooser(self):
        """In-app file chooser dialog fallback."""
        from kivy.uix.filechooser import FileChooserListView
        from kivy.uix.popup import Popup

        content = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(8))
        fc = FileChooserListView(path="/sdcard/Download" if os.path.isdir("/sdcard/Download") else "/sdcard",
                                 filters=["*.zip", "*.sqlite3"])
        btn_box = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(8))
        popup = Popup(title="Select Backup (.zip or .sqlite3)", size_hint=(0.95, 0.9), auto_dismiss=True)

        def _select(*_):
            if fc.selection:
                sel = fc.selection[0]
                popup.dismiss()
                self.apply_restored_file(sel)

        btn_ok = Factory.PrimaryButton(text="Select")
        btn_ok.bind(on_release=_select)
        btn_cancel = Factory.SecondaryButton(text="Cancel")
        btn_cancel.bind(on_release=popup.dismiss)

        btn_box.add_widget(btn_cancel)
        btn_box.add_widget(btn_ok)
        content.add_widget(fc)
        content.add_widget(btn_box)
        popup.content = content
        popup.open()

    def on_logout(self, *_):
        self.root.send("/clear_session")
        self.root.is_connected = False
        self.root.update_tab_bar()
        self.root.switch_tab("connect")

    def on_check_update_press(self, *_):
        self.btn_check_update.text = "Checking..."
        self.btn_check_update.disabled = True

        def _worker():
            ok, info, msg = updater.check_for_updates(APP_VERSION)
            def _done(dt):
                self.btn_check_update.text = "Check Updates"
                self.btn_check_update.disabled = False
                self.root.on_log(msg)
                if ok and info.get("has_update") and info.get("download_url"):
                    UpdateDialog(info, on_install=self.download_and_install_update).open()
                else:
                    InfoDialog("App Update Status", msg).open()
            Clock.schedule_once(_done, 0.05)

        threading.Thread(target=_worker, daemon=True).start()

    def download_and_install_update(self, dialog, download_url):
        def _worker():
            def _prog(pct, cur_mb, tot_mb):
                def _update_ui(dt):
                    dialog.lbl_progress.text = f"Downloading: {pct}% ({cur_mb:.1f} MB / {tot_mb:.1f} MB)"
                Clock.schedule_once(_update_ui, 0)

            ok, res_or_path = updater.download_update_apk(download_url, on_progress=_prog)
            def _finished(dt):
                if ok:
                    dialog.lbl_progress.text = "Download complete! Opening installer..."
                    self.root.on_log(f"Update APK downloaded to {res_or_path}")
                    inst_ok, inst_msg = updater.launch_apk_installer(res_or_path)
                    if not inst_ok:
                        InfoDialog("Install Notice", f"APK downloaded to:\n{res_or_path}\n\n{inst_msg}").open()
                    dialog.dismiss()
                else:
                    dialog.lbl_progress.text = "Download failed."
                    dialog.btn_download.disabled = False
                    dialog.btn_download.text = "Retry Download"
                    InfoDialog("Update Download Failed", res_or_path).open()
            Clock.schedule_once(_finished, 0.05)

        threading.Thread(target=_worker, daemon=True).start()


# --------------------------------------------------------------------------- Root Layout
class Root(BoxLayout):
    def __init__(self, **kw):
        try:
            super().__init__(**kw)
            self.engine_state = "running"
            self.is_connected = False
            self.tabs = {}
            self.ghost_config = GhostConfig.load(private_dir())
            self._base_pill_text = f"{ICON_DOT_ON} CONNECTING"
            self._base_pill_color = "#F59E0B"

            # Top Bar
            top_bar = BoxLayout(size_hint_y=None, height=dp(56), padding=[dp(12), dp(6), dp(12), dp(6)], spacing=dp(8))
            self.title_lbl = Label(
                text="WA CORE", font_size="17sp", bold=True,
                color=get_color_from_hex("#F3F4F6"), halign="left", valign="middle",
                size_hint_x=None, width=dp(92)
            )
            self.title_lbl.bind(size=self.title_lbl.setter('text_size'))

            # Live Status Pill
            self.status_pill = Factory.StatusPill()
            self.lbl_pill = Label(text=self._base_pill_text, font_size="11sp", bold=True,
                                  color=get_color_from_hex(self._base_pill_color))
            self.status_pill.add_widget(self.lbl_pill)

            # Master Header Quick Start/Stop Toggle Button with crisp symbols
            self.btn_master = MasterHeaderBtn(text=ICON_STOP)
            self.btn_master.bind(on_release=self.on_master_toggle)

            top_bar.add_widget(self.title_lbl)
            top_bar.add_widget(self.status_pill)
            top_bar.add_widget(self.btn_master)
            self.add_widget(top_bar)

            # Tab Navigation Row
            self.tab_bar = BoxLayout(size_hint_y=None, height=dp(46), padding=[dp(8), dp(2), dp(8), dp(4)], spacing=dp(6))
            self.add_widget(self.tab_bar)

            # Screen Manager
            self.sm = ScreenManager(transition=FadeTransition(duration=0.15))
            self.screen_connect = ConnectScreen(self)
            self.screen_chats = ChatsScreen(self)
            self.screen_chatroom = ChatRoomScreen(self)
            self.screen_contacts = ContactsScreen(self)
            self.screen_calls = CallsScreen(self)
            self.screen_plugins = PluginsScreen(self)
            self.screen_send = QuickSendScreen(self)
            self.screen_console = ConsoleScreen(self)
            self.screen_power = PowerScreen(self)
            self.screen_settings = SettingsScreen(self)

            self.sm.add_widget(self.screen_connect)
            self.sm.add_widget(self.screen_chats)
            self.sm.add_widget(self.screen_chatroom)
            self.sm.add_widget(self.screen_contacts)
            self.sm.add_widget(self.screen_calls)
            self.sm.add_widget(self.screen_power)
            self.sm.add_widget(self.screen_settings)
            self.add_widget(self.sm)

            # Build initial Tab Bar (Connect visible before pairing)
            self.update_tab_bar()

            # Seed bundled plugins into user plugin dir on first launch
            self.sync_bundled_plugins()

            # OSC Setup
            self.client = OSCClient(HOST, SVC_PORT, encoding="utf8")
            self.server = OSCThreadServer(encoding="utf8")
            try:
                self.server.listen(HOST, port=UI_PORT, default=True)
            except OSError as e:
                self.on_log(f"[WARN] UI_PORT {UI_PORT} already in use: {e}")
                try:
                    self.server.listen(HOST, port=UI_PORT + 10, default=True)
                except Exception:
                    pass

            ui = lambda fn: (lambda *a: Clock.schedule_once(lambda dt: fn(*a), 0))
            self.server.bind(b"/status", ui(self.on_status))
            self.server.bind(b"/engine_state", ui(self.on_engine_state))
            self.server.bind(b"/pair_code", ui(self.on_pair_code))
            self.server.bind(b"/plugins", ui(self.on_plugins))
            self.server.bind(b"/log", ui(self.on_log))
            self.server.bind(b"/qr", ui(self.on_qr))
            self.server.bind(b"/call_event", ui(self.on_call_event))
            self.server.bind(b"/call_history", ui(self.on_call_history))
            self.server.bind(b"/call_tracks", ui(self.on_call_tracks))
            self.server.bind(b"/sync_state", ui(self.on_sync_state))
            self.server.bind(b"/chat_updated", ui(self.on_chat_updated))
            self.server.bind(b"/contact_updated", ui(self.on_contact_updated))
            self.server.bind(b"/presence_update", ui(self.on_presence_update))
            self.server.bind(b"/privacy_state", ui(self.on_privacy_state))
            self.server.bind(b"/check_number_result", ui(self.on_check_number_result))
            self.server.bind(b"/yt_stream_status", ui(self.on_yt_stream_status))

            Clock.schedule_once(lambda dt: self.send("/hello"), 1.2)
        except Exception as e:
            log_exception(f"Root.__init__ failed: {traceback.format_exc()}")
            raise

    def update_tab_bar(self):
        """Dynamic tab bar: shows full WhatsApp client tabs once paired."""
        self.tab_bar.clear_widgets()
        self.tabs.clear()

        if self.is_connected:
            tab_defs = [
                ("chats", "Chats"),
                ("contacts", "Contacts"),
                ("calls", "Calls"),
                ("power", "Power"),
                ("settings", "Settings"),
            ]
            default_tab = "chats"
        else:
            tab_defs = [
                ("connect", "Connect"),
                ("power", "Power"),
                ("calls", "Calls"),
                ("settings", "Settings"),
            ]
            default_tab = "connect"

        for tab_id, tab_label in tab_defs:
            btn = TabButton(text=tab_label)
            btn.bind(on_release=lambda _, t=tab_id: self.switch_tab(t))
            self.tabs[tab_id] = btn
            self.tab_bar.add_widget(btn)

        cur = self.sm.current
        if cur == "chatroom" and self.is_connected:
            for tid, btn in self.tabs.items():
                btn.active = (tid == "chats")
        elif cur not in self.tabs:
            self.switch_tab(default_tab)
        else:
            self.switch_tab(cur)

    def switch_tab(self, tab_id):
        if tab_id in ("plugins", "send", "console"):
            self.sm.current = "power"
            self.screen_power.switch_subtab(tab_id)
            for tid, btn in self.tabs.items():
                btn.active = (tid == "power")
            return

        self.sm.current = tab_id
        for tid, btn in self.tabs.items():
            btn.active = (tid == tab_id)

        if tab_id == "chats":
            self.screen_chats.refresh(immediate=True)
        elif tab_id == "contacts":
            self.screen_contacts.refresh(immediate=True)

    def open_chat(self, jid: str):
        nj = normalize_jid(jid)
        if not nj:
            return
        self.screen_chatroom.load_chat(nj)
        self.sm.current = "chatroom"
        for tid, btn in self.tabs.items():
            btn.active = (tid == "chats")

    def on_master_toggle(self, *_):
        if self.engine_state == "running":
            self.send("/stop_bot")
        else:
            self.send("/start_bot")

    def on_engine_state(self, *state):
        st = str(state[0]).lower() if state else "running"
        self.engine_state = st
        if st == "running":
            self.btn_master.text = ICON_STOP
            self.btn_master.is_running = True
        elif st == "stopped":
            self.btn_master.text = ICON_PLAY
            self.btn_master.is_running = False
        elif st == "connecting":
            self.btn_master.text = ICON_REFRESH
            self.btn_master.is_running = True

    def on_status(self, *s):
        s_val = s[0] if s else ""
        s_str = str(s_val).upper()
        old_connected = self.is_connected

        if "DISCONNECTED" in s_str or "STOPPED" in s_str:
            self.is_connected = False
            self._base_pill_text = f"{ICON_DOT_OFF} OFFLINE"
            self._base_pill_color = "#EF4444"
        elif "READY_TO_PAIR" in s_str:
            self.is_connected = False
            self._base_pill_text = f"{ICON_DOT_ON} PAIRING"
            self._base_pill_color = "#F59E0B"
        elif "CONNECTED" in s_str:
            self.is_connected = True
            self._base_pill_text = f"{ICON_DOT_ON} CONNECTED"
            self._base_pill_color = "#10B981"
        elif "PAIR" in s_str:
            self.is_connected = True
            self._base_pill_text = f"{ICON_DOT_ON} PAIRED"
            self._base_pill_color = "#10B981"
        else:
            self.is_connected = False
            self._base_pill_text = f"{ICON_DOT_ON} {s_str[:12]}"
            self._base_pill_color = "#F59E0B"

        self.lbl_pill.text = self._base_pill_text
        self.lbl_pill.color = get_color_from_hex(self._base_pill_color)

        if old_connected != self.is_connected:
            self.update_tab_bar()

    def on_pair_code(self, *code):
        c = code[0] if code else ""
        self.screen_connect.set_code(str(c))

    def on_plugins(self, *p):
        pl = p[0] if p else ""
        self.screen_plugins.update_plugins(str(pl))

    def on_log(self, *text):
        txt = text[0] if text else ""
        self.screen_console.append_log(str(txt))

    def on_qr(self, *data):
        d = data[0] if data else ""
        self.screen_connect.set_qr(str(d))

    def on_call_event(self, *data):
        d = data[0] if data else "{}"
        self.screen_calls.on_call_event(str(d))

    def on_call_history(self, *data):
        d = data[0] if data else "[]"
        self.screen_calls.on_call_history(str(d))

    def on_call_tracks(self, *data):
        d = data[0] if data else "{}"
        self.screen_calls.on_call_tracks(str(d))

    def on_sync_state(self, *data):
        raw = str(data[0]) if data else ""
        state_str = raw
        detail = raw
        try:
            if raw.strip().startswith("{"):
                d = json.loads(raw)
                state_str = str(d.get("state") or d.get("status") or "")
                detail = str(d.get("detail") or d.get("message") or state_str or raw)
        except Exception:
            pass
        low = state_str.lower()
        is_syncing = ("sync" in low or "progress" in low or "fetch" in low) and not any(
            w in low for w in ("done", "complete", "idle", "ready", "finish")
        )
        if is_syncing:
            self.lbl_pill.text = f"{ICON_REFRESH} SYNCING"
            self.lbl_pill.color = get_color_from_hex("#38BDF8")
        else:
            self.lbl_pill.text = self._base_pill_text
            self.lbl_pill.color = get_color_from_hex(self._base_pill_color)

        self.screen_settings.on_sync_state(detail)
        self.screen_chats.schedule_refresh(0.15)
        self.screen_contacts.schedule_refresh(0.15)

    def on_chat_updated(self, *data):
        raw = str(data[0]) if data else ""
        jid = ""
        try:
            if raw.strip().startswith("{"):
                d = json.loads(raw)
                jid = normalize_jid(str(d.get("chat_id") or d.get("jid") or ""))
            elif raw.strip().lower() not in ("", "all", "sync", "*"):
                jid = normalize_jid(raw)
        except Exception:
            jid = ""
        self.screen_chats.schedule_refresh(0.1)
        if self.sm.current == "chatroom":
            self.screen_chatroom.on_chat_updated(jid)

    def on_contact_updated(self, *data):
        self.screen_contacts.schedule_refresh(0.15)
        self.screen_chats.schedule_refresh(0.15)
        if self.sm.current == "chatroom":
            self.screen_chatroom._update_header_meta()

    def on_presence_update(self, *data):
        jid = ""
        payload = {}
        try:
            raw = str(data[0]) if data else ""
            if raw.strip().startswith("{"):
                d = json.loads(raw)
                jid = normalize_jid(str(d.get("jid") or d.get("chat_id") or ""))
                if "online" in d or "is_online" in d:
                    payload["is_online"] = d.get("is_online", d.get("online"))
                if "typing" in d or "is_typing" in d:
                    payload["is_typing"] = d.get("is_typing", d.get("typing"))
                if "last_seen" in d or "last_seen_ts" in d:
                    payload["last_seen_ts"] = d.get("last_seen_ts", d.get("last_seen"))
            else:
                jid = normalize_jid(raw)
                if len(data) > 1:
                    st = str(data[1]).lower()
                    payload = {
                        "is_online": ("online" in st or "available" in st),
                        "is_typing": ("typing" in st or "composing" in st),
                    }
        except Exception:
            pass
        self.screen_chats.schedule_refresh(0.15)
        if self.sm.current == "chatroom":
            self.screen_chatroom.on_presence_update(jid, payload)

    def on_privacy_state(self, *data):
        raw = str(data[0]) if data else ""
        try:
            if raw.strip().startswith("{"):
                d = json.loads(raw)
                self.screen_settings.apply_privacy_state(d)
                if self.sm.current == "chatroom":
                    self.screen_chatroom._update_header_meta()
        except Exception as e:
            self.on_log(f"Privacy state parse error: {e}")

    def on_check_number_result(self, *data):
        self.screen_contacts.on_check_number_result(data)

    def on_yt_stream_status(self, *data):
        d = data[0] if data else ""
        self.screen_calls.on_yt_stream_status(str(d))
        if str(d).strip():
            self.on_log(f"[YT Stream] {d}")

    def send(self, addr, *vals):
        try:
            self.client.send_message(addr.encode(), list(vals) or [""])
        except Exception as e:
            self.on_log(f"OSC Send failed: {e}")

    def sync_bundled_plugins(self):
        """Ensure all bundled plugins are copied to user plugin directory for in-app editing."""
        try:
            target_dir = plugin_dir()
            app_dir = os.path.dirname(os.path.abspath(__file__))

            # 1. Unpack from bundled_plugins.json (impervious to p4a python stripping)
            bundle_json = os.path.join(app_dir, "bundled_plugins.json")
            if os.path.exists(bundle_json):
                try:
                    import json
                    with open(bundle_json, "r", encoding="utf-8") as fp:
                        b_dict = json.load(fp)
                    for name, code in b_dict.items():
                        dst = os.path.join(target_dir, f"{name}.py")
                        should_write = not os.path.exists(dst)
                        if not should_write and name == "ytcall" and "play_call_av_url" in code:
                            try:
                                with open(dst, "r", encoding="utf-8", errors="ignore") as cur_fp:
                                    cur_src = cur_fp.read()
                                if "sanatani-hackers/ytstream" in cur_src and "play_call_av_url" not in cur_src:
                                    should_write = True
                            except Exception:
                                pass
                        if should_write:
                            with open(dst, "w", encoding="utf-8") as fp:
                                fp.write(code)
                except Exception as e:
                    self.on_log(f"JSON plugin seed error: {e}")

            # 2. Also check file-based bundle directory
            bundle_dir = os.path.join(app_dir, "plugins")
            if os.path.isdir(bundle_dir):
                for f in os.listdir(bundle_dir):
                    if f.endswith(".py") and not f.startswith("_"):
                        dst = os.path.join(target_dir, f)
                        if not os.path.exists(dst):
                            shutil.copy2(os.path.join(bundle_dir, f), dst)
        except Exception as e:
            self.on_log(f"Plugin sync error: {e}")

    def get_plugin_source(self, name: str) -> str:
        """Read source code of any plugin (user folder first, fallback to JSON bundle)."""
        fname = f"{name}.py"
        # 1. Check user plugin dir (contains user edits)
        p1 = os.path.join(plugin_dir(), fname)
        if os.path.exists(p1):
            try:
                with open(p1, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception as e:
                return f"# Error reading {p1}: {e}\n"

        # 2. Check bundled_plugins.json
        app_dir = os.path.dirname(os.path.abspath(__file__))
        bundle_json = os.path.join(app_dir, "bundled_plugins.json")
        if os.path.exists(bundle_json):
            try:
                import json
                with open(bundle_json, "r", encoding="utf-8") as fp:
                    b_dict = json.load(fp)
                if name in b_dict:
                    return b_dict[name]
            except Exception:
                pass

        # 3. Check bundled plugins directory
        bundle_dir = os.path.join(app_dir, "plugins")
        p2 = os.path.join(bundle_dir, fname)
        if os.path.exists(p2):
            try:
                with open(p2, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception as e:
                return f"# Error reading {p2}: {e}\n"

        return f"# Plugin: {name}\nNAME = '{name}'\n\nasync def on_message(ctx, msg):\n    pass\n"

    def save_plugin(self, dialog, name, code):
        """Write modified plugin to storage and immediately hot-reload."""
        try:
            target = os.path.join(plugin_dir(), name)
            with open(target, "w", encoding="utf-8") as f:
                f.write(code)
            self.on_log(f"Plugin {name} saved successfully.")
            self.send("/reload")
            dialog.dismiss()
        except Exception as e:
            self.on_log(f"Failed to save {name}: {e}")


def configure_status_bar():
    """Ensure Android status bar and navigation bar are permanently visible and themed."""
    if platform != "android":
        return
    try:
        from jnius import autoclass
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        activity = PythonActivity.mActivity
        if not activity:
            return

        def _apply():
            try:
                window = activity.getWindow()
                WindowManager = autoclass("android.view.WindowManager")
                FLAG_FULLSCREEN = WindowManager.LayoutParams.FLAG_FULLSCREEN
                FLAG_FORCE_NOT_FULLSCREEN = WindowManager.LayoutParams.FLAG_FORCE_NOT_FULLSCREEN

                # Clear fullscreen flag so system status bar stays visible
                window.clearFlags(FLAG_FULLSCREEN)
                window.addFlags(FLAG_FORCE_NOT_FULLSCREEN)

                # Set dark background (#080C14) for seamless look
                Color = autoclass("android.graphics.Color")
                dark_color = Color.parseColor("#080C14")
                window.setStatusBarColor(dark_color)
                window.setNavigationBarColor(dark_color)

                # Ensure system UI doesn't hide status bar
                decor = window.getDecorView()
                sys_ui = decor.getSystemUiVisibility()
                # 4 = SYSTEM_UI_FLAG_FULLSCREEN, 2048 = SYSTEM_UI_FLAG_IMMERSIVE, 4096 = SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                decor.setSystemUiVisibility(sys_ui & ~(4 | 2048 | 4096))
            except Exception as ex:
                print(f"Status bar setup error: {ex}")

        try:
            from android.run_on_ui_thread import run_on_ui_thread
            run_on_ui_thread(_apply)()
        except Exception:
            _apply()
    except Exception as e:
        print(f"configure_status_bar error: {e}")


class WaApp(App):
    def build(self):
        try:
            ask_permissions()
        except Exception as e:
            log_exception(f"ask_permissions failed: {e}")
        try:
            start_service()
        except Exception as e:
            log_exception(f"start_service failed: {e}")
        try:
            configure_status_bar()
        except Exception as e:
            log_exception(f"configure_status_bar failed: {e}")
        self._root = Root()
        return self._root

    def on_start(self):
        try:
            configure_status_bar()
        except Exception:
            pass

    def on_resume(self):
        try:
            configure_status_bar()
        except Exception:
            pass
        try:
            root_w = getattr(self, "root", None) or getattr(self, "_root", None)
            if root_w:
                root_w.send("/app_lifecycle", "foreground")
                if root_w.sm.current == "chats":
                    root_w.screen_chats.schedule_refresh(0.05)
                elif root_w.sm.current == "chatroom":
                    root_w.screen_chatroom.schedule_refresh(0.05)
        except Exception:
            pass

    def on_pause(self):
        try:
            root_w = getattr(self, "root", None) or getattr(self, "_root", None)
            if root_w:
                root_w.send("/app_lifecycle", "background")
        except Exception:
            pass
        return True


if __name__ == "__main__":
    WaApp().run()
