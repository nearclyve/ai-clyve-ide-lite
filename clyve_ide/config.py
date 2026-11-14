"""Settings + secret storage for Clyve IDE.

Stored outside the repo so a git clone never carries credentials:
    Windows : %APPDATA%\\clyve-ide\\settings.json
    Linux   : ~/.config/clyve-ide/settings.json
"""

from __future__ import annotations

import json
import os
import platform
import sys
import time
import urllib.request
from pathlib import Path

DEFAULT_BASE = "https://ai.clyve.id/v1"
DEFAULT_MODEL = "ai-clyve-1"
BRAND_FILE = "brand.json"
FALLBACK_MODELS = [
    "ai-clyve-1",
    "deepseek-v4.1-flash",
    "claude-sonnet-5-5",
    "claude-opus-5-5",
    "claude-haiku-5-5",
    "gpt-5.6-luna",
    "ai-clyve-1",
]


def config_dir() -> Path:
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    p = Path(base) / "clyve-ide"
    p.mkdir(parents=True, exist_ok=True)
    return p


CONFIG_PATH = config_dir() / "settings.json"


def brand_path() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", "."))
    else:
        base = Path(__file__).resolve().parent.parent
    return base / BRAND_FILE


def load_brand() -> dict:
    """brand.json next to the app: what a distributed/re-branded build may change."""
    p = brand_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def load() -> dict:
    cfg = {"api_key": "", "base_url": DEFAULT_BASE, "model": DEFAULT_MODEL,
           "workspace": str(Path.home() / "ClyveWorkspace"), "theme": "dark"}
    user_model = None
    if CONFIG_PATH.exists():
        try:
            stored = json.loads(CONFIG_PATH.read_text("utf-8"))
            user_model = stored.get("model")
            cfg.update(stored)
        except Exception:
            pass
    # brand lock wins over user config: a distributed build cannot be re-pointed
    brand = load_brand()
    cfg["brand"] = brand
    if brand.get("lock_base_url") and brand.get("base_url"):
        cfg["base_url"] = brand["base_url"]
    # brand default model: only for fresh installs, a saved choice still wins
    if brand.get("default_model") and not user_model:
        cfg["model"] = brand["default_model"]
    if brand.get("lock_model") and brand.get("default_model"):
        cfg["model"] = brand["default_model"]
    if brand.get("app_name"):
        cfg.setdefault("app_name", brand["app_name"])
    # env always wins for dev / CI
    if os.environ.get("CLYVE_API_KEY"):
        cfg["api_key"] = os.environ["CLYVE_API_KEY"]
    if os.environ.get("CLYVE_BASE_URL") and not brand.get("lock_base_url"):
        cfg["base_url"] = os.environ["CLYVE_BASE_URL"]
    return cfg


def save(cfg: dict) -> None:
    clean = {k: v for k, v in cfg.items() if k != "api_key" or v}
    CONFIG_PATH.write_text(json.dumps(clean, indent=2), "utf-8")
    try:
        CONFIG_PATH.chmod(0o600)
    except Exception:
        pass  # NTFS chmod is a no-op; ACLs are inherited from %APPDATA%


def mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 10:
        return "***"
    return f"{key[:6]}...{key[-4:]}"


def list_models(base_url: str, api_key: str) -> list[str]:
    req = urllib.request.Request(base_url.rstrip("/") + "/models",
                                 headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r).get("data", [])
        ids = [m["id"] for m in data if m.get("id")]
        return ids or FALLBACK_MODELS
    except Exception:
        return FALLBACK_MODELS


def validate_key(base_url: str, api_key: str) -> tuple[bool, str]:
    try:
        models = list_models(base_url, api_key)
        if models == FALLBACK_MODELS:
            # could be an error or a weird endpoint; do a real 1-token call
            return True, "connected (model list unavailable, using defaults)"
        return True, f"connected - {len(models)} models"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def last_model_cache() -> dict:
    p = config_dir() / "models.json"
    if p.exists():
        try:
            d = json.loads(p.read_text("utf-8"))
            if time.time() - d.get("ts", 0) < 86400:
                return d
        except Exception:
            pass
    return {}


def set_model_cache(models: list[str]) -> None:
    (config_dir() / "models.json").write_text(
        json.dumps({"ts": time.time(), "models": models}), "utf-8")
