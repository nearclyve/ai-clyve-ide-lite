"""Snapshot-based checkpoints: roll back whatever the agent changed.

Layout under <config-dir>/checkpoints/<chat>/:
    <id>/manifest.json      {"id","label","ts","files":[{"path","existed","index"}]}
    <id>/files/<index>      raw copy of the previous contents
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path


class CheckpointStore:
    def __init__(self, root: Path, chat: str = "default"):
        self.root = Path(root) / (chat or "default")
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ create
    def create(self, label: str, paths: list[str]) -> dict | None:
        """Snapshot each path before it is touched. Returns the manifest."""
        entries, blobs = [], []
        seen = set()
        for raw in paths:
            if not raw or raw in seen:
                continue
            seen.add(raw)
            p = Path(raw)
            idx = len(entries)
            existed = p.is_file()
            entry = {"path": str(p), "existed": existed, "index": idx}
            if existed:
                try:
                    blobs.append((idx, p.read_bytes()))
                except OSError:
                    entry["existed"] = False
            entries.append(entry)
        if not entries:
            return None
        cid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        folder = self.root / cid
        (folder / "files").mkdir(parents=True, exist_ok=True)
        for idx, data in blobs:
            (folder / "files" / str(idx)).write_bytes(data)
        manifest = {"id": cid, "label": label, "ts": time.time(), "files": entries}
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=1), "utf-8")
        self._prune()
        return manifest

    # ------------------------------------------------------------------ read
    def list(self, limit: int = 40) -> list[dict]:
        out = []
        for m in sorted(self.root.glob("*/manifest.json"), reverse=True)[:limit]:
            try:
                d = json.loads(m.read_text("utf-8"))
            except Exception:  # noqa: BLE001
                continue
            out.append({"id": d.get("id"), "label": d.get("label"),
                        "ts": d.get("ts"), "count": len(d.get("files") or [])})
        out.sort(key=lambda c: c.get("ts") or 0, reverse=True)
        return out

    # ------------------------------------------------------------------ restore
    def restore(self, cid: str) -> tuple[bool, str]:
        folder = self.root / cid
        man = folder / "manifest.json"
        if not man.exists():
            return False, "checkpoint tidak ditemukan"
        d = json.loads(man.read_text("utf-8"))
        restored, removed = 0, 0
        for f in d.get("files") or []:
            p = Path(f["path"])
            if f.get("existed"):
                try:
                    data = (folder / "files" / str(f["index"])).read_bytes()
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(data)
                    restored += 1
                except OSError as e:
                    return False, f"gagal restore {p}: {e}"
            else:
                try:
                    if p.exists():
                        p.unlink()
                        removed += 1
                except OSError:
                    pass
        return True, f"{restored} file dipulihkan, {removed} file baru dihapus"

    # ------------------------------------------------------------------ misc
    def _prune(self, keep: int = 120) -> None:
        folders = sorted([p for p in self.root.iterdir() if p.is_dir()], reverse=True)
        for old in folders[keep:]:
            import shutil
            shutil.rmtree(old, ignore_errors=True)
