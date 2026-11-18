"""Kivy UI. Starts the foreground service, talks to it over localhost OSC."""
import os
from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.utils import platform
from oscpy.client import OSCClient
from oscpy.server import OSCThreadServer

from common import HOST, PACKAGE, SERVICE, SVC_PORT, UI_PORT, plugin_dir


def start_service():
    if platform != "android":
        return
    from jnius import autoclass
    activity = autoclass("org.kivy.android.PythonActivity").mActivity
    autoclass(f"{PACKAGE}.Service{SERVICE}").start(activity, "")


def ask_permissions():
    if platform != "android":
        return
    from android.permissions import request_permissions
    request_permissions(["android.permission.POST_NOTIFICATIONS"])      # Android 13+: shows the FGS notification


def ask_ignore_battery_optimizations():
    """Optional but recommended for 24/7: one-tap system dialog."""
    if platform != "android":
        return
    from jnius import autoclass
    Intent, Settings, Uri = (autoclass("android.content.Intent"),
                             autoclass("android.provider.Settings"),
                             autoclass("android.net.Uri"))
    act = autoclass("org.kivy.android.PythonActivity").mActivity
    i = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
    i.setData(Uri.parse("package:" + act.getPackageName()))
    act.startActivity(i)


class PluginDialog(Popup):
    def __init__(self, on_save, **kw):
        super().__init__(title="Install / Update Plugin", size_hint=(0.95, 0.8), **kw)
        layout = BoxLayout(orientation="vertical", spacing=8, padding=10)
        warn = Label(
            text="[color=ffcc00]Notice:[/color] Plugins have direct access to your session keys.\nOnly install plugins you trust.",
            markup=True, size_hint_y=None, height=44, font_size="13sp"
        )
        self.filename = TextInput(hint_text="Filename (e.g. echo.py)", multiline=False, size_hint_y=None, height=44)
        self.code = TextInput(hint_text="# Paste plugin Python code here\nNAME = 'my_plugin'\n\nasync def on_message(ctx, msg):\n    ...", multiline=True)
        btn_bar = BoxLayout(size_hint_y=None, height=44, spacing=8)
        save_btn = Button(text="Save & Reload", on_release=lambda *_: on_save(self, self.filename.text.strip(), self.code.text))
        close_btn = Button(text="Cancel", on_release=lambda *_: self.dismiss())
        btn_bar.add_widget(save_btn)
        btn_bar.add_widget(close_btn)
        for w in (warn, self.filename, self.code, btn_bar):
            layout.add_widget(w)
        self.content = layout


class Root(BoxLayout):
    def __init__(self, **kw):
        super().__init__(orientation="vertical", padding=12, spacing=8, **kw)
        self.status = Label(text="status: starting...", size_hint_y=None, height=40)
        self.phone = TextInput(hint_text="Phone with country code, e.g. 9198xxxxxxxx",
                               multiline=False, input_filter="int", size_hint_y=None, height=48)
        self.code = Label(text="", font_size="28sp", size_hint_y=None, height=60)
        row = BoxLayout(size_hint_y=None, height=48, spacing=8)
        row.add_widget(Button(text="Get pairing code", on_release=self.pair))
        row.add_widget(Button(text="Reload plugins", on_release=lambda *_: self.send("/reload")))
        row.add_widget(Button(text="+ Plugin", on_release=self.open_plugin_dialog))
        row.add_widget(Button(text="Battery", on_release=lambda *_: ask_ignore_battery_optimizations()))
        self.plugins = Label(text="plugins: -", size_hint_y=None, height=30)
        self.log = Label(text="", size_hint_y=None, halign="left", valign="top", markup=False)
        self.log.bind(texture_size=lambda w, s: setattr(w, "height", s[1]),
                      width=lambda w, v: setattr(w, "text_size", (v, None)))
        sv = ScrollView()
        sv.add_widget(self.log)
        for w in (self.status, self.phone, row, self.code, self.plugins, sv):
            self.add_widget(w)

        self.client = OSCClient(HOST, SVC_PORT, encoding="utf8")
        self.server = OSCThreadServer(encoding="utf8")
        self.server.listen(HOST, port=UI_PORT, default=True)
        ui = lambda fn: (lambda *a: Clock.schedule_once(lambda dt: fn(*a), 0))   # OSC thread -> UI thread
        self.server.bind(b"/status", ui(lambda s: setattr(self.status, "text", f"status: {s}")))
        self.server.bind(b"/pair_code", ui(lambda c: setattr(self.code, "text", c)))
        self.server.bind(b"/plugins", ui(lambda p: setattr(self.plugins, "text", f"plugins: {p or '-'}")))
        self.server.bind(b"/log", ui(self.add_log))
        self.server.bind(b"/qr", ui(lambda q: self.add_log(f"QR payload: {q[:60]}...")))
        Clock.schedule_once(lambda dt: self.send("/hello"), 1.5)      # ask the service to push state

    def open_plugin_dialog(self, *_):
        PluginDialog(self.save_plugin).open()

    def save_plugin(self, dialog, name, code):
        if not name:
            return
        if not name.endswith(".py"):
            name += ".py"
        try:
            target = os.path.join(plugin_dir(), name)
            with open(target, "w", encoding="utf-8") as f:
                f.write(code)
            self.add_log(f"Plugin {name} installed successfully.")
            self.send("/reload")
            dialog.dismiss()
        except Exception as e:
            self.add_log(f"Failed to install plugin: {e}")

    def send(self, addr, *vals):
        self.client.send_message(addr.encode(), list(vals) or [""])

    def pair(self, *_):
        if self.phone.text:
            self.send("/pair", self.phone.text.strip())

    def add_log(self, line):
        self.log.text = (self.log.text + "\n" + str(line))[-4000:]


class WaApp(App):
    def build(self):
        ask_permissions()
        start_service()
        return Root()

    def on_pause(self):
        return True                      # keep the Activity alive; the service is a separate process anyway


if __name__ == "__main__":
    WaApp().run()
