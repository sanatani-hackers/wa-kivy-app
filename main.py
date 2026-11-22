"""WA Core - Minimalistic Modern WhatsApp Automation Engine for Android.
Features:
- Pure Kivy Dark Design System (obsidian & emerald palette, rounded cards)
- 4-Tab ScreenManager: Connect, Plugins, Quick Send, Console
- Phone Pairing (with 1-tap clipboard copy) & Segno QR Code Viewer
- In-app Plugin Manager with Code Editor, Template Picker, and Delete/Reload
- Direct Message Dispatcher via Paced Anti-spam Outbound Queue
- Real-time Terminal Log Console with Auto-scroll
- Foreground Service integration over OSC
"""
import os
import time
import traceback

from kivy.metrics import dp, sp
from kivy.app import App
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
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

from common import HOST, PACKAGE, SERVICE, SVC_PORT, UI_PORT, plugin_dir, private_dir, qr_cache_path


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
        # In desktop / Termux local test mode: auto-spawn service if not already running
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
        request_permissions(["android.permission.POST_NOTIFICATIONS"])
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
        super().__init__(size_hint=(0.94, 0.88), auto_dismiss=True, **kw)
        self.on_save = on_save
        self.background_color = (0, 0, 0, 0.7)

        card = Factory.DialogCard()

        header = BoxLayout(size_hint_y=None, height=36)
        title = Label(text="Plugin Editor", font_size="17sp", bold=True,
                      color=get_color_from_hex("#F3F4F6"), halign="left")
        title.bind(size=title.setter('text_size'))
        header.add_widget(title)

        notice = Label(
            text="[color=10B981]Tip:[/color] Use [b]!prefix[/b] or check [b]msg.text[/b]. Plugins run with anti-spam throttle.",
            markup=True, size_hint_y=None, height=28, font_size="11sp",
            color=get_color_from_hex("#94A3B8"), halign="left"
        )
        notice.bind(size=notice.setter('text_size'))

        # Filename input
        self.filename_input = Factory.ModernInput()
        self.filename_input.hint_text = "Filename (e.g. hello.py)"

        # Template buttons row
        tmpl_row = BoxLayout(size_hint_y=None, height=32, spacing=6)
        btn_tmpl1 = Factory.SecondaryButton(text="Template: Echo")
        btn_tmpl1.height = 32
        btn_tmpl1.font_size = "11sp"
        btn_tmpl1.bind(on_release=lambda *_: self.load_template("echo"))

        btn_tmpl2 = Factory.SecondaryButton(text="Template: Responder")
        btn_tmpl2.height = 32
        btn_tmpl2.font_size = "11sp"
        btn_tmpl2.bind(on_release=lambda *_: self.load_template("responder"))

        tmpl_row.add_widget(btn_tmpl1)
        tmpl_row.add_widget(btn_tmpl2)

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
                "    if msg.text.startswith('!say '):\n"
                "        await ctx.reply(msg, msg.text[5:])\n"
            )
        elif kind == "responder":
            self.filename_input.text = "keyword_reply.py"
            self.code_input.text = (
                "NAME = 'keyword_reply'\n\n"
                "async def on_message(ctx, msg):\n"
                "    if 'price' in msg.text.lower():\n"
                "        await ctx.reply(msg, 'Our service is 100% free & open-source!')\n"
            )

    def _save(self, *_):
        name = self.filename_input.text.strip()
        code = self.code_input.text
        if not name:
            return
        if not name.endswith(".py"):
            name += ".py"
        self.on_save(self, name, code)


