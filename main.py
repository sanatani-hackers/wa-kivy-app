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
from kivy.factory import Factory
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

from common import (
    HOST,
    PACKAGE,
    SERVICE,
    SVC_PORT,
    UI_PORT,
    activity_log_path,
    export_activity_log,
    export_session_zip,
    get_ram_usage_mb,
    import_session_zip,
    plugin_dir,
    private_dir,
    qr_cache_path,
    stop_android_service,
)

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


# Modern Minimalistic Design Styles
KV_STYLES = """
#:import get_color_from_hex kivy.utils.get_color_from_hex

<Root>:
    orientation: 'vertical'
    canvas.before:
        Color:
            rgba: get_color_from_hex('#0B0F19')
        Rectangle:
            pos: self.pos
            size: self.size

<Card@BoxLayout>:
    orientation: 'vertical'
    padding: dp(14)
    spacing: dp(10)
    size_hint_y: None
    height: self.minimum_height
    canvas.before:
        Color:
            rgba: get_color_from_hex('#151D2A')
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(12)]
        Color:
            rgba: get_color_from_hex('#233044')
        Line:
            rounded_rectangle: [self.x, self.y, self.width, self.height, dp(12)]
            width: dp(1)

<DialogCard@BoxLayout>:
    orientation: 'vertical'
    padding: dp(16)
    spacing: dp(10)
    canvas.before:
        Color:
            rgba: get_color_from_hex('#151D2A')
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(14)]
        Color:
            rgba: get_color_from_hex('#233044')
        Line:
            rounded_rectangle: [self.x, self.y, self.width, self.height, dp(14)]
            width: dp(1)

<CodeBox@BoxLayout>:
    size_hint_y: None
    height: dp(54)
    padding: dp(8)
    spacing: dp(8)
    canvas.before:
        Color:
            rgba: get_color_from_hex('#0D131F')
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
    padding: dp(10)
    canvas.before:
        Color:
            rgba: get_color_from_hex('#0A0E17')
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(10)]
        Color:
            rgba: get_color_from_hex('#1E293B')
        Line:
            rounded_rectangle: [self.x, self.y, self.width, self.height, dp(10)]
            width: dp(1)

<StatusPill@BoxLayout>:
    size_hint: (1, 1)
    padding: [dp(10), dp(4), dp(10), dp(4)]
    canvas.before:
        Color:
            rgba: get_color_from_hex('#151D2A')
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(16)]

<MasterHeaderBtn@Button>:
    is_running: True
    background_color: (0, 0, 0, 0)
    background_normal: ''
    background_down: ''
    bold: True
    font_size: '14sp'
    size_hint: (None, None)
    size: (dp(54), dp(34))
    color: get_color_from_hex('#EF4444') if self.is_running else get_color_from_hex('#0B0F19')
    canvas.before:
        Color:
            rgba: (get_color_from_hex('#2A1818') if self.is_running else get_color_from_hex('#10B981')) if self.state != 'down' else get_color_from_hex('#374151')
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(17)]
        Color:
            rgba: get_color_from_hex('#EF4444') if self.is_running else get_color_from_hex('#059669')
        Line:
            rounded_rectangle: [self.x, self.y, self.width, self.height, dp(17)]
            width: dp(1)

<PrimaryButton@Button>:
    background_color: (0, 0, 0, 0)
    background_normal: ''
    background_down: ''
    color: get_color_from_hex('#0B0F19')
    bold: True
    font_size: '14sp'
    size_hint_y: None
    height: dp(46)
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
    color: get_color_from_hex('#F3F4F6')
    font_size: '13sp'
    size_hint_y: None
    height: dp(42)
    canvas.before:
        Color:
            rgba: get_color_from_hex('#2A384C') if self.state == 'down' else get_color_from_hex('#1F293A')
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
    color: get_color_from_hex('#FFFFFF')
    font_size: '13sp'
    size_hint_y: None
    height: dp(42)
    canvas.before:
        Color:
            rgba: get_color_from_hex('#DC2626') if self.state == 'down' else get_color_from_hex('#EF4444')
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(8)]

<ToggleActiveBtn@Button>:
    background_color: (0, 0, 0, 0)
    background_normal: ''
    background_down: ''
    color: get_color_from_hex('#0B0F19')
    bold: True
    font_size: '11sp'
    size_hint: (None, None)
    size: (dp(48), dp(34))
    canvas.before:
        Color:
            rgba: get_color_from_hex('#059669') if self.state == 'down' else get_color_from_hex('#10B981')
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(8)]

<ToggleInactiveBtn@Button>:
    background_color: (0, 0, 0, 0)
    background_normal: ''
    background_down: ''
    color: get_color_from_hex('#94A3B8')
    bold: True
    font_size: '11sp'
    size_hint: (None, None)
    size: (dp(48), dp(34))
    canvas.before:
        Color:
            rgba: get_color_from_hex('#2A384C') if self.state == 'down' else get_color_from_hex('#1F293A')
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(8)]
        Color:
            rgba: get_color_from_hex('#334155')
        Line:
            rounded_rectangle: [self.x, self.y, self.width, self.height, dp(8)]
            width: dp(1)

<SymbolEditBtn@Button>:
    background_color: (0, 0, 0, 0)
    background_normal: ''
    background_down: ''
    color: get_color_from_hex('#F3F4F6')
    font_size: '14sp'
    bold: True
    size_hint: (None, None)
    size: (dp(40), dp(34))
    canvas.before:
        Color:
            rgba: get_color_from_hex('#2A384C') if self.state == 'down' else get_color_from_hex('#1F293A')
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(8)]
        Color:
            rgba: get_color_from_hex('#334155')
        Line:
            rounded_rectangle: [self.x, self.y, self.width, self.height, dp(8)]
            width: dp(1)

<SymbolDeleteBtn@Button>:
    background_color: (0, 0, 0, 0)
    background_normal: ''
    background_down: ''
    color: get_color_from_hex('#EF4444')
    font_size: '14sp'
    bold: True
    size_hint: (None, None)
    size: (dp(40), dp(34))
    canvas.before:
        Color:
            rgba: get_color_from_hex('#451A1A') if self.state == 'down' else get_color_from_hex('#2A1818')
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(8)]
        Color:
            rgba: get_color_from_hex('#7F1D1D')
        Line:
            rounded_rectangle: [self.x, self.y, self.width, self.height, dp(8)]
            width: dp(1)

<ModernInput@TextInput>:
    background_color: (0, 0, 0, 0)
    background_normal: ''
    background_active: ''
    cursor_color: get_color_from_hex('#10B981')
    foreground_color: get_color_from_hex('#F3F4F6')
    hint_text_color: get_color_from_hex('#64748B')
    font_size: '14sp'
    padding: [dp(12), dp(12), dp(12), dp(12)]
    size_hint_y: None
    height: dp(46)
    multiline: False
    canvas.before:
        Color:
            rgba: get_color_from_hex('#0D131F')
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(8)]
        Color:
            rgba: get_color_from_hex('#233044')
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
            rgba: get_color_from_hex('#1A2433') if self.active else (0, 0, 0, 0)
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(8)]
        Color:
            rgba: get_color_from_hex('#10B981') if self.active else (0, 0, 0, 0)
        Line:
            points: [self.x + dp(6), self.y + dp(2), self.right - dp(6), self.y + dp(2)] if self.active else []
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
    """In-app Code Editor for installing/updating WhatsApp automation plugins."""

    def __init__(self, on_save, **kw):
        super().__init__(size_hint=(0.94, 0.90), auto_dismiss=True, **kw)
        self.on_save = on_save
        self.background_color = (0, 0, 0, 0.75)

        card = Factory.DialogCard()

        header = BoxLayout(size_hint_y=None, height=36)
        title = Label(text="Plugin Studio & Editor", font_size="17sp", bold=True,
                      color=get_color_from_hex("#F3F4F6"), halign="left", valign="middle")
        title.bind(size=title.setter('text_size'))
        header.add_widget(title)

        notice = Label(
            text="[color=10B981]Tip:[/color] Save installs and immediately activates the plugin via hot-reload.",
            markup=True, size_hint_y=None, height=24, font_size="11sp",
            color=get_color_from_hex("#94A3B8"), halign="left", valign="middle"
        )
        notice.bind(size=notice.setter('text_size'))

        # Filename input
        self.filename_input = Factory.ModernInput()
        self.filename_input.hint_text = "Filename (e.g. custom_bot.py)"

        # Template buttons row
        tmpl_row = BoxLayout(size_hint_y=None, height=34, spacing=6)
        btn_tmpl1 = Factory.SecondaryButton(text="Template: Echo")
        btn_tmpl1.height = 34
        btn_tmpl1.font_size = "11sp"
        btn_tmpl1.bind(on_release=lambda *_: self.load_template("echo"))

        btn_tmpl2 = Factory.SecondaryButton(text="Template: Responder")
        btn_tmpl2.height = 34
        btn_tmpl2.font_size = "11sp"
        btn_tmpl2.bind(on_release=lambda *_: self.load_template("responder"))

        btn_tmpl3 = Factory.SecondaryButton(text="Template: Remind")
        btn_tmpl3.height = 34
        btn_tmpl3.font_size = "11sp"
        btn_tmpl3.bind(on_release=lambda *_: self.load_template("remind"))

        tmpl_row.add_widget(btn_tmpl1)
        tmpl_row.add_widget(btn_tmpl2)
        tmpl_row.add_widget(btn_tmpl3)

        # Code editor area
        self.code_input = TextInput(
            background_color=get_color_from_hex('#0D131F'),
            foreground_color=get_color_from_hex('#F3F4F6'),
            cursor_color=get_color_from_hex('#10B981'),
            hint_text_color=get_color_from_hex('#475569'),
            font_size="12sp",
            hint_text="# Write Python code here\nNAME = 'my_plugin'\n\nasync def on_message(ctx, msg):\n    if msg.text == '!test':\n        await ctx.reply(msg, 'Working!')",
            multiline=True
        )

        # Action Buttons
        btn_bar = BoxLayout(size_hint_y=None, height=44, spacing=10)
        btn_save = Factory.PrimaryButton(text="Save & Install")
        btn_save.bind(on_release=self._save)

        btn_cancel = Factory.SecondaryButton(text="Cancel")
        btn_cancel.bind(on_release=lambda *_: self.dismiss())

        btn_bar.add_widget(btn_save)
        btn_bar.add_widget(btn_cancel)

        for w in (header, notice, self.filename_input, tmpl_row, self.code_input, btn_bar):
            card.add_widget(w)

        self.add_widget(card)

    def load_template(self, kind):
        if kind == "echo":
            self.filename_input.text = "custom_echo.py"
            self.code_input.text = (
                "NAME = 'custom_echo'\n\n"
                "async def on_message(ctx, msg):\n"
                "    if msg.text and msg.text.startswith('!say '):\n"
                "        await ctx.reply(msg, msg.text[5:])\n"
            )
        elif kind == "responder":
            self.filename_input.text = "keyword_reply.py"
            self.code_input.text = (
                "NAME = 'keyword_reply'\n\n"
                "async def on_message(ctx, msg):\n"
                "    if msg.text and 'help' in msg.text.lower():\n"
                "        await ctx.reply(msg, 'Need help? I am active 24/7!')\n"
            )
        elif kind == "remind":
            self.filename_input.text = "timer_alert.py"
            self.code_input.text = (
                "import asyncio\n"
                "NAME = 'timer_alert'\n\n"
                "async def on_message(ctx, msg):\n"
                "    if msg.text and msg.text.startswith('!pingme'):\n"
                "        await ctx.reply(msg, 'Timer started (10s)...')\n"
                "        await asyncio.sleep(10)\n"
                "        await ctx.reply(msg, 'Time is up!')\n"
            )

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
        super().__init__(size_hint=(0.88, 0.40), auto_dismiss=True, **kw)
        self.background_color = (0, 0, 0, 0.75)
        card = Factory.DialogCard()

        lbl_t = Label(text=title_text, font_size="16sp", bold=True,
                      color=get_color_from_hex("#F3F4F6"), size_hint_y=None, height=dp(30))
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
        content = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(14), size_hint_y=None)
        content.bind(minimum_height=content.setter("height"))

        # Card 1: Pairing with Phone Code
        self.card_pair = Factory.Card()
        lbl_pair_title = Label(
            text="Pair with Phone Number", font_size="15sp", bold=True,
            color=get_color_from_hex("#F3F4F6"), size_hint_y=None, height=dp(28), halign="left", valign="middle"
        )
        lbl_pair_title.bind(size=lbl_pair_title.setter('text_size'))

        self.lbl_pair_sub = Label(
            text="Enter full phone with country code (e.g. 919876543210):",
            font_size="11sp", color=get_color_from_hex("#94A3B8"),
            size_hint_y=None, height=dp(28), halign="left", valign="middle"
        )
        self.lbl_pair_sub.bind(size=self.lbl_pair_sub.setter('text_size'))

        self.input_phone = Factory.ModernInput()
        self.input_phone.hint_text = "Country code + phone (e.g. 9198xxxxxxxx)"
        self.input_phone.input_filter = "int"

        btn_get_code = Factory.PrimaryButton(text="Request 8-Digit Pairing Code")
        btn_get_code.bind(on_release=self.on_request_code)

        # Code display box
        self.code_box = Factory.CodeBox()
        self.lbl_code = Label(text="------", font_size="22sp", bold=True,
                              color=get_color_from_hex("#10B981"))
        self.btn_copy = Factory.SecondaryButton(text="Copy", size_hint=(None, None), size=(dp(80), dp(38)))
        self.btn_copy.font_size = "12sp"
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
            color=get_color_from_hex("#F3F4F6"), size_hint_y=None, height=dp(26), halign="left", valign="middle"
        )
        lbl_qr_title.bind(size=lbl_qr_title.setter('text_size'))
        self.lbl_qr_sub = Label(
            text="QR code will appear here when ready for link.",
            font_size="11sp", color=get_color_from_hex("#94A3B8"),
            size_hint_y=None, height=dp(28), halign="left", valign="middle"
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
            self.btn_copy.text = "Copied!"
            Clock.schedule_once(lambda dt: setattr(self.btn_copy, "text", "Copy"), 2)

    def set_qr(self, path_or_str):
        if os.path.exists(path_or_str):
            self.qr_image.source = path_or_str
            self.qr_image.reload()
            self.lbl_qr_sub.text = "Scan this code with WhatsApp > Linked Devices:"
        else:
            self.lbl_qr_sub.text = f"QR Payload: {path_or_str[:30]}..."


class PluginsScreen(Screen):
    """Manage installed automation plugins, toggle ON/OFF, and hot-reload."""

    def __init__(self, app_root, **kw):
        super().__init__(name="plugins", **kw)
        self.root = app_root

        layout = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(10))

        # Header with actions
        header_card = Factory.Card()
        row = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8))
        self.lbl_title = Label(
            text="Automations & Plugins", font_size="16sp", bold=True,
            color=get_color_from_hex("#F3F4F6"), halign="left", valign="middle"
        )
        self.lbl_title.bind(size=self.lbl_title.setter('text_size'))

        btn_add = Factory.PrimaryButton(text="+ Add", size_hint=(None, None), size=(dp(70), dp(38)))
        btn_add.bind(on_release=lambda *_: self.open_editor())

        btn_reload = Factory.SecondaryButton(text=ICON_REFRESH, size_hint=(None, None), size=(dp(46), dp(38)))
        btn_reload.font_size = "17sp"
        btn_reload.bold = True
        btn_reload.bind(on_release=lambda *_: self.root.send("/reload"))

        row.add_widget(self.lbl_title)
        row.add_widget(btn_add)
        row.add_widget(btn_reload)
        header_card.add_widget(row)
        layout.add_widget(header_card)

        # Plugins list scroll
        scroll = ScrollView(size_hint=(1, 1))
        self.list_box = BoxLayout(orientation="vertical", spacing=dp(10), size_hint_y=None)
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
        fname = f"{name}.py"
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
        self.lbl_title.text = f"Plugins ({active_count}/{len(plugins)} Active)"

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
            row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(8))

            lbl_name = Label(
                text=f"[b]{name}[/b]",
                markup=True,
                font_size="15sp",
                color=get_color_from_hex("#F3F4F6"),
                halign="left", valign="middle"
            )
            lbl_name.bind(size=lbl_name.setter('text_size'))
            row.add_widget(lbl_name)

            # Interactive ON / OFF toggle button
            if is_on:
                btn_toggle = Factory.ToggleActiveBtn(text="ON")
            else:
                btn_toggle = Factory.ToggleInactiveBtn(text="OFF")
            btn_toggle.bind(on_release=lambda _, n=name: self.root.send("/toggle_plugin", n))
            row.add_widget(btn_toggle)

            # In-app Code Editor symbol button (pencil icon)
            btn_edit = Factory.SymbolEditBtn(text=ICON_EDIT)
            btn_edit.bind(on_release=lambda _, n=name: self.edit_plugin(n))
            row.add_widget(btn_edit)

            # Delete symbol button (trash icon)
            btn_del = Factory.SymbolDeleteBtn(text=ICON_DELETE)
            btn_del.bind(on_release=lambda _, n=name: self.root.send("/delete_plugin", n))
            row.add_widget(btn_del)

            # Subtitle description
            sub_text = f"[color=10B981]{ICON_DOT_ON} Active & Handling Messages[/color]" if is_on else f"[color=64748B]{ICON_DOT_OFF} Disabled (Muted)[/color]"
            lbl_sub = Label(
                text=sub_text,
                markup=True,
                font_size="11sp",
                size_hint_y=None, height=dp(18),
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
        content = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(14), size_hint_y=None)
        content.bind(minimum_height=content.setter("height"))

        card = Factory.Card()
        lbl_title = Label(
            text="Quick Message Dispatch", font_size="16sp", bold=True,
            color=get_color_from_hex("#F3F4F6"), size_hint_y=None, height=dp(28), halign="left", valign="middle"
        )
        lbl_title.bind(size=lbl_title.setter('text_size'))

        lbl_desc = Label(
            text="Send a message directly through the bot's anti-spam queue to test connectivity.",
            font_size="11sp", color=get_color_from_hex("#94A3B8"),
            size_hint_y=None, height=dp(28), halign="left", valign="middle"
        )
        lbl_desc.bind(size=lbl_desc.setter('text_size'))

        self.input_target = Factory.ModernInput()
        self.input_target.hint_text = "Target Number or JID (e.g. 9198xxxxxxxx or 'self')"

        self.input_msg = Factory.ModernInput()
        self.input_msg.hint_text = "Message text..."
        self.input_msg.height = dp(72)
        self.input_msg.multiline = True

        btn_send = Factory.PrimaryButton(text="Queue Outbound Message")
        btn_send.bind(on_release=self.on_send)

        btn_ping_self = Factory.SecondaryButton(text=f"{ICON_PLAY} Ping Myself (Test Bot)")
        btn_ping_self.bind(on_release=lambda *_: self.root.send("/send", "self", "!ping"))

        self.lbl_feedback = Label(
            text="", font_size="12sp", color=get_color_from_hex("#10B981"),
            size_hint_y=None, height=24
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
            self.lbl_feedback.text = f"{ICON_CHECK} Dispatched to queue for {target}"
            self.input_msg.text = ""
            Clock.schedule_once(lambda dt: setattr(self.lbl_feedback, "text", ""), 4)
        else:
            self.lbl_feedback.color = get_color_from_hex("#EF4444")
            self.lbl_feedback.text = f"{ICON_WARNING} Please enter both target number and message."
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

        # Header - Pagination & Export Toolbar
        header = Factory.Card()
        row = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(6))

        title = Label(
            text="Console", font_size="15sp", bold=True,
            color=get_color_from_hex("#F3F4F6"), halign="left", valign="middle",
            size_hint_x=None, width=dp(65)
        )
        title.bind(size=title.setter('text_size'))

        btn_prev = Factory.SecondaryButton(text=ICON_PREV, size_hint=(None, None), size=(dp(36), dp(34)))
        btn_prev.bind(on_release=self.prev_page)

        self.lbl_page = Label(text="1/1", font_size="11sp", bold=True,
                              color=get_color_from_hex("#94A3B8"), size_hint_x=None, width=dp(42))

        btn_next = Factory.SecondaryButton(text=ICON_NEXT, size_hint=(None, None), size=(dp(36), dp(34)))
        btn_next.bind(on_release=self.next_page)

        self.btn_scroll = Factory.SecondaryButton(text="Auto: ON", size_hint=(None, None), size=(dp(72), dp(34)))
        self.btn_scroll.font_size = "10sp"
        self.btn_scroll.bind(on_release=self.toggle_scroll)

        btn_export = Factory.SecondaryButton(text="Export", size_hint=(None, None), size=(dp(54), dp(34)))
        btn_export.font_size = "10sp"
        btn_export.bind(on_release=self.export_logs)

        btn_clear = Factory.SecondaryButton(text="Clear", size_hint=(None, None), size=(dp(46), dp(34)))
        btn_clear.font_size = "10sp"
        btn_clear.bind(on_release=self.clear_logs)

        row.add_widget(title)
        row.add_widget(btn_prev)
        row.add_widget(self.lbl_page)
        row.add_widget(btn_next)
        row.add_widget(self.btn_scroll)
        row.add_widget(btn_export)
        row.add_widget(btn_clear)
        header.add_widget(row)
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

        scroll = ScrollView(size_hint=(1, 1))
        content = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(14), size_hint_y=None)
        content.bind(minimum_height=content.setter("height"))

        # Card 1: Ghost & Privacy Mode
        card_privacy = Factory.Card()
        lbl_p_title = Label(text="Ghost & Privacy Controls", font_size="15sp", bold=True,
                            color=get_color_from_hex("#F3F4F6"), size_hint_y=None, height=dp(26), halign="left")
        lbl_p_title.bind(size=lbl_p_title.setter('text_size'))

        # Row 1: Auto-Wipe Toggle
        row_wipe = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(8))
        lbl_wipe = Label(text="Auto-Wipe RAM Cache (24h)", font_size="13sp", color=get_color_from_hex("#F3F4F6"), halign="left", valign="middle")
        lbl_wipe.bind(size=lbl_wipe.setter('text_size'))
        self.btn_auto_wipe = Factory.ToggleActiveBtn(text="ON")
        self.btn_auto_wipe.bind(on_release=self.toggle_auto_wipe)
        row_wipe.add_widget(lbl_wipe)
        row_wipe.add_widget(self.btn_auto_wipe)

        # Row 2: Manual Flush button
        btn_flush = Factory.SecondaryButton(text=f"{ICON_BROOM} Wipe Intercepted Cache Now")
        btn_flush.bind(on_release=lambda *_: self.root.send("/wipe_cache"))

        # Row 3: Stealth Read Toggle
        row_stealth = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(8))
        lbl_stealth = Label(text="Stealth Mode (No Blue Ticks)", font_size="13sp", color=get_color_from_hex("#F3F4F6"), halign="left", valign="middle")
        lbl_stealth.bind(size=lbl_stealth.setter('text_size'))
        self.btn_stealth = Factory.ToggleActiveBtn(text="ON")
        self.btn_stealth.bind(on_release=self.toggle_stealth)
        row_stealth.add_widget(lbl_stealth)
        row_stealth.add_widget(self.btn_stealth)

        card_privacy.add_widget(lbl_p_title)
        card_privacy.add_widget(row_wipe)
        card_privacy.add_widget(btn_flush)
        card_privacy.add_widget(row_stealth)

        # Card 2: Engine & Service Lifecycle
        card_engine = Factory.Card()
        lbl_e_title = Label(text="Service & Power Management", font_size="15sp", bold=True,
                            color=get_color_from_hex("#F3F4F6"), size_hint_y=None, height=dp(26), halign="left")
        lbl_e_title.bind(size=lbl_e_title.setter('text_size'))

        btn_battery = Factory.SecondaryButton(text=f"{ICON_BOLT} Battery Exemption Setting")
        btn_battery.bind(on_release=lambda *_: ask_ignore_battery_optimizations())

        btn_kill_svc = Factory.DangerButton(text=f"{ICON_POWER} Terminate Background Service (Complete Offline)")
        btn_kill_svc.bind(on_release=self.terminate_service)

        card_engine.add_widget(lbl_e_title)
        card_engine.add_widget(btn_battery)
        card_engine.add_widget(btn_kill_svc)

        # Card 3: Session Backup & Restore
        card_session = Factory.Card()
        lbl_s_title = Label(text="Session Backup & Transfer", font_size="15sp", bold=True,
                            color=get_color_from_hex("#F3F4F6"), size_hint_y=None, height=dp(26), halign="left")
        lbl_s_title.bind(size=lbl_s_title.setter('text_size'))

        btn_export = Factory.SecondaryButton(text=f"{ICON_DOWNLOAD} Export Session (.zip)")
        btn_export.bind(on_release=self.export_session)

        lbl_import_hint = Label(text="Restore session from backup path:", font_size="11sp",
                                color=get_color_from_hex("#94A3B8"), size_hint_y=None, height=dp(22), halign="left")
        lbl_import_hint.bind(size=lbl_import_hint.setter('text_size'))

        self.input_import = Factory.ModernInput()
        self.input_import.hint_text = "/sdcard/Download/WA_Session_...zip"

        btn_import = Factory.SecondaryButton(text=f"{ICON_UPLOAD} Restore & Connect")
        btn_import.bind(on_release=self.import_session)

        card_session.add_widget(lbl_s_title)
        card_session.add_widget(btn_export)
        card_session.add_widget(lbl_import_hint)
        card_session.add_widget(self.input_import)
        card_session.add_widget(btn_import)

        # Card 4: Danger Zone (Logout)
        card_danger = Factory.Card()
        lbl_d_title = Label(text="Account Disconnect", font_size="15sp", bold=True,
                            color=get_color_from_hex("#EF4444"), size_hint_y=None, height=dp(26), halign="left")
        lbl_d_title.bind(size=lbl_d_title.setter('text_size'))

        btn_logout = Factory.DangerButton(text=f"{ICON_WARNING} Logout & Reset WhatsApp Session")
        btn_logout.bind(on_release=self.on_logout)

        card_danger.add_widget(lbl_d_title)
        card_danger.add_widget(btn_logout)

        content.add_widget(card_privacy)
        content.add_widget(card_engine)
        content.add_widget(card_session)
        content.add_widget(card_danger)

        scroll.add_widget(content)
        self.add_widget(scroll)

    def toggle_auto_wipe(self, *_):
        self.auto_wipe_active = not self.auto_wipe_active
        self.btn_auto_wipe.text = "ON" if self.auto_wipe_active else "OFF"
        self.root.on_log(f"Auto-wipe {'enabled' if self.auto_wipe_active else 'disabled'}.")

    def toggle_stealth(self, *_):
        self.stealth_read_active = not self.stealth_read_active
        self.btn_stealth.text = "ON" if self.stealth_read_active else "OFF"
        self.root.on_log(f"Stealth Read Mode {'enabled' if self.stealth_read_active else 'disabled'}.")

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
                ("send", "Quick Send"),
                ("console", "Console"),
                ("settings", f"{ICON_GEAR} Settings"),
            ]
            default_tab = "plugins"
        else:
            tab_defs = [
                ("connect", "Connect"),
                ("plugins", "Plugins"),
                ("send", "Quick Send"),
                ("console", "Console"),
                ("settings", f"{ICON_GEAR} Settings"),
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
        return Root()

    def on_pause(self):
        return True


if __name__ == "__main__":
    WaApp().run()
