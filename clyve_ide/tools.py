"""Tools the agent can call. Stdlib only, so packaging stays simple."""

from __future__ import annotations

import fnmatch
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from .proc import no_window_kwargs

IS_WINDOWS = platform.system() == "Windows"
OUTPUT_LIMIT = 8000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".idea", ".vscode"}

CATASTROPHIC = [
    r"\brm\s+-rf\s+/(?:\s|$|\*)", r"\brm\s+-rf\s+~\s*$",
    r"\bformat\s+[a-zA-Z]:", r"\bdiskpart\b", r"\bmkfs(\.\w+)?\b",
    r"\bdd\s+if=.*of=/dev/[sh]d", r":\(\)\s*\{\s*:\|:&\s*\};:",
    r"del\s+/[sq]\s+[a-zA-Z]:\\\s*$", r"Remove-Item\s+.*-Recurse.*-Force\s+[a-zA-Z]:\\\s*$",
]


class Tools:
    def __init__(self, workspace: Path, log=None, extra_roots=None, full_access: bool = False):
        self.ws = Path(workspace).resolve()
        self.extra_roots = [Path(p).expanduser().resolve() for p in (extra_roots or [])]
        self.full_access = bool(full_access)
        self.log = log or (lambda *a, **k: None)

    # ---------------------------------------------------------------- paths
    def roots(self) -> list[Path]:
        return [self.ws] + [r for r in self.extra_roots if r != self.ws]

    def _inside(self, p: Path, root: Path) -> bool:
        return p == root or root in p.parents

    def _safe(self, raw: str, must_exist: bool = False) -> Path:
        p = Path(str(raw)).expanduser()
        p = (p if p.is_absolute() else self.ws / p).resolve()
        if not self.full_access and not any(self._inside(p, r) for r in self.roots()):
            allowed = ", ".join(str(r) for r in self.roots())
            raise PermissionError(
                f"{p} is outside the allowed folders ({allowed}). "
                "Ask the user to add that folder in the sidebar (Akses) or switch workspace."
            )
        if must_exist and not p.exists():
            raise FileNotFoundError(str(p))
        return p

    # ---------------------------------------------------------------- files
    def read_file(self, path: str, offset: int = 0, limit: int = 500) -> str:
        p = self._safe(path, must_exist=True)
        if p.is_dir():
            return self.list_dir(str(p))
        text = p.read_text("utf-8", errors="replace")
        lines = text.splitlines()
        chunk = lines[int(offset):int(offset) + int(limit)]
        body = "\n".join(f"{int(offset) + i + 1}| {line}" for i, line in enumerate(chunk))
        if int(offset) + int(limit) < len(lines):
            body += f"\n... ({len(lines)} lines total, next offset={int(offset) + int(limit)})"
        return body or "(empty file)"

    def write_file(self, path: str, content: str) -> str:
        p = self._safe(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, "utf-8")
        self.log("write", str(p))
        return f"wrote {len(content)} chars -> {p}"

    def edit_file(self, path: str, old: str, new: str, replace_all: bool = False) -> str:
        p = self._safe(path, must_exist=True)
        text = p.read_text("utf-8")
        count = text.count(old)
        if count == 0:
            return "ERROR: old string not found. Read the file and copy the exact text."
        if count > 1 and not replace_all:
            return f"ERROR: old string appears {count} times. Pass replace_all=true or include more context."
        text = text.replace(old, new, -1 if replace_all else 1)
        p.write_text(text, "utf-8")
        return f"edited {p} ({count if replace_all else 1} replacement)"

    def make_dir(self, path: str) -> str:
        p = self._safe(path)
        p.mkdir(parents=True, exist_ok=True)
        return f"created dir {p}"

    def delete_path(self, path: str) -> str:
        p = self._safe(path, must_exist=True)
        if p == self.ws:
            return "ERROR: refusing to delete the workspace root."
        if any(p == r for r in self.roots()):
            return "ERROR: refusing to delete an allowed-folder root."
        # never wipe a drive root or a system directory, even with full access
        if p.parent == p:
            return "ERROR: refusing to delete a filesystem root."
        guarded = {"windows", "program files", "program files (x86)", "programdata",
                   "users", "system32", "boot", "etc", "usr", "bin", "var", "system"}
        if p.name.lower() in guarded and p.parent == p.parent.parent:
            return f"ERROR: refusing to delete system directory {p}."
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return f"deleted {p}"

    def list_dir(self, path: str = ".") -> str:
        p = self._safe(path, must_exist=True)
        if not p.is_dir():
            return f"ERROR: not a directory: {p}"
        rows = []
        for e in sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))[:400]:
            try:
                size = e.stat().st_size
            except OSError:
                size = 0
            rows.append(f"{'DIR ' if e.is_dir() else 'FILE'} {size:>10}  {e.name}")
        return "\n".join(rows) or "(empty directory)"

    def search_files(self, pattern: str, path: str = ".", glob: str = "*",
                     case_sensitive: bool = False) -> str:
        root = self._safe(path, must_exist=True)
        rx = re.compile(pattern, 0 if case_sensitive else re.I)
        hits, scanned = [], 0
        for f in root.rglob("*"):
            if not f.is_file() or not fnmatch.fnmatch(f.name, glob):
                continue
            if SKIP_DIRS & set(f.parts):
                continue
            try:
                if f.stat().st_size > 2_000_000:
                    continue
                txt = f.read_text("utf-8", errors="ignore")
            except OSError:
                continue
            scanned += 1
            for i, line in enumerate(txt.splitlines(), 1):
                if rx.search(line):
                    try:
                        rel = f.relative_to(self.ws)
                    except ValueError:
                        rel = f
                    hits.append(f"{rel}:{i}: {line.strip()[:240]}")
                    if len(hits) >= 80:
                        return "\n".join(hits) + f"\n(stopped at 80 hits, {scanned} files scanned)"
        return "\n".join(hits) or f"no matches ({scanned} files scanned)"

    # ---------------------------------------------------------------- shell
    def run_shell(self, cmd: str, timeout: int = 300, cwd: str = "") -> str:
        for pat in CATASTROPHIC:
            if re.search(pat, cmd, re.I):
                return (f"BLOCKED by safety guard: pattern /{pat}/ looks destructive. "
                        "If it is genuinely needed, ask the user to run it manually.")
        workdir = self._safe(cwd) if cwd else self.ws
        self.log("shell", cmd)
        try:
            # CREATE_NO_WINDOW: tanpa ini tiap perintah memunculkan window cmd
            # hitam sekejap di Windows (flicker + lag saat agent kerja).
            proc = subprocess.run(cmd, shell=True, cwd=str(workdir), capture_output=True,
                                  text=True, timeout=int(timeout), errors="replace",
                                  **no_window_kwargs())
        except subprocess.TimeoutExpired:
            return f"ERROR: command timed out after {timeout}s"
        except Exception as e:  # noqa: BLE001
            return f"ERROR: {type(e).__name__}: {e}"
        out = (proc.stdout or "")[-OUTPUT_LIMIT:]
        err = (proc.stderr or "")[-3000:]
        return f"exit={proc.returncode}\n--- stdout ---\n{out}\n--- stderr ---\n{err}"

    # ---------------------------------------------------------------- db
    def sqlite_query(self, db: str, sql: str, allow_write: bool = False) -> str:
        p = self._safe(db, must_exist=True)
        head = re.sub(r"^\s*(--[^\n]*\n|\s)+", "", sql).strip().lower()
        if not allow_write and not head.startswith(("select", "with", "pragma", "explain")):
            return "BLOCKED: read-only mode. Only SELECT/WITH/PRAGMA/EXPLAIN. Pass allow_write=true to modify."
        con = sqlite3.connect(f"file:{p}?mode={'rw' if allow_write else 'ro'}", uri=True)
        try:
            cur = con.execute(sql)
            if cur.description:
                cols = [d[0] for d in cur.description]
                rows = cur.fetchmany(200)
                out = [" | ".join(cols)]
                out += [" | ".join("" if v is None else str(v) for v in r) for r in rows]
                return "\n".join(out)
            con.commit() if allow_write else None
            return f"ok, rowcount={cur.rowcount}"
        except Exception as e:  # noqa: BLE001
            return f"SQL ERROR: {e}"
        finally:
            con.close()

    # ---------------------------------------------------------------- http
    def http_request(self, method: str, url: str, headers: dict | None = None,
                     body: str | None = None, timeout: int = 45) -> str:
        method = method.upper()
        if not url.lower().startswith(("http://", "https://")):
            return "ERROR: url must start with http:// or https://"
        data = body.encode("utf-8") if isinstance(body, str) and body else None
        hdrs = dict(headers or {})
        if data and not any(k.lower() == "content-type" for k in hdrs):
            try:
                json.loads(body)
                hdrs["Content-Type"] = "application/json"
            except Exception:  # noqa: BLE001
                hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        self.log("http", f"{method} {url}")
        try:
            with urllib.request.urlopen(req, timeout=int(timeout)) as r:
                return f"status={r.status}\n--- body ---\n{r.read(OUTPUT_LIMIT).decode('utf-8', 'replace')}"
        except urllib.error.HTTPError as e:
            return f"status={e.code}\n--- body ---\n{e.read(4000).decode('utf-8', 'replace')}"
        except Exception as e:  # noqa: BLE001
            return f"ERROR: {type(e).__name__}: {e}"

    # ---------------------------------------------------------------- desktop
    def open_path(self, path: str = ".") -> str:
        p = self._safe(str(path))
        if not p.exists():
            return f"ERROR: does not exist: {p}"
        try:
            if IS_WINDOWS:
                os.startfile(str(p))  # noqa: S606
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", str(p)])
            else:
                subprocess.Popen(["xdg-open", str(p)])
            return f"opened {p} in the file manager"
        except Exception as e:  # noqa: BLE001
            return f"ERROR: {e}"

    def open_url(self, url: str) -> str:
        if not url.lower().startswith(("http://", "https://")):
            return "ERROR: url must start with http:// or https://"
        try:
            webbrowser.open(url)
            return f"opened {url} in the default browser"
        except Exception as e:  # noqa: BLE001
            return f"ERROR: {e}"

    def system_info(self) -> str:
        import sys
        return json.dumps({
            "os": platform.platform(),
            "python": sys.version.split()[0],
            "workspace": str(self.ws),
            "shell": "cmd.exe" if IS_WINDOWS else "bash",
            "cwd": os.getcwd(),
        }, indent=2)


