"""Streaming agent loop. Emits dict events; the server turns them into SSE."""

from __future__ import annotations

import json
import platform
import threading
import time
import urllib.error
import urllib.request
from difflib import unified_diff
from pathlib import Path

from . import config
from .checkpoints import CheckpointStore
from .tools import SCHEMAS, Tools

MAX_STEPS = 40
HISTORY_LIMIT = 60
IS_WINDOWS = platform.system() == "Windows"
MUTATING = {"write_file", "edit_file", "delete_path", "make_dir"}

SYSTEM_PROMPT = """You are Clyve IDE, an autonomous coding agent running locally on the user's {os} machine.

Workspace root: {ws}
Allowed folders: {roots}
Full disk access: {full}
Shell: {shell} (working directory is always the workspace root)
Mode: {mode}
Permissions: the user granted "always allow". Do not ask for confirmation, just act.

How to behave:
- Act, do not describe. If the user asks for something, build it and run it.
- Verify everything by actually running it. Never claim success without command output.
- Read a file before editing it. Prefer edit_file over rewriting a whole file.
- Paths outside the allowed folders are rejected. If the user needs one, tell them to add it
  in the sidebar (Akses -> + Folder) or enable full disk access, then retry. Do not pretend it worked.
- Install dependencies yourself (pip install / npm install) in a venv when the OS Python is locked down.
- Write code that runs on Windows too: avoid POSIX-only paths, `&&` in PowerShell is fine, prefer `python -m pip`.
- When an app has a UI or a server, start it and open it with open_url.
- Reply in the user's language, short and concrete. The user already sees every tool call.
"""


