"""Local HTTP server: static UI + JSON API + SSE stream. Stdlib only."""

from __future__ import annotations

import json
import os
import platform
import re
import sys
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config
from . import attachments as attach
from .agent import AgentSession

IS_WINDOWS = platform.system() == "Windows"


# --------------------------------------------------------------------- chats
def chats_dir() -> Path:
    p = config.config_dir() / "chats"
    p.mkdir(parents=True, exist_ok=True)
    return p


def chat_file(cid: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "", cid or "") or "chat"
    return chats_dir() / f"{safe}.json"


def list_chats() -> list[dict]:
    out = []
    for f in chats_dir().glob("*.json"):
        try:
            d = json.loads(f.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            continue
        out.append({"id": d.get("id", f.stem), "title": d.get("title") or "(baru)",
                    "updated": d.get("updated", 0), "count": len(d.get("messages") or [])})
    out.sort(key=lambda c: c["updated"], reverse=True)
    return out


def save_chat(cid: str, messages: list[dict]) -> None:
    clean = [m for m in messages if m.get("role") in ("user", "assistant") and m.get("content")]
    title = "(baru)"
    for m in clean:
        if m["role"] == "user" and m["content"]:
            title = re.sub(r"\s+", " ", m["content"]).strip()[:70]
            break
    data = {"id": cid, "title": title, "updated": time.time(), "messages": clean}
    chat_file(cid).write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")


def load_chat(cid: str) -> dict | None:
    p = chat_file(cid)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def web_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", ".")) / "web"
    return Path(__file__).resolve().parent.parent / "web"


class State:
    cfg: dict = {}
    session: AgentSession | None = None
    models: list[str] = []
    current_chat: str = ""
    lock = threading.Lock()


def make_session(cfg: dict) -> AgentSession:
    return AgentSession(cfg["api_key"], cfg["base_url"], cfg["model"], cfg["workspace"],
                        extra_roots=cfg.get("extra_roots") or [],
                        full_access=bool(cfg.get("full_access")))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ClyveIDE"

    # ------------------------------------------------------------- plumbing
    def log_message(self, fmt, *args):  # keep the console readable
        if os.environ.get("CLYVE_IDE_DEBUG"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def _static(self, rel: str, ctype: str):
        p = (web_root() / rel).resolve()
        if web_root().resolve() not in p.parents and p != web_root().resolve():
            return self._json({"error": "forbidden"}, 403)
        if not p.is_file():
            return self._json({"error": "not found"}, 404)
        data = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------- routing
    def do_GET(self):  # noqa: N802
        url = urllib.parse.urlparse(self.path)
        path = url.path
        if path in ("/", "/index.html"):
            return self._static("index.html", "text/html; charset=utf-8")
        if path == "/styles.css":
            return self._static("styles.css", "text/css; charset=utf-8")
        if path == "/app.js":
            return self._static("app.js", "application/javascript; charset=utf-8")
        if path == "/favicon.ico":
            return self._json({}, 204)
        if path == "/api/state":
            return self._json(self.state_payload())
        if path == "/api/browse":
            q = urllib.parse.parse_qs(url.query)
            return self._json(self.browse(q.get("path", [""])[0]))
        if path == "/api/history":
            sess = State.session
            return self._json({"history": sess.history() if sess else []})
        if path == "/api/chats":
            return self._json({"chats": list_chats(), "current": State.current_chat})
        return self._json({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/key":
            return self.post_key()
        if path == "/api/workspace":
            return self.post_workspace()
        if path == "/api/model":
            return self.post_model()
        if path == "/api/access":
            return self.post_access()
        if path == "/api/upload":
            return self.post_upload()
        if path == "/api/chats/new":
            return self.post_chat_new()
        if path == "/api/chats/open":
            return self.post_chat_open()
        if path == "/api/chats/delete":
            return self.post_chat_delete()
        if path == "/api/reset":
            State.session and State.session.reset()
            return self._json({"ok": True})
        if path == "/api/cancel":
            State.session and State.session.cancel.set()
            return self._json({"ok": True})
        if path == "/api/chat":
            return self.post_chat()
        return self._json({"error": "not found"}, 404)

    # ------------------------------------------------------------- handlers
    def state_payload(self) -> dict:
        cfg = State.cfg
        roots = [cfg.get("workspace")] + [p for p in (cfg.get("extra_roots") or [])]
        return {
            "has_key": bool(cfg.get("api_key")),
            "key_masked": config.mask(cfg.get("api_key", "")),
            "base_url": cfg.get("base_url"),
            "model": cfg.get("model"),
            "models": State.models or config.FALLBACK_MODELS,
            "workspace": cfg.get("workspace"),
            "extra_roots": cfg.get("extra_roots") or [],
            "full_access": bool(cfg.get("full_access")),
            "roots": [r for r in roots if r],
            "os": platform.platform(),
            "version": "1.0.0",
        }

    def post_key(self):
        data = self._read_json()
        key = (data.get("key") or "").strip()
        base = (data.get("base_url") or State.cfg.get("base_url") or config.DEFAULT_BASE).strip()
        if not key:
            return self._json({"ok": False, "error": "API key kosong"}, 400)
        State.cfg["api_key"] = key
        State.cfg["base_url"] = base
        try:
            models = config.list_models(base, key)
            if models == config.FALLBACK_MODELS:
                # verify with a real request so a typo does not look like success
                import urllib.request
                req = urllib.request.Request(
                    base.rstrip("/") + "/chat/completions",
                    data=json.dumps({"model": State.cfg["model"], "max_tokens": 1,
                                     "messages": [{"role": "user", "content": "hi"}]}).encode(),
                    headers={"Authorization": f"Bearer {key}",
                             "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=25) as r:
                    json.load(r)
        except Exception as e:  # noqa: BLE001
            State.cfg["api_key"] = ""
            return self._json({"ok": False, "error": f"key ditolak: {e}"}, 400)

        State.models = models
        config.set_model_cache(models)
        if State.cfg.get("model") not in models and models:
            State.cfg["model"] = models[0]
        config.save(State.cfg)
        State.session = make_session(State.cfg)
        return self._json({"ok": True, **self.state_payload()})

    def post_workspace(self):
        data = self._read_json()
        p = (data.get("path") or "").strip()
        if not p:
            return self._json({"ok": False, "error": "path kosong"}, 400)
        try:
            Path(p).expanduser().mkdir(parents=True, exist_ok=True)
            State.cfg["workspace"] = str(Path(p).expanduser().resolve())
        except Exception as e:  # noqa: BLE001
            return self._json({"ok": False, "error": str(e)}, 400)
        config.save(State.cfg)
        if State.session:
            State.session.set_workspace(State.cfg["workspace"])
        else:
            State.session = make_session(State.cfg)
        return self._json({"ok": True, "workspace": State.cfg["workspace"]})

    def post_model(self):
        data = self._read_json()
        m = (data.get("model") or "").strip()
        if not m:
            return self._json({"ok": False, "error": "model kosong"}, 400)
        State.cfg["model"] = m
        config.save(State.cfg)
        if State.session:
            State.session.set_model(m)
        return self._json({"ok": True, "model": m})

    def post_access(self):
        data = self._read_json()
        if "extra_roots" in data:
            cleaned = []
            for p in data.get("extra_roots") or []:
                try:
                    pp = Path(str(p)).expanduser().resolve()
                except Exception:  # noqa: BLE001
                    continue
                if pp.is_dir() and str(pp) not in cleaned and str(pp) != State.cfg.get("workspace"):
                    cleaned.append(str(pp))
            State.cfg["extra_roots"] = cleaned
        if "full_access" in data:
            State.cfg["full_access"] = bool(data.get("full_access"))
        config.save(State.cfg)
        if State.session is None:
            State.session = make_session(State.cfg)
        State.session.set_access(State.cfg.get("extra_roots") or [],
                                 bool(State.cfg.get("full_access")))
        return self._json({"ok": True, **self.state_payload()})

    def post_upload(self):
        data = self._read_json()
        name = (data.get("name") or "file").strip()
        try:
            raw = attach.decode_data_url(data.get("data") or "")
        except Exception as e:  # noqa: BLE001
            return self._json({"ok": False, "error": f"payload tidak valid: {e}"}, 400)
        if not raw:
            return self._json({"ok": False, "error": "file kosong"}, 400)
        ws = State.cfg.get("workspace") or str(Path.home())
        try:
            info = attach.save_upload(Path(ws), name, raw)
        except Exception as e:  # noqa: BLE001
            return self._json({"ok": False, "error": str(e)}, 400)
        return self._json({"ok": True, **info,
                           "max_files": attach.MAX_FILES,
                           "max_image_mb": attach.MAX_IMAGE_BYTES // 1024 // 1024,
                           "max_doc_mb": attach.MAX_DOC_BYTES // 1024 // 1024})

    def post_chat_new(self):
        if State.session is None:
            State.session = make_session(State.cfg)
        State.current_chat = uuid.uuid4().hex[:12]
        State.session.reset()
        return self._json({"ok": True, "id": State.current_chat, "messages": [],
                           "chats": list_chats()})

    def post_chat_open(self):
        data = self._read_json()
        cid = (data.get("id") or "").strip()
        chat = load_chat(cid)
        if not chat:
            return self._json({"ok": False, "error": "chat tidak ditemukan"}, 404)
        if State.session is None:
            State.session = make_session(State.cfg)
        State.current_chat = cid
        State.session.cancel.clear()
        State.session.load(chat.get("messages") or [])
        return self._json({"ok": True, "id": cid, "title": chat.get("title"),
                           "messages": chat.get("messages") or []})

    def post_chat_delete(self):
        data = self._read_json()
        cid = (data.get("id") or "").strip()
        p = chat_file(cid)
        if p.exists():
            p.unlink()
        if cid == State.current_chat:
            State.current_chat = uuid.uuid4().hex[:12]
            if State.session:
                State.session.reset()
        return self._json({"ok": True, "chats": list_chats(), "current": State.current_chat})

    def browse(self, raw: str) -> dict:
        target = (raw or "").strip()
        try:
            if not target:
                if IS_WINDOWS:
                    import string
                    from ctypes import windll
                    drives = []
                    try:
                        mask = windll.kernel32.GetLogicalDrives()
                        drives = [f"{string.ascii_uppercase[i]}:\\" for i in range(26)
                                  if mask & (1 << i)]
                    except Exception:  # noqa: BLE001
                        drives = ["C:\\"]
                    return {"path": "", "parent": None, "dirs": drives, "at_root": True}
                target = str(Path.home())
            p = Path(target).expanduser().resolve()
            if not p.is_dir():
                return {"error": f"bukan folder: {p}", "path": target, "dirs": []}
            dirs = []
            for e in sorted(p.iterdir(), key=lambda x: x.name.lower()):
                if e.is_dir() and not e.name.startswith("$") and e.name != "System Volume Information":
                    dirs.append(str(e))
            parent = str(p.parent) if p.parent != p else None
            return {"path": str(p), "parent": parent, "dirs": dirs[:300],
                    "at_root": parent is None}
        except PermissionError as e:
            return {"error": f"akses ditolak: {e}", "path": target, "dirs": []}
        except Exception as e:  # noqa: BLE001
            return {"error": str(e), "path": target, "dirs": []}

    def post_chat(self):
        data = self._read_json()
        text = (data.get("message") or "").strip()
        if not text:
            return self._json({"error": "message kosong"}, 400)
        if not State.cfg.get("api_key"):
            return self._json({"error": "API key belum diisi"}, 401)
        atts = data.get("attachments") or []
        if len(atts) > attach.MAX_FILES:
            return self._json({"error": f"maksimal {attach.MAX_FILES} lampiran per pesan"}, 400)
        if State.session is None:
            State.session = make_session(State.cfg)
        if not State.current_chat:
            State.current_chat = uuid.uuid4().hex[:12]

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True  # no Content-Length on SSE: closing the socket is the end signal
        try:
            for event in State.session.run(text, data.get("attachments") or []):
                self.wfile.write(f"data: {event}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            State.session.cancel.set()
        except Exception as e:  # noqa: BLE001
            try:
                self.wfile.write(f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n".encode())
                self.wfile.flush()
            except Exception:  # noqa: BLE001
                pass
        finally:
            try:
                save_chat(State.current_chat, State.session.snapshot())
            except Exception:  # noqa: BLE001
                pass


def start_server(port: int = 0) -> tuple["ThreadingHTTPServer", str]:
    """Initialise state, start the HTTP server on a daemon thread, return (httpd, url)."""
    State.cfg = config.load()
    if State.cfg.get("api_key"):
        State.models = config.list_models(State.cfg["base_url"], State.cfg["api_key"])
        State.session = make_session(State.cfg)
    else:
        cached = config.last_model_cache().get("models") or []
        State.models = cached or config.FALLBACK_MODELS

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    if not State.current_chat:
        State.current_chat = uuid.uuid4().hex[:12]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    return httpd, url


def serve(port: int = 0, open_browser: bool = True) -> None:
    httpd, url = start_server(port)

    print("\n  Clyve IDE")
    print(f"  UI        : {url}")
    print(f"  config    : {config.CONFIG_PATH}")
    print(f"  workspace : {State.cfg.get('workspace')}")
    print("  Ctrl+C untuk stop\n")

    if open_browser:
        threading.Thread(target=lambda: (__import__("time").sleep(0.6),
                                         __import__("webbrowser").open(url)),
                         daemon=True).start()
    try:
        while True:
            __import__("time").sleep(1)
    except KeyboardInterrupt:
        print("\n  stopped\n")
    finally:
        httpd.shutdown()
        httpd.server_close()
