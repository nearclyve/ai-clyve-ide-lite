"""Cek update + unduh installer dari GitHub Releases.

Alur distribusi yang diasumsikan:
  tag `v1.2.0` -> release berisi `AIClyveIDELite-Setup-1.2.0.exe` (Inno Setup).
App membandingkan `clyve_ide.version.__version__` dengan tag rilis terbaru,
mengunduh installer ke temp, lalu menjalankannya dalam mode senyap. Installer
menutup app yang sedang jalan lewat Restart Manager (`CloseApplications=force`).

Tidak ada I/O jaringan saat modul di-import: semua fungsi dipanggil eksplisit.
"""

from __future__ import annotations

import json
import platform
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from .version import APP_ID, REPO, __version__, is_newer

API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
UA = f"{APP_ID}/{__version__} (+https://github.com/{REPO})"


def _get_json(url: str, timeout: int = 12) -> dict | None:
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": UA,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            json.JSONDecodeError, OSError):
        return None


def pick_asset(release: dict) -> dict | None:
    """Ambil asset installer Windows (.exe). Prioritas yang namanya mengandung 'Setup'."""
    assets = (release or {}).get("assets") or []
    exes = [a for a in assets if str(a.get("name", "")).lower().endswith(".exe")]
    for a in exes:
        if "setup" in a["name"].lower():
            return a
    return exes[0] if exes else None


def check(timeout: int = 12) -> dict | None:
    """Return dict info update kalau ada versi lebih baru, else None."""
    rel = _get_json(API_LATEST, timeout=timeout)
    if not rel:
        return None
    tag = str(rel.get("tag_name") or "")
    if not tag or not is_newer(tag, __version__):
        return None
    asset = pick_asset(rel)
    if not asset:
        return None
    return {
        "version": tag.lstrip("vV"),
        "tag": tag,
        "url": asset.get("browser_download_url") or "",
        "name": asset.get("name") or f"{APP_ID}-Setup-{tag}.exe",
        "notes": (rel.get("body") or "").strip()[:1500],
        "published": rel.get("published_at") or "",
        "size": int(asset.get("size") or 0),
    }


def download(url: str, progress=None, timeout: int = 180) -> Path:
    """Unduh installer ke temp. `progress(diterima, total)` dipanggil tiap chunk."""
    dest = Path(tempfile.gettempdir()) / (url.rsplit("/", 1)[-1] or f"{APP_ID}-Setup.exe")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if progress:
                progress(got, total)
    if dest.stat().st_size < 1024:
        raise RuntimeError("file installer tidak valid (terlalu kecil)")
    return dest


def launch_installer(path: str | Path) -> None:
    """Jalankan installer senyap. App harus segera exit setelah ini."""
    args = [str(path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
            "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"]
    if platform.system() == "Windows":
        subprocess.Popen(args, close_fds=True)
    else:  # dev di Linux/macOS: jangan benar-benar menjalankan exe Windows
        raise RuntimeError("installer Windows hanya bisa dijalankan di Windows")
