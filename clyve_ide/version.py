"""Satu sumber versi app.

Dibaca oleh: updater (cek rilis GitHub), build .exe (version resource Windows),
CI (nama installer + tag rilis). Jangan hardcode versi di tempat lain.
"""

from __future__ import annotations

__version__ = "1.1.0"

APP_ID = "AIClyveIDELite"
REPO = "nearclyve/ai-clyve-ide-lite"


def tuple_of(v: str) -> tuple[int, ...]:
    """'1.2.3' -> (1,2,3). Bagian non-angka diabaikan supaya tag 'v1.2.3-beta' tetap aman."""
    out: list[int] = []
    for part in v.strip().lstrip("vV").split("."):
        digits = ""
        for ch in part:
            if not ch.isdigit():
                break
            digits += ch
        out.append(int(digits) if digits else 0)
    return tuple(out) or (0,)


def is_newer(latest: str, current: str = __version__) -> bool:
    a, b = tuple_of(latest), tuple_of(current)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))