# --------------------------------------------------------------------------- Screens
class ConnectScreen(Screen):
    """Pairing with phone number, viewing live QR code, and session status."""

    def __init__(self, app_root, **kw):
        super().__init__(name="connect", **kw)
        self.root = app_root

        scroll = ScrollView(size_hint=(1, 1))
        content = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(14), size_hint_y=None)
        content.bind(minimum_height=content.setter("height"))

        # Card 1: Connection Status & Battery
        card_status = Factory.Card()
        row_status = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8))
        self.lbl_status = Label(text="STATUS: STARTING", font_size="13sp", bold=True,
                                color=get_color_from_hex("#10B981"), halign="left", valign="middle")
        self.lbl_status.bind(size=self.lbl_status.setter('text_size'))
        btn_battery = Factory.SecondaryButton(text="⚡ Battery Exemption", size_hint=(None, None), size=(dp(165), dp(38)))
        btn_battery.font_size = "11sp"
        btn_battery.bind(on_release=lambda *_: ask_ignore_battery_optimizations())
        row_status.add_widget(self.lbl_status)
        row_status.add_widget(btn_battery)

        desc = Label(
            text="Service runs in foreground (persistent 24/7 notification).",
            font_size="11sp", color=get_color_from_hex("#64748B"),
            size_hint_y=None, height=dp(24), halign="left", valign="middle"
        )
        desc.bind(size=desc.setter('text_size'))
        card_status.add_widget(row_status)
        card_status.add_widget(desc)

        # Card 2: Pairing with Phone Code
        card_pair = Factory.Card()
        lbl_pair_title = Label(text="Pair with Phone Number", font_size="15sp", bold=True,
                               color=get_color_from_hex("#F3F4F6"), size_hint_y=None, height=dp(28), halign="left", valign="middle")
        lbl_pair_title.bind(size=lbl_pair_title.setter('text_size'))
        
        self.lbl_pair_sub = Label(text="Enter full phone with country code (e.g. 919876543210):",
                                  font_size="11sp", color=get_color_from_hex("#94A3B8"),
                                  size_hint_y=None, height=dp(32), halign="left", valign="middle")
        self.lbl_pair_sub.bind(size=self.lbl_pair_sub.setter('text_size'))

        self.input_phone = Factory.ModernInput()
        self.input_phone.hint_text = "Country code + phone (e.g. 9198xxxxxxxx)"
        self.input_phone.input_filter = "int"

        btn_get_code = Factory.PrimaryButton(text="Request 8-Digit Pairing Code")
        btn_get_code.bind(on_release=self.on_request_code)

        # Code display box (styled clickable card)
        self.code_box = Factory.CodeBox()
        self.lbl_code = Label(text="------", font_size="22sp", bold=True,
                              color=get_color_from_hex("#10B981"))
        self.btn_copy = Factory.SecondaryButton(text="Copy", size_hint=(None, None), size=(dp(80), dp(38)))
        self.btn_copy.font_size = "12sp"
        self.btn_copy.bind(on_release=self.copy_code)
        self.code_box.add_widget(self.lbl_code)
        self.code_box.add_widget(self.btn_copy)

        card_pair.add_widget(lbl_pair_title)
        card_pair.add_widget(self.lbl_pair_sub)
        card_pair.add_widget(self.input_phone)
        card_pair.add_widget(btn_get_code)
        card_pair.add_widget(self.code_box)

        # Card 3: QR Code Section (Optional scan)
        self.card_qr = Factory.Card()
        lbl_qr_title = Label(text="Or Scan QR Code", font_size="15sp", bold=True,
                             color=get_color_from_hex("#F3F4F6"), size_hint_y=None, height=dp(26), halign="left", valign="middle")
        lbl_qr_title.bind(size=lbl_qr_title.setter('text_size'))
        self.lbl_qr_sub = Label(text="QR code will appear here when ready for link.",
                                font_size="11sp", color=get_color_from_hex("#94A3B8"),
                                size_hint_y=None, height=dp(32), halign="left", valign="middle")
        self.lbl_qr_sub.bind(size=self.lbl_qr_sub.setter('text_size'))
        self.qr_image = Image(size_hint_y=None, height=dp(220), fit_mode="contain")
        self.card_qr.add_widget(lbl_qr_title)
        self.card_qr.add_widget(self.lbl_qr_sub)
        self.card_qr.add_widget(self.qr_image)

        # Card 4: Session Control
        card_ctrl = Factory.Card()
        btn_clear = Factory.DangerButton(text="Reset & Clear Session")
        btn_clear.bind(on_release=lambda *_: self.root.send("/clear_session"))
        card_ctrl.add_widget(btn_clear)

        content.add_widget(card_status)
        content.add_widget(card_pair)
        content.add_widget(self.card_qr)
        content.add_widget(card_ctrl)

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

    def on_status_change(self, s_str):
        if "CONNECTED" in s_str:
            self.lbl_pair_sub.text = "✓ Device linked & active. To link another account, use Clear Session."
            self.lbl_code.text = "LINKED"
            self.lbl_qr_sub.text = "✓ Connected to WhatsApp servers"
        elif "DISCONNECTED" in s_str:
            self.lbl_pair_sub.text = "Enter full phone with country code (e.g. 919876543210):"
            self.lbl_code.text = "------"
            self.lbl_qr_sub.text = "QR code will appear here when ready for link."

    def set_qr(self, path_or_str):
        if os.path.exists(path_or_str):
            self.qr_image.source = path_or_str
            self.qr_image.reload()
            self.lbl_qr_sub.text = "Scan this code with WhatsApp > Linked Devices:"
        else:
            self.lbl_qr_sub.text = f"QR Payload: {path_or_str[:30]}..."


