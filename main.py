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
import os
import shutil
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
Window.softinput_mode = "below_target"
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
    plugin_dir,
    private_dir,
    qr_cache_path,
    stop_android_service,
)


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

<MasterHeaderBtn@Button>:
    is_running: True
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

<SwitchBtn@Button>:
    is_active: True
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

<ToggleActiveBtn@SwitchBtn>:
    is_active: True

<ToggleInactiveBtn@SwitchBtn>:
    is_active: False

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
class PluginEditorDialog(ModalView):
    """In-app Plugin Studio & Code Editor with real-time telemetry and clean layout."""

    def __init__(self, on_save, **kw):
        super().__init__(size_hint=(0.96, 0.94), auto_dismiss=True, **kw)
        self.on_save = on_save
        self.background_color = (0, 0, 0, 0.85)

        card = Factory.DialogCard()
        card.padding = dp(12)
        card.spacing = dp(8)

        # 1. Studio Header Toolbar: [Filename] [Snippets ▾] [Cancel] [Save]
        header = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(8))

        # Monospace Filename input pill
        self.filename_input = Factory.ModernInput(size_hint_x=0.45)
        self.filename_input.height = dp(38)
        self.filename_input.font_size = "12sp"
        self.filename_input.hint_text = "custom_bot.py"

        # Template/Snippets quick switcher
        self.btn_snippets = Factory.SecondaryButton(text="Snippets ▾", size_hint=(None, None), size=(dp(84), dp(38)))
        self.btn_snippets.font_size = "11sp"
        self.btn_snippets.bind(on_release=self.show_snippets_menu)

        btn_cancel = Factory.SecondaryButton(text="Cancel", size_hint=(None, None), size=(dp(64), dp(38)))
        btn_cancel.font_size = "11sp"
        btn_cancel.bind(on_release=lambda *_: self.dismiss())

        btn_save = Factory.PrimaryButton(text="Save & Reload", size_hint=(None, None), size=(dp(98), dp(38)))
        btn_save.font_size = "11sp"
        btn_save.bind(on_release=self._save)

        header.add_widget(self.filename_input)
        header.add_widget(self.btn_snippets)
        header.add_widget(btn_cancel)
        header.add_widget(btn_save)

        # 2. Code Editor Area (Full Space, Monospace, High-Contrast)
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

        # 3. Editor Telemetry Status Line (Quiet, informative, monospace)
        footer = BoxLayout(size_hint_y=None, height=dp(22), spacing=dp(8))
        self.lbl_telemetry = Label(
            text="Ln 1 | 0 B | Python 3 | Hot-Reload Ready",
            font_size="10sp",
            color=get_color_from_hex("#64748B"),
            halign="left", valign="middle"
        )
        self.lbl_telemetry.bind(size=self.lbl_telemetry.setter('text_size'))
        footer.add_widget(self.lbl_telemetry)

        card.add_widget(header)
        card.add_widget(self.code_input)
        card.add_widget(footer)
        self.add_widget(card)

    def _update_telemetry(self, *_):
        txt = self.code_input.text
        lines = len(txt.split("\n"))
        size_bytes = len(txt.encode("utf-8"))
        size_str = f"{size_bytes} B" if size_bytes < 1024 else f"{size_bytes / 1024:.1f} KB"
        self.lbl_telemetry.text = f"Ln {lines} | {size_str} | Python 3 | Hot-Reload Ready"

    def show_snippets_menu(self, *_):
        dialog = ModalView(size_hint=(0.84, 0.42), auto_dismiss=True)
        dcard = Factory.DialogCard()
        dcard.padding = dp(14)
        dcard.spacing = dp(8)
        lbl_stitle = Label(text="Insert Starter Template", font_size="14sp", bold=True,
                           color=get_color_from_hex("#F8FAFC"), size_hint_y=None, height=dp(26))
        dcard.add_widget(lbl_stitle)

        def _pick(kind):
            self.load_template(kind)
            dialog.dismiss()

        btn1 = Factory.SecondaryButton(text="Template: Echo Command (!say)")
        btn1.bind(on_release=lambda *_: _pick("echo"))
        btn2 = Factory.SecondaryButton(text="Template: Keyword Auto-Responder")
        btn2.bind(on_release=lambda *_: _pick("responder"))
        btn3 = Factory.SecondaryButton(text="Template: Async Timer & Alert (!pingme)")
        btn3.bind(on_release=lambda *_: _pick("remind"))

        for b in (btn1, btn2, btn3):
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
                "# Keyword automated responder\n"
                "NAME = 'keyword_reply'\n\n"
                "async def on_message(ctx, msg):\n"
                "    if msg.text and 'help' in msg.text.lower():\n"
                "        await ctx.reply(msg, 'Need help? I am active 24/7!')\n"
            )
        elif kind == "remind":
            if not self.filename_input.text:
                self.filename_input.text = "timer_alert.py"
            self.code_input.text = (
                "import asyncio\n\n"
                "# Asynchronous timed reminder\n"
                "NAME = 'timer_alert'\n\n"
                "async def on_message(ctx, msg):\n"
                "    if msg.text and msg.text.startswith('!pingme'):\n"
                "        await ctx.reply(msg, 'Timer scheduled (10s)...')\n"
                "        await asyncio.sleep(10)\n"
                "        await ctx.reply(msg, 'Time is up! Here is your alert.')\n"
            )
        self._update_telemetry()

    def _save(self, *_):
        name = self.filename_input.text.strip()
        code = self.code_input.text
        if not name:
            return
        if not name.endswith(".py"):
            name += ".py"
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

        btn_add = Factory.PrimaryButton(text="+ Add", size_hint=(None, None), size=(dp(68), dp(36)))
        btn_add.font_size = "12sp"
        btn_add.bind(on_release=lambda *_: self.open_editor())

        btn_reload = Factory.SecondaryButton(text=ICON_REFRESH, size_hint=(None, None), size=(dp(42), dp(36)))
        btn_reload.font_size = "15sp"
        btn_reload.bold = True
        btn_reload.bind(on_release=lambda *_: self.root.send("/reload"))

        row.add_widget(self.lbl_title)
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
            btn_toggle = Factory.ToggleActiveBtn(text="ON") if is_on else Factory.ToggleInactiveBtn(text="OFF")
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
        self.btn_stealth = Factory.SwitchBtn(text="ON", is_active=True)
        self.btn_stealth.bind(on_release=self.toggle_stealth)
        row_stealth.add_widget(lbl_stealth)
        row_stealth.add_widget(self.btn_stealth)

        # Row 2: Auto-Wipe RAM Cache
        row_wipe = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(8))
        lbl_wipe = Label(text="Auto-Wipe RAM Cache (24h retention)", font_size="13sp",
                         color=get_color_from_hex("#F8FAFC"), halign="left", valign="middle")
        lbl_wipe.bind(size=lbl_wipe.setter('text_size'))
        self.btn_auto_wipe = Factory.SwitchBtn(text="ON", is_active=True)
        self.btn_auto_wipe.bind(on_release=self.toggle_auto_wipe)
        row_wipe.add_widget(lbl_wipe)
        row_wipe.add_widget(self.btn_auto_wipe)

        # Row 3: Auto-Forward Deleted Messages to Self
        row_forward = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(8))
        lbl_forward = Label(text="Anti-Delete Relay to Saved Messages", font_size="13sp",
                            color=get_color_from_hex("#F8FAFC"), halign="left", valign="middle")
        lbl_forward.bind(size=lbl_forward.setter('text_size'))
        self.btn_forward = Factory.SwitchBtn(text="ON", is_active=True)
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

        btn_about_logs = Factory.SecondaryButton(text="Export Activity Log (.log)")
        btn_about_logs.bind(on_release=self.export_logs_from_about)

        card_about.add_widget(lbl_a_title)
        card_about.add_widget(lbl_meta1)
        card_about.add_widget(lbl_meta2)
        card_about.add_widget(lbl_meta3)
        card_about.add_widget(btn_about_logs)

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
            self.btn_master = Factory.MasterHeaderBtn(text=ICON_STOP)
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
        configure_status_bar()
        return Root()

    def on_start(self):
        configure_status_bar()

    def on_resume(self):
        configure_status_bar()

    def on_pause(self):
        return True


if __name__ == "__main__":
    WaApp().run()
