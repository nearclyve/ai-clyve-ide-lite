#!/usr/bin/env python3
"""AI Clyve IDE Lite launcher.

    python app.py                  # jendela desktop native (Qt)
    python app.py --ui web         # UI web + webview/browser
    python app.py --ui web --browser
    python app.py --no-window      # server saja (debug)
"""

from __future__ import annotations

import argparse
import sys

from clyve_ide import config


def main() -> int:
    ap = argparse.ArgumentParser(description="AI Clyve IDE Lite - local AI agent")
    ap.add_argument("--ui", choices=["qt", "web"], default="qt",
                    help="qt = desktop native (default), web = UI browser/webview")
    ap.add_argument("--port", type=int, default=0, help="0 = pick a free port (web only)")
    ap.add_argument("--browser", action="store_true", help="web UI: buka di browser biasa")
    ap.add_argument("--no-window", "--no-browser", dest="no_window", action="store_true",
                    help="web UI: jangan buka jendela, server saja")
    ap.add_argument("--mode", choices=["auto", "webview", "app", "browser"], default="auto",
                    help="web UI: jenis jendela")
    ap.add_argument("--workspace", "-w", help="workspace folder (persisted)")
    ap.add_argument("--key", "-k", help="Clyve API key (persisted)")
    ap.add_argument("--model", "-m")
    ap.add_argument("--base-url")
    args = ap.parse_args()

    cfg = config.load()
    changed = False
    if args.workspace:
        cfg["workspace"] = args.workspace; changed = True
    if args.key:
        cfg["api_key"] = args.key; changed = True
    if args.model:
        cfg["model"] = args.model; changed = True
    if args.base_url:
        cfg["base_url"] = args.base_url; changed = True
    if changed:
        config.save(cfg)

    if args.ui == "qt":
        try:
            from clyve_ide.qt_app import run as run_qt
        except ImportError as e:
            print(f"[qt] tidak tersedia ({e}). Install: pip install PySide6")
            print("[qt] fallback ke UI web")
        else:
            return run_qt(cfg)

    # ---------------- web UI path (kept for browser access / debugging)
    from clyve_ide.desktop import open_window
    from clyve_ide.server import start_server

    httpd, url = start_server(port=args.port)
    print("\n  AI Clyve IDE Lite (web UI)")
    print(f"  UI        : {url}")
    print(f"  config    : {config.CONFIG_PATH}")
    print(f"  workspace : {cfg.get('workspace')}")

    if args.no_window:
        print("  mode      : server only (Ctrl+C untuk stop)\n")
        try:
            while True:
                import time
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            httpd.shutdown(); httpd.server_close()
        return 0

    mode = "browser" if args.browser else args.mode
    if mode == "browser":
        import threading, webbrowser, time
        threading.Thread(target=lambda: (time.sleep(0.5), webbrowser.open(url)), daemon=True).start()
        print("  mode      : browser (Ctrl+C untuk stop)\n")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            httpd.shutdown(); httpd.server_close()
        return 0

    print(f"  mode      : {mode}\n")
    used = open_window(url, mode=mode)
    print(f"  window    : {used}")
    httpd.shutdown(); httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
