"""Desktop window for Clyve IDE.

Order of preference:
1. pywebview  -> native window using the OS webview (WebView2 on Windows)
2. Edge/Chrome in --app mode -> borderless app window, no tabs or address bar
3. default browser -> last resort, still works

Only stdlib is required for modes 2 and 3. Mode 1 needs `pip install pywebview`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

APP_TITLE = "Clyve IDE"
IS_WINDOWS = sys.platform.startswith("win")

EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def _profile_dir() -> Path:
    """Dedicated browser profile so the app window has no tabs, extensions or history."""
    from . import config
    p = config.config_dir() / "window-profile"
    p.mkdir(parents=True, exist_ok=True)
    return p


def try_webview(url: str, on_close=None) -> bool:
    """Native window via pywebview. Returns False when unavailable or broken."""
    try:
        import webview  # type: ignore
    except Exception:  # noqa: BLE001  (not installed / no backend)
        return False
    try:
        win = webview.create_window(
            APP_TITLE, url,
            width=1280, height=860, min_size=(940, 620),
            background_color="#0b0d12",
            text_select=True,
        )
        if on_close is not None:
            win.events.closed += on_close
        webview.start()
        return True
    except Exception as e:  # noqa: BLE001  (no WebView2 runtime, no display, ...)
        print(f"[desktop] pywebview gagal: {e}")
        return False


def find_chromium() -> str | None:
    for p in EDGE_PATHS + CHROME_PATHS:
        if os.path.exists(p):
            return p
    for name in ("msedge", "chrome", "chromium", "google-chrome"):
        exe = shutil.which(name)
        if exe:
            return exe
    return None


def try_app_window(url: str) -> bool:
    """Chromium --app window: real OS window, no tab strip, no address bar."""
    exe = find_chromium()
    if not exe:
        return False
    args = [
        exe,
        f"--app={url}",
        "--window-size=1280,860",
        f"--user-data-dir={_profile_dir()}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate",
    ]
    try:
        proc = subprocess.Popen(args) if IS_WINDOWS else subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:  # noqa: BLE001
        print(f"[desktop] gagal buka app window: {e}")
        return False
    try:
        proc.wait()  # block until the window closes
    except KeyboardInterrupt:
        pass
    return True


def open_window(url: str, mode: str = "auto") -> str:
    """Open the UI and return which mode actually worked."""
    if mode in ("auto", "webview"):
        if try_webview(url):
            return "webview"
        if mode == "webview":
            print("[desktop] pywebview tidak tersedia, fallback ke browser window")
    if mode in ("auto", "app"):
        if try_app_window(url):
            return "app-window"
    webbrowser.open(url)
    return "browser"
