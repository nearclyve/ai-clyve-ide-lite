"""Helper proses lintas platform.

Masalah yang diselesaikan: di Windows, `subprocess.run(..., shell=True)` dan
`git` tanpa flag akan membuat window console hitam yang muncul-sekilas lalu
hilang setiap kali agent menjalankan perintah. Selain jelek, itu memicu
flicker + lag di app windowed. Semua child process non-interaktif harus
memakai `CREATE_NO_WINDOW` + STARTUPINFO tersembunyi.
"""

from __future__ import annotations

import os
import subprocess

IS_WINDOWS = os.name == "nt"


def no_window_kwargs() -> dict:
    """kwargs untuk subprocess.run/Popen supaya tidak memunculkan console."""
    if not IS_WINDOWS:
        return {}
    si_cls = getattr(subprocess, "STARTUPINFO", None)
    if si_cls is None:  # interpreter non-Windows walau flag dipaksa
        return {"creationflags": 0x08000000}
    si = si_cls()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        "startupinfo": si,
    }
