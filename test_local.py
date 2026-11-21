#!/usr/bin/env python3
"""Local testing and verification CLI for WA Core.
Use this script inside Termux or desktop to test logic, verify dependencies,
or run the WhatsApp engine without compiling an APK.

Usage:
    python test_local.py             # Run all diagnostic & import checks
    python test_local.py --check     # Quick sanity check of environment & ports
    python test_local.py --service   # Run backend engine in foreground terminal
    python test_local.py --pair <nr> # Request 8-digit pairing code via OSC
    python test_local.py --status    # Ping running service for status
"""
import argparse
import os
import socket
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from common import HOST, SVC_PORT, UI_PORT, plugin_dir, private_dir


def check_port(port, label):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.bind((HOST, port))
            return False, f"[FREE] {label} (UDP {port})"
        except OSError:
            return True, f"[BUSY] {label} (UDP {port}) - Process/APK is active"


def run_checks():
    print("=" * 60)
    print("🔍 WA Core Local Environment & Integrity Check")
    print("=" * 60)

    print(f"• Python Version : {sys.version.split()[0]} ({sys.platform})")
    print(f"• App Directory  : {SCRIPT_DIR}")
    print(f"• Private Data   : {private_dir()}")
    print(f"• Plugins Dir    : {plugin_dir()}")

    print("\n[1/4] Checking Python Dependencies...")
    modules = [
        ("kivy", "Kivy Framework"),
        ("filetype", "Image MIME detection"),
        ("segno", "QR Code Generation"),
        ("oscpy", "OSC IPC Server/Client"),
        ("google.protobuf", "Protobuf runtime"),
        ("requests", "HTTP Requests"),
        ("phonenumbers", "Phone validation"),
        ("tqdm", "Progress bar helper"),
    ]
    all_ok = True
    for mod_name, desc in modules:
        try:
            __import__(mod_name)
            print(f"  ✓ {mod_name:<16} : OK ({desc})")
        except Exception as e:
            all_ok = False
            print(f"  ✗ {mod_name:<16} : FAILED ({e})")

    print("\n[2/4] Checking Core Architecture & Shims...")
    try:
        import core._shims
        import core.wa_client
        import service.main
        print("  ✓ core._shims       : Loaded")
        print("  ✓ core.wa_client    : Loaded")
        print("  ✓ service.main      : Loaded")
    except Exception as e:
        all_ok = False
        print(f"  ✗ Core imports failed: {e}")

    print("\n[3/4] Checking Native Go Shared Library (Neonize)...")
    try:
        import neonize._binder
        version = neonize._binder.gocode.GetVersion()
        print(f"  ✓ Neonize Engine    : LOADED (Version: {version.decode() if isinstance(version, bytes) else version})")
    except Exception as e:
        all_ok = False
        print(f"  ✗ Neonize loading failed: {e}")

    print("\n[4/4] Checking OSC Port Status...")
    ui_busy, ui_msg = check_port(UI_PORT, "UI Port")
    svc_busy, svc_msg = check_port(SVC_PORT, "Service Port")
    print(f"  • {ui_msg}")
    print(f"  • {svc_msg}")

    if svc_busy:
        print("\n💡 NOTE: Service port 38202 is currently active (Android APK or background service is running).")
    else:
        print("\n💡 Service port 38202 is free. You can run 'python test_local.py --service' to test the engine directly.")

    print("\n" + "=" * 60)
    if all_ok:
        print("✅ ALL SANITY CHECKS PASSED! Ready for local test or compilation.")
    else:
        print("⚠️ Some checks failed. Review errors above.")
    print("=" * 60)
    return all_ok


def run_service():
    print("🚀 Starting WhatsApp Core Service in foreground terminal...")
    print("Press Ctrl+C to stop.")
    import service.main
    service.main.main()


def ping_service():
    from oscpy.client import OSCClient
    from oscpy.server import OSCThreadServer

    responses = []
    server = OSCThreadServer(encoding="utf8")
    try:
        server.listen(HOST, port=UI_PORT + 5, default=True)
    except OSError:
        server.listen(HOST, port=0, default=True)

    server.bind(b"/status", lambda *s: responses.append(("status", s)))
    server.bind(b"/plugins", lambda *p: responses.append(("plugins", p)))
    server.bind(b"/log", lambda *l: responses.append(("log", l)))

    client = OSCClient(HOST, SVC_PORT, encoding="utf8")
    print("Sending /hello to service on port 38202...")
    client.send_message(b"/hello", [b""])

    time.sleep(1.5)
    server.terminate_server()
    if responses:
        print(f"✓ Response received from service: {responses}")
    else:
        print("✗ No response received. Is the service running?")


def pair_phone(phone):
    from oscpy.client import OSCClient
    from oscpy.server import OSCThreadServer

    phone = phone.strip().lstrip("+")
    print(f"📱 Requesting 8-digit pairing code for +{phone}...")

    got_code = []
    logs = []
    server = OSCThreadServer(encoding="utf8")
    try:
        server.listen(HOST, port=UI_PORT + 2, default=True)
    except OSError:
        server.listen(HOST, port=0, default=True)

    server.bind(b"/pair_code", lambda *c: got_code.append(str(c[0])))
    server.bind(b"/log", lambda *l: logs.append(str(l[0])))

    client = OSCClient(HOST, SVC_PORT, encoding="utf8")
    client.send_message(b"/pair", [phone.encode()])

    start = time.time()
    while time.time() - start < 15:
        if got_code:
            break
        if logs:
            while logs:
                print(f"  [Engine] {logs.pop(0)}")
        time.sleep(0.5)

    server.terminate_server()
    if got_code:
        print(f"\n🎉 8-Digit Pairing Code: >> {got_code[0]} <<")
    else:
        print("\n⏳ Pairing code timeout. Check logs above.")


def main():
    parser = argparse.ArgumentParser(description="WA Core Local Test CLI")
    parser.add_argument("--check", action="store_true", help="Run sanity checks")
    parser.add_argument("--service", action="store_true", help="Run service in foreground")
    parser.add_argument("--status", action="store_true", help="Ping running service")
    parser.add_argument("--pair", type=str, help="Request 8-digit pairing code for phone")
    args = parser.parse_args()

    if args.service:
        run_service()
    elif args.status:
        ping_service()
    elif args.pair:
        pair_phone(args.pair)
    else:
        run_checks()


if __name__ == "__main__":
    main()