def _sse(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


class AgentSession:
    def __init__(self, api_key: str, base_url: str, model: str, workspace: str,
                 extra_roots=None, full_access: bool = False):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.workspace = str(Path(workspace).expanduser().resolve())
        self.extra_roots = [str(Path(p).expanduser().resolve()) for p in (extra_roots or [])]
        self.full_access = bool(full_access)
        self.tools = Tools(Path(self.workspace), extra_roots=self.extra_roots,
                           full_access=self.full_access)
        self.mode = "auto"                 # auto | review | plan
        self.approval = None               # callable(payload) -> bool
        self.chat_key = "default"
        self.checkpoints = CheckpointStore(config.config_dir(), self.chat_key)
        self.messages: list[dict] = [{"role": "system", "content": self._prompt()}]
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}

    # ------------------------------------------------------------------ setup
    MODE_HINT = {
        "auto": "auto - act directly, no approval prompts",
        "review": "review - every file write, delete and shell command needs user approval; wait for it",
        "plan": "plan - read-only. Do NOT write files or run commands. Produce a concrete plan "
                "(steps, files to touch, risks) and stop.",
    }

    def _prompt(self) -> str:
        roots = ", ".join(str(r) for r in self.tools.roots())
        return SYSTEM_PROMPT.format(os=platform.platform(), ws=self.workspace,
                                    roots=roots,
                                    full="ON (every path on this machine)" if self.full_access else "OFF",
                                    mode=self.MODE_HINT.get(self.mode, self.MODE_HINT["auto"]),
                                    shell="cmd.exe / PowerShell" if IS_WINDOWS else "bash")

    def set_mode(self, mode: str) -> str:
        if mode in ("auto", "review", "plan"):
            self.mode = mode
            self.messages.append({"role": "system",
                                  "content": f"Mode changed to {mode}. " + self.MODE_HINT[mode]})
        return self.mode

    def set_chat(self, cid: str) -> None:
        self.chat_key = cid or "default"
        self.checkpoints = CheckpointStore(config.config_dir(), self.chat_key)

    def _rebuild_tools(self) -> None:
        self.tools = Tools(Path(self.workspace), extra_roots=self.extra_roots,
                           full_access=self.full_access)

    def set_workspace(self, path: str) -> str:
        p = Path(path).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        self.workspace = str(p)
        self._rebuild_tools()
        self.messages.append({"role": "system",
                              "content": f"Workspace changed to {p}. Use this root from now on."})
        return str(p)

    def set_access(self, extra_roots=None, full_access=None) -> dict:
        if extra_roots is not None:
            self.extra_roots = [str(Path(p).expanduser().resolve()) for p in extra_roots]
        if full_access is not None:
            self.full_access = bool(full_access)
        self._rebuild_tools()
        self.messages.append({"role": "system", "content": (
            "Access rules updated. Allowed folders: " + ", ".join(str(r) for r in self.tools.roots()) +
            ("; full disk access: ON" if self.full_access else "; full disk access: OFF") +
            ". Retry the blocked operation if it is now allowed.")})
        return {"extra_roots": self.extra_roots, "full_access": self.full_access,
                "roots": [str(r) for r in self.tools.roots()]}

    def set_model(self, model: str) -> None:
        self.model = model

    def reset(self) -> None:
        with self.lock:
            self.messages = [{"role": "system", "content": self._prompt()}]
            self.usage = {"prompt_tokens": 0, "completion_tokens": 0}

    def history(self) -> list[dict]:
        return [m for m in self.messages if m.get("role") in ("user", "assistant") and m.get("content")]

    def load(self, msgs) -> None:
        """Replace the conversation with a saved transcript."""
        with self.lock:
            self.messages = [{"role": "system", "content": self._prompt()}]
            for m in msgs or []:
                if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str):
                    self.messages.append({"role": m["role"], "content": m["content"]})

    def snapshot(self) -> list[dict]:
        """Text-only transcript, safe to persist and re-render."""
        out = []
        for m in self.messages:
            if m.get("role") not in ("user", "assistant"):
                continue
            c = m.get("content")
            if isinstance(c, list):
                c = "\n".join(p.get("text", "") for p in c
                              if isinstance(p, dict) and p.get("type") == "text")
            out.append({"role": m["role"], "content": c or ""})
        return out

    # ------------------------------------------------------------------ http
    def _stream_chat(self, on_delta):
        """Yield (assistant_message, usage) after streaming one completion."""
        payload = json.dumps({
            "model": self.model,
            "messages": self.messages,
            "tools": SCHEMAS,
            "tool_choice": "auto",
            "stream": True,
        }).encode()
        req = urllib.request.Request(
            self.base_url + "/chat/completions", data=payload,
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json",
                     "Accept": "text/event-stream"})
        content, tool_calls, finish, usage = [], {}, None, None
        delays = [2, 5, 10]
        last_err = None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    for raw in r:
                        if self.cancel.is_set():
                            raise RuntimeError("__cancelled__")
                        line = raw.decode("utf-8", "replace").strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if chunk.get("usage"):
                            usage = chunk["usage"]
                        if chunk.get("error"):
                            raise RuntimeError(chunk["error"])
                        for ch in chunk.get("choices", []):
                            delta = ch.get("delta") or {}
                            if delta.get("content"):
                                content.append(delta["content"])
                                on_delta(delta["content"])
                            for tc in delta.get("tool_calls") or []:
                                idx = tc.get("index", 0)
                                slot = tool_calls.setdefault(
                                    idx, {"id": "", "type": "function",
                                          "function": {"name": "", "arguments": ""}})
                                if tc.get("id"):
                                    slot["id"] = tc["id"]
                                fn = tc.get("function") or {}
                                if fn.get("name"):
                                    slot["function"]["name"] = fn["name"]
                                if fn.get("arguments"):
                                    slot["function"]["arguments"] += fn["arguments"]
                            if ch.get("finish_reason"):
                                finish = ch["finish_reason"]
                break
            except urllib.error.HTTPError as e:
                body = e.read(600).decode("utf-8", "replace")
                last_err = f"HTTP {e.code}: {body}"
                if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(delays[attempt]); continue
                raise RuntimeError(last_err)
            except RuntimeError as e:
                # gateway-side stream timeout: shrink the prompt and try again
                msg = str(e).lower()
                if ("timed out" in msg or "timeout" in msg or "stream" in msg) and attempt < 2:
                    self._shrink(0.5)
                    content, tool_calls, finish = [], {}, None
                    time.sleep(delays[attempt])
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_err = f"network: {e}"
                if attempt < 3:
                    time.sleep(delays[attempt]); continue
                raise RuntimeError(last_err)
        msg = {"role": "assistant", "content": "".join(content)}
        if tool_calls:
            msg["tool_calls"] = [tool_calls[k] for k in sorted(tool_calls)]
        return msg, usage

    # ------------------------------------------------------------------ guard
    def _diff_for(self, name: str, args: dict) -> dict:
        path = str(args.get("path") or "")
        p = Path(path)
        if not p.is_absolute():
            p = self.tools.ws / p
        old = ""
        try:
            if p.is_file():
                old = p.read_text("utf-8", errors="replace")
        except OSError:
            old = ""
        new = old
        if name == "write_file":
            new = str(args.get("content") or "")
        elif name == "edit_file":
            o, n = str(args.get("old") or ""), str(args.get("new") or "")
            new = old.replace(o, n, -1 if args.get("replace_all") else 1)
        diff = "\n".join(unified_diff(old.splitlines(), new.splitlines(),
                                      fromfile=f"a/{p.name}", tofile=f"b/{p.name}", lineterm=""))
        added = sum(1 for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in diff.splitlines() if l.startswith("-") and not l.startswith("---"))
        return {"action": name, "path": str(p), "diff": diff[:20000],
                "added": added, "removed": removed}

    def _guard(self, name: str, args: dict) -> str | None:
        """Plan mode blocks writes; review mode asks the UI. Returns an error string to skip."""
        if self.mode == "plan" and name in (MUTATING | {"run_shell"}):
            return ("PLAN MODE: tool perubahan dimatikan. Susun rencana saja "
                    "(file yang akan diubah, langkah, risiko), lalu berhenti.")
        if name in MUTATING:
            payload = (self._diff_for(name, args) if name in ("write_file", "edit_file")
                       else {"action": name, "path": str(args.get("path") or ""), "diff": ""})
        elif name == "run_shell":
            payload = {"action": "run_shell", "cmd": str(args.get("cmd") or ""), "diff": ""}
        else:
            return None

        if self.mode == "review" and self.approval is not None:
            try:
                ok = bool(self.approval(payload))
            except Exception:  # noqa: BLE001
                ok = False
            if not ok:
                return "DIBATALKAN oleh user: perubahan tidak diterapkan."

        if name in MUTATING:
            target = str(args.get("path") or "")
            try:
                self.checkpoints.create(f"{name}: {Path(target).name}", [target])
            except Exception:  # noqa: BLE001
                pass
        return None

    def run(self, user_text: str, attachments: list[dict] | None = None):
        from .attachments import build_context, image_part

        ctx, images = build_context(attachments or [])
        text = user_text
        if ctx:
            text += ("\n\n--- Lampiran dari user ---\n" + ctx +
                     "\n--- akhir lampiran ---")
        if images:
            content = [{"type": "text", "text": text}] + [image_part(p) for p in images]
        else:
            content = text
        images_used = bool(images)
        stripped_images = False

        with self.lock:
            self.messages.append({"role": "user", "content": content})
            self.cancel.clear()
        try:
            yield _sse({"type": "start"})
            for step in range(MAX_STEPS):
                if self.cancel.is_set():
                    yield _sse({"type": "done", "content": "dihentikan oleh user"})
                    return
                self._trim()
                text_parts = []

                def on_delta(t):
                    text_parts.append(t)
                    # handled by generator below via queue-free approach

                # stream with live deltas: read in a thread, forward through a queue
                import queue
                q: "queue.Queue" = queue.Queue()

                def worker():
                    try:
                        msg, usage = self._stream_chat(wd)
                        q.put(("m", (msg, usage)))
                    except Exception as e:  # noqa: BLE001
                        q.put(("e", str(e)))

                partial: list[str] = []

                def wd(t: str) -> None:
                    partial.append(t)
                    q.put(("d", t))

                th = threading.Thread(target=worker, daemon=True)
                th.start()
                retry_no_images = False
                while True:
                    try:
                        kind, val = q.get(timeout=0.25)
                    except queue.Empty:
                        if self.cancel.is_set():
                            yield _sse({"type": "done", "content": "dihentikan oleh user"})
                            return
                        continue
                    if kind == "d":
                        yield _sse({"type": "delta", "text": val})
                    elif kind == "e":
                        if val == "__cancelled__":
                            if partial:
                                self.messages.append({"role": "assistant",
                                                      "content": "".join(partial)})
                            yield _sse({"type": "done", "content": "dihentikan oleh user",
                                        "cancelled": True})
                            return
                        if images_used and not stripped_images:
                            # model may not accept vision content; retry text-only once
                            stripped_images = True
                            retry_no_images = True
                            for m in reversed(self.messages):
                                if m.get("role") == "user" and isinstance(m.get("content"), list):
                                    texts = [p.get("text", "") for p in m["content"]
                                             if isinstance(p, dict) and p.get("type") == "text"]
                                    m["content"] = "\n".join(texts) + (
                                        "\n\n(Catatan: model ini menolak gambar, "
                                        "jadi lampiran gambar tidak dikirim.)")
                                    break
                            yield _sse({"type": "delta",
                                        "text": "_(model tidak menerima gambar, coba tanpa gambar…)_\n\n"})
                        else:
                            yield _sse({"type": "error", "message": val})
                            return
                    else:
                        msg, usage = val
                        break
                th.join(timeout=1)
                if retry_no_images:
                    continue

                if usage:
                    self.usage["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
                    self.usage["completion_tokens"] += int(usage.get("completion_tokens") or 0)

                stored = {"role": "assistant", "content": msg.get("content") or ""}
                if msg.get("tool_calls"):
                    stored["tool_calls"] = msg["tool_calls"]
                self.messages.append(stored)

                calls = msg.get("tool_calls") or []
                if not calls:
                    yield _sse({"type": "done", "content": msg.get("content") or "",
                                "usage": self.usage, "steps": step + 1})
                    return

                for tc in calls:
                    if self.cancel.is_set():
                        yield _sse({"type": "done", "content": "dihentikan oleh user"})
                        return
                    name = tc["function"]["name"]
                    raw_args = tc["function"].get("arguments") or "{}"
                    try:
                        args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        args = {}
                        yield _sse({"type": "tool_start", "id": tc.get("id"), "name": name, "args": raw_args[:500]})
                        out = "ERROR: arguments were not valid JSON. Re-send the call with valid JSON."
                    else:
                        yield _sse({"type": "tool_start", "id": tc.get("id"), "name": name, "args": args})
                        fn = getattr(self.tools, name, None)
                        t0 = time.time()
                        if fn is None:
                            out = f"ERROR: unknown tool {name}"
                        else:
                            blocked = self._guard(name, args)
                            try:
                                out = blocked if blocked else str(fn(**args))
                            except TypeError as e:
                                out = f"ERROR: bad arguments for {name}: {e}"
                            except Exception as e:  # noqa: BLE001
                                out = f"ERROR: {type(e).__name__}: {e}"
                        yield _sse({"type": "tool_end", "id": tc.get("id"), "name": name,
                                    "ok": not out.startswith(("ERROR", "BLOCKED")),
                                    "result": out[:6000], "ms": int((time.time() - t0) * 1000)})
                    self.messages.append({"role": "tool", "tool_call_id": tc.get("id"),
                                          "content": out[:4000]})
            yield _sse({"type": "done", "content": "(berhenti: batas langkah tercapai)"})
        except Exception as e:  # noqa: BLE001
            yield _sse({"type": "error", "message": f"{type(e).__name__}: {e}"})

    def _shrink(self, keep_ratio: float = 0.6) -> None:
        """Cut context size when the provider stalls or the prompt gets too big."""
        if len(self.messages) <= 3:
            return
        head, body = self.messages[0], self.messages[1:]
        for m in body:
            c = m.get("content")
            if m.get("role") == "tool" and isinstance(c, str) and len(c) > 1200:
                m["content"] = c[:1200] + "\n... (dipotong untuk hemat konteks)"
        keep = max(6, int(len(body) * keep_ratio))
        body = body[-keep:]
        while body and body[0].get("role") == "tool":
            body.pop(0)
        self.messages = [head] + body

    def _context_chars(self) -> int:
        total = 0
        for m in self.messages:
            c = m.get("content")
            if isinstance(c, str):
                total += len(c)
            for tc in m.get("tool_calls") or []:
                total += len(tc.get("function", {}).get("arguments") or "")
        return total

    def _trim(self) -> None:
        if len(self.messages) <= HISTORY_LIMIT and self._context_chars() < 200_000:
            return
        if self._context_chars() >= 200_000:
            self._shrink(0.5)
        if len(self.messages) <= HISTORY_LIMIT:
            return
        head, body = self.messages[0], self.messages[1:]
        while len(body) > HISTORY_LIMIT - 1 and len(body) > 2:
            body.pop(0)
        while body and body[0].get("role") == "tool":
            body.pop(0)
        self.messages = [head] + body