SCHEMAS = [
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a text file (numbered lines). Use offset/limit for big files.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Create or overwrite a file. Parent folders are created automatically.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "Replace an exact string in a file. Safer than rewriting the whole file.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"},
            "replace_all": {"type": "boolean"}}, "required": ["path", "old", "new"]}}},
    {"type": "function", "function": {
        "name": "make_dir",
        "description": "Create a directory (recursive).",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                       "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "delete_path",
        "description": "Delete a file or directory inside the workspace.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                       "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "list_dir",
        "description": "List files and folders in a directory.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "default '.'"}}}}},
    {"type": "function", "function": {
        "name": "search_files",
        "description": "Search file contents with a regex. Returns path:line: text.",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"}, "path": {"type": "string"},
            "glob": {"type": "string", "description": "e.g. *.py"},
            "case_sensitive": {"type": "boolean"}}, "required": ["pattern"]}}},
    {"type": "function", "function": {
        "name": "run_shell",
        "description": "Run a shell command in the workspace. Use it to run code, install packages (pip/npm), git, etc. Timeout in seconds.",
        "parameters": {"type": "object", "properties": {
            "cmd": {"type": "string"}, "timeout": {"type": "integer"},
            "cwd": {"type": "string"}}, "required": ["cmd"]}}},
    {"type": "function", "function": {
        "name": "sqlite_query",
        "description": "Query a SQLite database file inside the workspace. Read-only unless allow_write=true.",
        "parameters": {"type": "object", "properties": {
            "db": {"type": "string"}, "sql": {"type": "string"},
            "allow_write": {"type": "boolean"}}, "required": ["db", "sql"]}}},
    {"type": "function", "function": {
        "name": "http_request",
        "description": "Call any HTTP API (Postman-style): method, url, headers, body, timeout.",
        "parameters": {"type": "object", "properties": {
            "method": {"type": "string"}, "url": {"type": "string"},
            "headers": {"type": "object"}, "body": {"type": "string"},
            "timeout": {"type": "integer"}}, "required": ["method", "url"]}}},
    {"type": "function", "function": {
        "name": "open_path",
        "description": "Open a file or folder in Windows Explorer / the OS file manager.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "open_url",
        "description": "Open a URL in the user's default browser (e.g. to show a running localhost app).",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}},
                       "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "system_info",
        "description": "OS, Python version, workspace path, current shell.",
        "parameters": {"type": "object", "properties": {}}}},
]