class PluginsScreen(Screen):
    """Manage installed automation plugins, add new ones, and hot-reload."""

    def __init__(self, app_root, **kw):
        super().__init__(name="plugins", **kw)
        self.root = app_root

        layout = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(10))

        # Header with actions
        header_card = Factory.Card()
        row = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8))
        self.lbl_title = Label(text="Automations & Plugins", font_size="16sp", bold=True,
                               color=get_color_from_hex("#F3F4F6"), halign="left")
        self.lbl_title.bind(size=self.lbl_title.setter('text_size'))

        btn_add = Factory.PrimaryButton(text="+ Add", size_hint=(None, None), size=(dp(75), dp(38)))
        btn_add.bind(on_release=lambda *_: self.open_editor())

        btn_reload = Factory.SecondaryButton(text="🔄 Reload", size_hint=(None, None), size=(dp(90), dp(38)))
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

    def update_plugins(self, plugin_names):
        self.list_box.clear_widgets()
        names = [n.strip() for n in plugin_names.split(",") if n.strip()]
        self.lbl_title.text = f"Plugins ({len(names)} Active)"

        if not names:
            empty_card = Factory.Card()
            empty_card.add_widget(Label(text="No plugins loaded. Tap '+ Add' to create one.",
                                        color=get_color_from_hex("#64748B"), size_hint_y=None, height=dp(40)))
            self.list_box.add_widget(empty_card)
            return

        for name in names:
            p_card = Factory.Card()
            row = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8))
            
            lbl_name = Label(text=f"🧩  {name}", font_size="14sp", bold=True,
                             color=get_color_from_hex("#F3F4F6"), halign="left", valign="middle")
            lbl_name.bind(size=lbl_name.setter('text_size'))

            badge = Label(text="ACTIVE", font_size="10sp", bold=True,
                          color=get_color_from_hex("#10B981"), size_hint_x=None, width=dp(54), valign="middle")
            badge.bind(size=badge.setter('text_size'))

            btn_del = Factory.DangerButton(text="Del", size_hint=(None, None), size=(dp(58), dp(34)))
            btn_del.font_size = "11sp"
            btn_del.bind(on_release=lambda _, n=name: self.root.send("/delete_plugin", n))

            row.add_widget(lbl_name)
            row.add_widget(badge)
            row.add_widget(btn_del)
            p_card.add_widget(row)
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
        lbl_title = Label(text="Quick Message Dispatch", font_size="16sp", bold=True,
                          color=get_color_from_hex("#F3F4F6"), size_hint_y=None, height=dp(28), halign="left", valign="middle")
        lbl_title.bind(size=lbl_title.setter('text_size'))

        lbl_desc = Label(text="Send a message directly through the bot's anti-spam queue to test connectivity.",
                         font_size="11sp", color=get_color_from_hex("#94A3B8"),
                         size_hint_y=None, height=dp(32), halign="left", valign="middle")
        lbl_desc.bind(size=lbl_desc.setter('text_size'))

        self.input_target = Factory.ModernInput()
        self.input_target.hint_text = "Target Number or JID (e.g. 9198xxxxxxxx)"

        self.input_msg = Factory.ModernInput()
        self.input_msg.hint_text = "Message text..."
        self.input_msg.height = dp(72)
        self.input_msg.multiline = True

        btn_send = Factory.PrimaryButton(text="Queue Outbound Message")
        btn_send.bind(on_release=self.on_send)

        self.lbl_feedback = Label(text="", font_size="12sp", color=get_color_from_hex("#10B981"),
                                  size_hint_y=None, height=24)

        card.add_widget(lbl_title)
        card.add_widget(lbl_desc)
        card.add_widget(self.input_target)
        card.add_widget(self.input_msg)
        card.add_widget(btn_send)
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
            self.lbl_feedback.text = f"✓ Dispatched to queue for {target}"
            self.input_msg.text = ""
            Clock.schedule_once(lambda dt: setattr(self.lbl_feedback, "text", ""), 4)
        else:
            self.lbl_feedback.color = get_color_from_hex("#EF4444")
            self.lbl_feedback.text = "⚠️ Please enter both target number and message."
            Clock.schedule_once(lambda dt: setattr(self.lbl_feedback, "text", ""), 3)


class ConsoleScreen(Screen):
    """Live terminal logs from foreground service and plugins."""

    def __init__(self, app_root, **kw):
        super().__init__(name="console", **kw)
        self.root = app_root

        layout = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(10))

        # Header
        header = Factory.Card()
        row = BoxLayout(size_hint_y=None, height=36, spacing=8)
        title = Label(text="Activity Console", font_size="16sp", bold=True,
                      color=get_color_from_hex("#F3F4F6"), halign="left", valign="middle")
        title.bind(size=title.setter('text_size'))

        btn_clear = Factory.SecondaryButton(text="Clear", size_hint=(None, None), size=(dp(75), dp(36)))
        btn_clear.bind(on_release=lambda *_: setattr(self.log_label, "text", ""))

        row.add_widget(title)
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

    def append_log(self, text):
        t_str = time.strftime("%H:%M:%S")
        line = f"[{t_str}] {text}"
        current = self.log_label.text
        updated = (current + "\n" + line)[-6000:]
        self.log_label.text = updated
        Clock.schedule_once(lambda dt: setattr(self.scroll, "scroll_y", 0), 0.05)


