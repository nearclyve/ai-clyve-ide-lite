"""Generate version resource Windows untuk PyInstaller (--version-file).

Dijalankan otomatis oleh CI dan build_exe.ps1, supaya versi di
Properties -> Details exe selalu sama dengan clyve_ide/version.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clyve_ide.version import APP_ID, __version__  # noqa: E402

parts = list(__version__.split("."))
while len(parts) < 4:
    parts.append("0")
nums = ",".join(str(int(p) if p.isdigit() else 0) for p in parts[:4])

TPL = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({nums}),
    prodvers=({nums}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'Clyve'),
        StringStruct('FileDescription', 'AI Clyve IDE Lite'),
        StringStruct('FileVersion', '{__version__}'),
        StringStruct('InternalName', '{APP_ID}'),
        StringStruct('LegalCopyright', 'Clyve'),
        StringStruct('OriginalFilename', '{APP_ID}.exe'),
        StringStruct('ProductName', 'AI Clyve IDE Lite'),
        StringStruct('ProductVersion', '{__version__}')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def main() -> int:
    out_dir = ROOT / "platform_win"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / "version_info.txt"
    out.write_text(TPL, encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
