"""Git helper untuk panel Changes (read + commit), stdlib saja.

Tidak memakai library git: cukup subprocess `git`, dan semua fungsi gagal
dengan tenang (return kosong) kalau git belum terpasang atau folder bukan repo.
Tidak ada operasi destruktif di sini: cuma status, diff, add, commit.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .proc import no_window_kwargs

MAX_DIFF_CHARS = 200_000


def _git(root: str | Path, *args: str, timeout: int = 20) -> tuple[int, str, str]:
    try:
        p = subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                           text=True, timeout=timeout, errors="replace",
                           **no_window_kwargs())
        return p.returncode, p.stdout or "", p.stderr or ""
    except FileNotFoundError:
        return 127, "", "git tidak terpasang"
    except subprocess.TimeoutExpired:
        return 124, "", "git timeout"
    except Exception as e:  # noqa: BLE001
        return 1, "", f"{type(e).__name__}: {e}"


def is_repo(root: str | Path) -> bool:
    rc, out, _ = _git(root, "rev-parse", "--is-inside-work-tree")
    return rc == 0 and out.strip() == "true"


def branch(root: str | Path) -> str:
    rc, out, _ = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    return out.strip() if rc == 0 else ""


def status(root: str | Path) -> list[dict]:
    """Daftar file berubah: [{path, code, staged, untracked}] — code gaya `git status --porcelain`."""
    if not is_repo(root):
        return []
    rc, out, _ = _git(root, "status", "--porcelain=v1", "-z", "-uall")
    if rc != 0:
        return []
    items: list[dict] = []
    parts = [p for p in out.split("\0") if p]
    i = 0
    while i < len(parts):
        raw = parts[i]
        i += 1
        if len(raw) < 4:
            continue
        xy, path = raw[:2], raw[3:]
        if xy[0] in "RC":  # rename/copy: path asal ada di entri berikutnya
            i += 1
        items.append({
            "path": path,
            "code": xy.strip() or "??",
            "staged": xy[0] not in " ?",
            "untracked": xy == "??",
            "deleted": "D" in xy,
        })
    items.sort(key=lambda d: d["path"])
    return items


def _untracked_diff(root: Path, path: str) -> str:
    """File baru belum dilacak: git diff kosong, jadi tampilkan isinya sebagai tambahan."""
    try:
        text = (root / path).read_text("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""
    lines = text.splitlines()[:400]
    body = "\n".join(f"+{ln}" for ln in lines)
    return f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n{body}"


def diff(root: str | Path, path: str | None = None, staged: bool = False) -> str:
    """Unified diff (worktree vs HEAD, atau vs index kalau staged=True)."""
    rootp = Path(root)
    if not is_repo(rootp):
        return ""
    args = ["diff", "--no-color", "--no-ext-diff", "--unified=3"]
    if staged:
        args.append("--cached")
    if path:
        args += ["--", path]
    rc, out, _ = _git(rootp, *args)
    if rc != 0:
        return ""
    if not out.strip() and path:
        info = next((d for d in status(rootp) if d["path"] == path), None)
        if info and info["untracked"]:
            out = _untracked_diff(rootp, path)
    return out[:MAX_DIFF_CHARS]


def files_with_diff(root: str | Path) -> list[dict]:
    """Status + jumlah baris +/- per file (untuk badge di header panel)."""
    rootp = Path(root)
    rows = status(rootp)
    for row in rows:
        d = diff(rootp, row["path"], staged=row["staged"])
        row["added"] = sum(1 for ln in d.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
        row["removed"] = sum(1 for ln in d.splitlines() if ln.startswith("-") and not ln.startswith("---"))
    return rows


def commit(root: str | Path, message: str, paths: list[str] | None = None) -> tuple[bool, str]:
    """Stage (semua atau paths tertentu) lalu commit. Return (ok, pesan untuk UI)."""
    msg = (message or "").strip()
    if not msg:
        return False, "commit message kosong"
    if not is_repo(root):
        return False, "folder ini bukan repo git"
    adds = ["add", "-A"] if not paths else ["add", "--", *paths]
    rc, _, err = _git(root, *adds)
    if rc != 0:
        return False, f"git add gagal: {err.strip()[:200]}"
    rc, out, err = _git(root, "commit", "-m", msg, timeout=60)
    if rc != 0:
        detail = (out + err).strip()
        if "nothing to commit" in detail:
            return False, "tidak ada perubahan untuk di-commit"
        return False, f"git commit gagal: {detail[:200]}"
    first = out.strip().splitlines()[0] if out.strip() else "commit dibuat"
    return True, first