# --------------------------------------------------------------------------- Root Layout
class Root(BoxLayout):
    def __init__(self, **kw):
        try:
            super().__init__(**kw)

            # Top Bar
            top_bar = BoxLayout(size_hint_y=None, height=dp(56), padding=[dp(14), dp(6), dp(14), dp(6)], spacing=dp(10))
            self.title_lbl = Label(text="WA CORE", font_size="18sp", bold=True,
                                   color=get_color_from_hex("#F3F4F6"), halign="left", valign="middle",
                                   size_hint_x=None, width=dp(110))
            self.title_lbl.bind(size=self.title_lbl.setter('text_size'))

            # Live Status Pill
            self.status_pill = Factory.StatusPill()
            self.lbl_pill = Label(text="● CONNECTING", font_size="11sp", bold=True,
                                  color=get_color_from_hex("#F59E0B"))
            self.status_pill.add_widget(self.lbl_pill)

            top_bar.add_widget(self.title_lbl)
            top_bar.add_widget(self.status_pill)
            self.add_widget(top_bar)

            # Tab Navigation Row
            self.tab_bar = BoxLayout(size_hint_y=None, height=dp(46), padding=[dp(8), dp(2), dp(8), dp(4)], spacing=dp(6))
            self.tabs = {}
            for tab_id, tab_label in [("connect", "Connect"), ("plugins", "Plugins"),
                                      ("send", "Quick Send"), ("console", "Console")]:
                btn = TabButton(text=tab_label)
                btn.bind(on_release=lambda _, t=tab_id: self.switch_tab(t))
                self.tabs[tab_id] = btn
                self.tab_bar.add_widget(btn)
            self.add_widget(self.tab_bar)

            # Screen Manager
            self.sm = ScreenManager(transition=FadeTransition(duration=0.15))
            self.screen_connect = ConnectScreen(self)
            self.screen_plugins = PluginsScreen(self)
            self.screen_send = QuickSendScreen(self)
            self.screen_console = ConsoleScreen(self)

            self.sm.add_widget(self.screen_connect)
            self.sm.add_widget(self.screen_plugins)
            self.sm.add_widget(self.screen_send)
            self.sm.add_widget(self.screen_console)
            self.add_widget(self.sm)

            # Default Active Tab
            self.switch_tab("connect")

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
            self.server.bind(b"/pair_code", ui(self.on_pair_code))
            self.server.bind(b"/plugins", ui(self.on_plugins))
            self.server.bind(b"/log", ui(self.on_log))
            self.server.bind(b"/qr", ui(self.on_qr))

            Clock.schedule_once(lambda dt: self.send("/hello"), 1.2)
        except Exception as e:
            log_exception(f"Root.__init__ failed: {traceback.format_exc()}")
            raise

    def switch_tab(self, tab_id):
        self.sm.current = tab_id
        for tid, btn in self.tabs.items():
            btn.active = (tid == tab_id)

    def on_status(self, *s):
        s_val = s[0] if s else ""
        s_str = str(s_val).upper()
        if "CONNECTED" in s_str:
            self.lbl_pill.text = "● CONNECTED"
            self.lbl_pill.color = get_color_from_hex("#10B981")
            self.screen_connect.lbl_status.color = get_color_from_hex("#10B981")
        elif "PAIR" in s_str:
            self.lbl_pill.text = "● PAIRED"
            self.lbl_pill.color = get_color_from_hex("#10B981")
            self.screen_connect.lbl_status.color = get_color_from_hex("#10B981")
        elif "DISCONNECTED" in s_str:
            self.lbl_pill.text = "● DISCONNECTED"
            self.lbl_pill.color = get_color_from_hex("#EF4444")
            self.screen_connect.lbl_status.color = get_color_from_hex("#EF4444")
        else:
            self.lbl_pill.text = f"● {s_str[:16]}"
            self.lbl_pill.color = get_color_from_hex("#F59E0B")
            self.screen_connect.lbl_status.color = get_color_from_hex("#F59E0B")
        self.screen_connect.lbl_status.text = f"STATUS: {s_str}"
        self.screen_connect.on_status_change(s_str)

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

    def save_plugin(self, dialog, name, code):
        try:
            target = os.path.join(plugin_dir(), name)
            with open(target, "w", encoding="utf-8") as f:
                f.write(code)
            self.on_log(f"Plugin {name} installed successfully.")
            self.send("/reload")
            dialog.dismiss()
        except Exception as e:
            self.on_log(f"Failed to install {name}: {e}")


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
