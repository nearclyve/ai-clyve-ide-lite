"""Speech-to-text via an OpenAI-compatible /audio/transcriptions endpoint.

Default: OpenRouter + openai/gpt-4o-mini-transcribe:floor (termurah yang terukur).
Config (settings.json):
    "stt": {"base_url": "https://openrouter.ai/api/v1",
            "model": "openai/gpt-4o-mini-transcribe:floor",
            "api_key": "sk-or-..."}
atau env OPENROUTER_API_KEY / STT_API_KEY.
"""

from __future__ import annotations

import json
import mimetypes
import os
import uuid
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-4o-mini-transcribe:floor"
MODELS = [
    "openai/gpt-4o-mini-transcribe:floor",
    "openai/gpt-4o-transcribe",
    "openai/whisper-1",
]


def settings(cfg: dict) -> dict:
    stt = dict(cfg.get("stt") or {})
    key = (stt.get("api_key") or os.environ.get("OPENROUTER_API_KEY")
           or os.environ.get("STT_API_KEY") or "")
    return {
        "base_url": (stt.get("base_url") or DEFAULT_BASE).rstrip("/"),
        "model": stt.get("model") or DEFAULT_MODEL,
        "api_key": key,
        "language": stt.get("language") or "",
    }


def _multipart(fields: dict, file_field: str, path: Path) -> tuple[bytes, str]:
    boundary = "----clyve" + uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        if v in (None, ""):
            continue
        out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
            f"filename=\"{path.name}\"\r\nContent-Type: {mime}\r\n\r\n").encode()
    out += path.read_bytes()
    out += f"\r\n--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def transcribe(wav_path: str | Path, cfg: dict, language: str | None = None) -> str:
    """Return transcript text. Raises RuntimeError with the server message on failure."""
    s = settings(cfg)
    if not s["api_key"]:
        raise RuntimeError("STT API key belum diisi (settings.json -> stt.api_key)")
    p = Path(wav_path)
    if not p.is_file() or p.stat().st_size < 100:
        raise RuntimeError("rekaman kosong")
    body, ctype = _multipart(
        {"model": s["model"], "language": language or s["language"], "response_format": "json"},
        "file", p)
    req = urllib.request.Request(
        s["base_url"] + "/audio/transcriptions", data=body,
        headers={"Authorization": f"Bearer {s['api_key']}", "Content-Type": ctype,
                 "HTTP-Referer": "https://ai.clyve.id", "X-Title": "AI Clyve"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        msg = e.read(600).decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {e.code}: {msg}") from None
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"{type(e).__name__}: {e}") from None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip()
    return (data.get("text") or "").strip()


def save_settings(cfg: dict, base_url: str, model: str, api_key: str) -> dict:
    cfg["stt"] = {"base_url": base_url.rstrip("/"), "model": model, "api_key": api_key}
    return cfg
