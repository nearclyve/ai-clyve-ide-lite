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
import os
import platform
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from .version import APP_ID, REPO, __version__, is_newer

API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
# Manifest di setiap rilis (asset latest.json). Ini jalur utama karena
# github.com tidak punya kuota 60 request/jam seperti api.github.com — 403
# rate-limit di API adalah penyebab "cek update gagal" yang paling sering.
DEFAULT_MANIFEST = f"https://github.com/{REPO}/releases/latest/download/latest.json"
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


def manifest_url() -> str:
    """URL latest.json alternatif (brand.json `update_url` atau env CLYVE_UPDATE_URL).

    Dipakai kalau source repo private: rilis + installer bisa ditaruh di host
    statis publik tanpa membuka source code.
    """
    env = os.environ.get("CLYVE_UPDATE_URL", "").strip()
    if env:
        return env
    try:
        from . import config
        brand = str(config.load_brand().get("update_url") or "").strip()
        if brand:
            return brand
    except Exception:  # noqa: BLE001
        pass
    return DEFAULT_MANIFEST


def _check_manifest(url: str, timeout: int) -> tuple[dict | None, str]:
    data = _get_json(url, timeout=timeout)
    if not data:
        status = _http_status(url, timeout=timeout)
        return None, f"manifest tidak bisa dibaca ({status}): {url}"
    latest = str(data.get("version") or "")
    if not latest or not is_newer(latest, __version__):
        return None, ""
    if not data.get("url"):
        return None, "manifest tidak punya field url"
    return {
        "version": latest.lstrip("vV"),
        "tag": latest if latest.lower().startswith("v") else f"v{latest}",
        "url": data["url"],
        "name": data.get("name") or f"{APP_ID}-Setup-{latest}.exe",
        "notes": (data.get("notes") or "").strip()[:1500],
        "published": data.get("published") or "",
        "size": int(data.get("size") or 0),
        "source": "manifest",
    }, ""


def _http_status(url: str, timeout: int = 12) -> str:
    """Status + alasan dari server (rate limit, private, proxy blokir)."""
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            raw = e.read(800).decode("utf-8", "replace")
            detail = str(json.loads(raw).get("message") or "") or raw[:160]
        except Exception:  # noqa: BLE001
            pass
        remaining = (e.headers or {}).get("X-RateLimit-Remaining") if e.headers else None
        reset = (e.headers or {}).get("X-RateLimit-Reset") if e.headers else None
        if remaining == "0" and reset and str(reset).isdigit():
            import time as _t
            when = _t.strftime("%H:%M", _t.localtime(int(reset)))
            detail = (detail + f" (kuota API GitHub habis, reset {when})").strip()
        return f"HTTP {e.code}" + (f": {detail}" if detail else "")
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {e}"


def check_detailed(timeout: int = 12) -> tuple[dict | None, str]:
    """(info_update | None, pesan_error). Error kosong berarti cek berhasil.

    Alasan error perlu ditampilkan: repo private membuat API GitHub membalas
    404, dan tanpa pesan ini app terlihat "tidak ada update" padahal updater
    tidak pernah bisa melihat rilis.
    """
    url = manifest_url()
    manifest_err = ""
    if url:
        info, err = _check_manifest(url, timeout)
        if info:
            return info, ""
        manifest_err = err
        if err and url != DEFAULT_MANIFEST:
            return None, err     # manifest custom (brand/env) gagal: jangan fallback

    rel = _get_json(API_LATEST, timeout=timeout)
    if not rel:
        status = _http_status(API_LATEST, timeout=timeout)
        hint = ""
        if "HTTP 404" in status:
            hint = " (repo private / rilis belum ada)"
        if "rate limit" in status.lower() or "kuota" in status.lower():
            hint = " (rate limit GitHub; manifest rilis juga gagal dibaca)"
        msg = f"GitHub API {status}{hint}"
        if manifest_err:
            msg += f" | {manifest_err}"
        return None, msg
    tag = str(rel.get("tag_name") or "")
    if not tag:
        return None, "respons GitHub tanpa tag_name"
    if not is_newer(tag, __version__):
        return None, ""
    asset = pick_asset(rel)
    if not asset:
        return None, f"rilis {tag} tidak punya asset .exe (installer belum ter-upload)"
    return {
        "version": tag.lstrip("vV"),
        "tag": tag,
        "url": asset.get("browser_download_url") or "",
        "name": asset.get("name") or f"{APP_ID}-Setup-{tag}.exe",
        "notes": (rel.get("body") or "").strip()[:1500],
        "published": rel.get("published_at") or "",
        "size": int(asset.get("size") or 0),
        "source": "github",
    }, ""


def check(timeout: int = 12) -> dict | None:
    """Return info update kalau ada versi lebih baru, else None."""
    return check_detailed(timeout=timeout)[0]


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
