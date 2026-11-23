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

# Ensure window displays status bar and dynamically shifts up when software keyboard appears
Window.fullscreen = False
Window.borderless = False
Window.softinput_mode = ""
Window.keyboard_anim_args = {'d': 0.2, 't': 'in_out_quart'}
from kivy.lang import Builder
from kivy.properties import BooleanProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.screenmanager import FadeTransition, Screen, ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.utils import get_color_from_hex, platform
from oscpy.client import OSCClient
from oscpy.server import OSCThreadServer

APP_VERSION = "0.2.0"

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
    "antidelete", "antiviewonce", "autoreply", "echo", "ping", "remind", "stats"
}

PLUGIN_DESCRIPTIONS = {
    "antidelete": "Intercepts and recovers deleted messages & media",
    "antiviewonce": "Bypasses view-once restriction on photos & media",
    "autoreply": "Automated keyword replies and responder triggers",
    "echo": "Echo & broadcast testing tool (!say command)",
    "ping": "Heartbeat and bot response latency checker",
    "remind": "Task scheduler and timed reminder alerts",
    "stats": "Message traffic monitor and session telemetry",
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
from core import pip_hub, plugin_store, updater


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
    font_size: '12sp'
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
"""


class TabButton(Button):
    active = BooleanProperty(False)


class SwitchBtn(Button):
    is_active = BooleanProperty(True)


class MasterHeaderBtn(Button):
    is_running = BooleanProperty(True)


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
        dialog = ModalView(size_hint=(0.88, None), height=dp(300), auto_dismiss=True)
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

        for b in (btn1, btn2, btn3, btn4, btn5, btn6):
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


class SettingsScreen(Screen):
    """Configuration, Ghost Mode, Auto-Wipe, Service Shutdown, and Session Import/Export."""

    def __init__(self, app_root, **kw):
        super().__init__(name="settings", **kw)
        self.root = app_root
        self.auto_wipe_active = True
        self.stealth_read_active = True
        self.auto_forward_active = True

        scroll = ScrollView(size_hint=(1, 1))
        content = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(12), size_hint_y=None)
        content.bind(minimum_height=content.setter("height"))

        # ---------------------------------------------------- Card 1: Privacy & Stealth
        card_privacy = Factory.Card()
        card_privacy.padding = [dp(14), dp(12), dp(14), dp(12)]
        card_privacy.spacing = dp(8)

        lbl_p_title = Label(
            text="Privacy & Stealth", font_size="14sp", bold=True,
            color=get_color_from_hex("#F8FAFC"), size_hint_y=None, height=dp(24), halign="left"
        )
        lbl_p_title.bind(size=lbl_p_title.setter('text_size'))

        lbl_p_sub = Label(
            text="Local message interception, stealth receipts, and memory cache hygiene.",
            font_size="11sp", color=get_color_from_hex("#64748B"), size_hint_y=None, height=dp(20), halign="left"
        )
        lbl_p_sub.bind(size=lbl_p_sub.setter('text_size'))

        # Row 1: Stealth Read Mode
        row_stealth = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(8))
        lbl_stealth = Label(text="Stealth Mode (Suppress read receipts)", font_size="13sp",
                            color=get_color_from_hex("#F8FAFC"), halign="left", valign="middle")
        lbl_stealth.bind(size=lbl_stealth.setter('text_size'))
        self.btn_stealth = SwitchBtn(text="ON", is_active=True)
        self.btn_stealth.bind(on_release=self.toggle_stealth)
        row_stealth.add_widget(lbl_stealth)
        row_stealth.add_widget(self.btn_stealth)

        # Row 2: Auto-Wipe RAM Cache
        row_wipe = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(8))
        lbl_wipe = Label(text="Auto-Wipe RAM Cache (24h retention)", font_size="13sp",
                         color=get_color_from_hex("#F8FAFC"), halign="left", valign="middle")
        lbl_wipe.bind(size=lbl_wipe.setter('text_size'))
        self.btn_auto_wipe = SwitchBtn(text="ON", is_active=True)
        self.btn_auto_wipe.bind(on_release=self.toggle_auto_wipe)
        row_wipe.add_widget(lbl_wipe)
        row_wipe.add_widget(self.btn_auto_wipe)

        # Row 3: Auto-Forward Deleted Messages to Self
        row_forward = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(8))
        lbl_forward = Label(text="Anti-Delete Relay to Saved Messages", font_size="13sp",
                            color=get_color_from_hex("#F8FAFC"), halign="left", valign="middle")
        lbl_forward.bind(size=lbl_forward.setter('text_size'))
        self.btn_forward = SwitchBtn(text="ON", is_active=True)
        self.btn_forward.bind(on_release=self.toggle_forward)
        row_forward.add_widget(lbl_forward)
        row_forward.add_widget(self.btn_forward)

        # Action: Flush cache
        btn_flush = Factory.SecondaryButton(text="Flush Intercepted Message Cache Now")
        btn_flush.bind(on_release=self.on_flush_cache)

        card_privacy.add_widget(lbl_p_title)
        card_privacy.add_widget(lbl_p_sub)
        card_privacy.add_widget(row_stealth)
        card_privacy.add_widget(row_wipe)
        card_privacy.add_widget(row_forward)
        card_privacy.add_widget(btn_flush)

        # ---------------------------------------------------- Card 2: Session Backup & Transfer
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

        card_session.add_widget(lbl_s_title)
        card_session.add_widget(lbl_s_sub)
        card_session.add_widget(btn_pick)
        card_session.add_widget(btn_export)
        card_session.add_widget(lbl_manual)
        card_session.add_widget(row_manual)
        card_session.add_widget(btn_perm)

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

    def toggle_auto_wipe(self, *_):
        self.auto_wipe_active = not self.auto_wipe_active
        self.btn_auto_wipe.is_active = self.auto_wipe_active
        self.btn_auto_wipe.text = "ON" if self.auto_wipe_active else "OFF"
        self.root.on_log(f"Auto-wipe {'enabled' if self.auto_wipe_active else 'disabled'}.")

    def toggle_stealth(self, *_):
        self.stealth_read_active = not self.stealth_read_active
        self.btn_stealth.is_active = self.stealth_read_active
        self.btn_stealth.text = "ON" if self.stealth_read_active else "OFF"
        self.root.on_log(f"Stealth Read Mode {'enabled' if self.stealth_read_active else 'disabled'}.")

    def toggle_forward(self, *_):
        self.auto_forward_active = not self.auto_forward_active
        self.btn_forward.is_active = self.auto_forward_active
        self.btn_forward.text = "ON" if self.auto_forward_active else "OFF"
        self.root.on_log(f"Anti-Delete relay {'enabled' if self.auto_forward_active else 'disabled'}.")

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
        """Stream bytes from content:// URI into private storage using native file descriptor."""
        from jnius import autoclass
        cr = activity.getContentResolver()

        # 1. Resolve filename safely
        filename = "imported_session_backup"
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

        dest = os.path.join(private_dir(), filename)

        # 2. Kernel-level direct copy via ParcelFileDescriptor (fastest & 100% reliable)
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
        except Exception as e:
            self.root.on_log(f"pfd copy note: {e}")
            copied = False

        # 3. Native Android OS stream copy via android.os.FileUtils (Android 10+ / API 29+)
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
            except Exception as e:
                self.root.on_log(f"FileUtils copy note: {e}")
                copied = False

        if not os.path.exists(dest) or os.path.getsize(dest) == 0:
            raise RuntimeError("Selected file could not be read or is empty (0 bytes).")

        return dest

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
            self.lbl_pill = Label(text=f"{ICON_DOT_ON} CONNECTING", font_size="11sp", bold=True,
                                  color=get_color_from_hex("#F59E0B"))
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
            self.screen_plugins = PluginsScreen(self)
            self.screen_send = QuickSendScreen(self)
            self.screen_console = ConsoleScreen(self)
            self.screen_settings = SettingsScreen(self)

            self.sm.add_widget(self.screen_connect)
            self.sm.add_widget(self.screen_plugins)
            self.sm.add_widget(self.screen_send)
            self.sm.add_widget(self.screen_console)
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

            Clock.schedule_once(lambda dt: self.send("/hello"), 1.2)
        except Exception as e:
            log_exception(f"Root.__init__ failed: {traceback.format_exc()}")
            raise

    def update_tab_bar(self):
        """Dynamic tab bar: hides Connect tab once paired, shows Settings."""
        self.tab_bar.clear_widgets()
        self.tabs.clear()

        if self.is_connected:
            tab_defs = [
                ("plugins", "Plugins"),
                ("send", "Send"),
                ("console", "Console"),
                ("settings", "Settings"),
            ]
            default_tab = "plugins"
        else:
            tab_defs = [
                ("connect", "Connect"),
                ("plugins", "Plugins"),
                ("send", "Send"),
                ("console", "Console"),
                ("settings", "Settings"),
            ]
            default_tab = "connect"

        for tab_id, tab_label in tab_defs:
            btn = TabButton(text=tab_label)
            btn.bind(on_release=lambda _, t=tab_id: self.switch_tab(t))
            self.tabs[tab_id] = btn
            self.tab_bar.add_widget(btn)

        cur = self.sm.current
        if cur not in self.tabs:
            self.switch_tab(default_tab)
        else:
            self.switch_tab(cur)

    def switch_tab(self, tab_id):
        self.sm.current = tab_id
        for tid, btn in self.tabs.items():
            btn.active = (tid == tab_id)

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
            self.lbl_pill.text = f"{ICON_DOT_OFF} OFFLINE"
            self.lbl_pill.color = get_color_from_hex("#EF4444")
        elif "CONNECTED" in s_str:
            self.is_connected = True
            self.lbl_pill.text = f"{ICON_DOT_ON} CONNECTED"
            self.lbl_pill.color = get_color_from_hex("#10B981")
        elif "PAIR" in s_str:
            self.is_connected = True
            self.lbl_pill.text = f"{ICON_DOT_ON} PAIRED"
            self.lbl_pill.color = get_color_from_hex("#10B981")
        else:
            self.is_connected = False
            self.lbl_pill.text = f"{ICON_DOT_ON} {s_str[:12]}"
            self.lbl_pill.color = get_color_from_hex("#F59E0B")

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
                        if not os.path.exists(dst):
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
        return Root()

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

    def on_pause(self):
        return True


if __name__ == "__main__":
    WaApp().run()
