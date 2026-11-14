"""Clyve IDE desktop UI - native Qt widgets (PySide6), no webview.

Backend is the same agent/tools used by the web UI; this module only replaces
the presentation layer and adds review/plan modes plus checkpoint rollback.
"""

from __future__ import annotations

import html
import json
import platform
import re
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QPalette, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QTextBrowser, QVBoxLayout, QWidget,
)

from . import attachments as attach
from . import config
from . import stt as stt_mod
from .agent import AgentSession
from .checkpoints import CheckpointStore
from .server import list_chats, load_chat, save_chat

BG = "#0b0d12"
PANEL = "#0e1118"
PANEL2 = "#12161f"
LINE = "#232936"
FG = "#e6e9f0"
MUTED = "#98a1b6"
ACCENT = "#5b8cff"
OK = "#31c48d"
ERR = "#f26d6d"
WARN = "#f2b56d"

QSS = f"""
QWidget {{ background:{BG}; color:{FG}; font-size:13px; }}
QFrame#side {{ background:{PANEL}; border-right:1px solid {LINE}; }}
QLabel#brand {{ font-size:14px; font-weight:700; }}
QLabel.section {{ color:#828da6; font-size:10px; letter-spacing:1px; }}
QPushButton {{ background:{PANEL2}; border:1px solid {LINE}; border-radius:8px;
    padding:7px 12px; min-height:20px; }}
QPushButton:hover {{ border-color:#3a4560; }}
QPushButton#primary {{ background:#4a74e8; border-color:#4a74e8; color:white; font-weight:600; padding:8px 16px; }}
QPushButton#primary:hover {{ background:#5b84f5; }}
QPushButton#danger {{ background:#5a2020; border-color:#7c2c2c; color:#ffd9d9; }}
QPushButton:disabled {{ color:#5a6478; border-color:#1e2531; }}
QComboBox, QLineEdit {{ background:{PANEL2}; border:1px solid {LINE}; border-radius:8px;
    padding:6px 10px; min-height:22px; }}
QComboBox::drop-down {{ border:none; }}
QPlainTextEdit#input {{ background:{PANEL2}; border:1px solid {LINE}; border-radius:12px;
    padding:10px 12px; font-size:13px; }}
QPlainTextEdit#input:focus {{ border-color:#3d6ff0; }}
QTextBrowser {{ background:{BG}; border:none; }}
QListWidget {{ background:{PANEL}; border:none; outline:none; }}
QListWidget::item {{ padding:6px 8px; border-radius:6px; color:#b8c2d6; }}
QListWidget::item:selected {{ background:#161f31; color:#dbe6ff; }}
QListWidget::item:hover {{ background:#141925; }}
QScrollBar:vertical {{ background:transparent; width:10px; margin:2px; }}
QScrollBar::handle:vertical {{ background:#2f3a4f; border-radius:4px; min-height:28px; }}
QScrollBar::handle:vertical:hover {{ background:#3c4a63; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height:0; }}
QCheckBox {{ color:{MUTED}; spacing:7px; }}
QCheckBox::indicator {{ width:15px; height:15px; border:1px solid #33405a; border-radius:4px;
    background:{PANEL2}; }}
QCheckBox::indicator:checked {{ background:#3f6ddb; border-color:#5b8cff; }}
QDialog {{ background:{BG}; }}
"""


# --------------------------------------------------------------------- utils
def md_to_html(text: str) -> str:
    """Small markdown subset -> HTML for QTextBrowser."""
    parts = re.split(r"```", text)
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            body = part.split("\n", 1)[1] if "\n" in part else part
            out.append(
                "<table width='100%' cellspacing='0' cellpadding='8' bgcolor='#0a0d13' "
                "style='border:1px solid #1e2531;'>"
                "<tr><td><pre style='margin:0;color:#c8d2e6;font-family:monospace;'>"
                f"{html.escape(body.rstrip())}</pre></td></tr></table>")
            continue
        t = html.escape(part)
        t = re.sub(r"`([^`\n]+)`",
                   f"<code style='background:#12161f;border:1px solid {LINE};"
                   f"border-radius:4px;padding:0 3px;'>&nbsp;\\1&nbsp;</code>", t)
        t = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r"<a href='\2'>\1</a>", t)
        lines = []
        for line in t.split("\n"):
            m = re.match(r"^\s*[-*]\s+(.*)$", line)
            lines.append(f"&nbsp;• {m.group(1)}" if m else line)
        out.append("<br>".join(lines))
    return "".join(out)


def fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n // 1024} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def arg_summary(name: str, args: dict) -> str:
    a = args or {}
    if name in ("read_file", "write_file", "edit_file", "delete_path", "make_dir", "list_dir", "open_path"):
        return str(a.get("path") or ".")
    if name == "search_files":
        return f"{a.get('pattern', '')} {a.get('glob', '')}".strip()
    if name == "run_shell":
        return str(a.get("cmd") or "")
    if name == "sqlite_query":
        return f"{a.get('db', '')}: {str(a.get('sql', ''))[:60]}"
    if name == "http_request":
        return f"{str(a.get('method', 'GET')).upper()} {a.get('url', '')}"
    if name == "open_url":
        return str(a.get("url") or "")
    return json.dumps(a, ensure_ascii=False)[:80]


# --------------------------------------------------------------------- worker
class ChatInput(QPlainTextEdit):
    """Input chat yang menerima paste gambar (Ctrl+V) langsung sebagai lampiran."""

    image_pasted = Signal(object)

    def keyPressEvent(self, ev):  # noqa: N802
        if ev.matches(QKeySequence.Paste):
            img = QApplication.clipboard().image()
            if not img.isNull():
                self.image_pasted.emit(img)
                return
        super().keyPressEvent(ev)

    def insertFromMimeData(self, source):  # noqa: N802
        # Ctrl+V lewat menu/konteks: kalau clipboard berisi gambar, jadikan lampiran.
        if source is not None and source.hasImage():
            img = QApplication.clipboard().image()
            if not img.isNull():
                self.image_pasted.emit(img)
                return
        super().insertFromMimeData(source)


class AgentWorker(QThread):
    event = Signal(dict)

    def __init__(self, session: AgentSession, text: str, atts: list[dict], parent=None):
        super().__init__(parent)
        self.session, self.text, self.atts = session, text, atts

    def run(self):
        try:
            for raw in self.session.run(self.text, self.atts):
                payload = raw
                if isinstance(raw, str):
                    body = raw[6:] if raw.startswith("data: ") else raw
                    try:
                        payload = json.loads(body)
                    except json.JSONDecodeError:
                        continue
                if isinstance(payload, dict):
                    self.event.emit(payload)
        except Exception as e:  # noqa: BLE001
            self.event.emit({"type": "error", "message": f"{type(e).__name__}: {e}"})


class ApprovalBridge(QObject):
    """Worker thread asks, UI thread answers."""
    ask = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._evt = threading.Event()
        self._result = False
        self.ask.connect(self._on_ask, Qt.BlockingQueuedConnection)

    def _on_ask(self, payload: dict):
        self._result = self._show(payload)
        self._evt.set()

    def request(self, payload: dict) -> bool:
        self._evt.clear()
        self.ask.emit(payload)
        self._evt.wait(timeout=600)
        return self._result

    def _show(self, payload: dict) -> bool:
        dlg = DiffDialog(payload)
        return dlg.exec() == QDialog.Accepted


# --------------------------------------------------------------------- dialog
class DiffDialog(QDialog):
    def __init__(self, payload: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Setujui perubahan")
        self.resize(860, 620)
        lay = QVBoxLayout(self)
        action = payload.get("action", "write")
        head = QLabel()
        if action == "run_shell":
            head.setText(f"<b>Jalankan perintah shell?</b><br><code>{html.escape(payload.get('cmd', ''))}</code>")
        else:
            head.setText(f"<b>{action}</b> &nbsp;<code>{html.escape(payload.get('path', ''))}</code>"
                         f"<br><span style='color:{MUTED}'>+{payload.get('added', 0)} "
                         f"−{payload.get('removed', 0)} baris</span>")
        head.setWordWrap(True)
        lay.addWidget(head)

        if payload.get("diff"):
            view = QTextBrowser()
            view.setFont(QFont("Consolas, Menlo, monospace", 10))
            rows = []
            for line in payload["diff"].splitlines():
                esc = html.escape(line) or "&nbsp;"
                if line.startswith("+") and not line.startswith("+++"):
                    esc = f"<span style='color:{OK}'>{esc}</span>"
                elif line.startswith("-") and not line.startswith("---"):
                    esc = f"<span style='color:{ERR}'>{esc}</span>"
                elif line.startswith("@@"):
                    esc = f"<span style='color:{ACCENT}'>{esc}</span>"
                rows.append(f"<div style='white-space:pre;'>{esc}</div>")
            view.setHtml("".join(rows))
            lay.addWidget(view, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        reject = QPushButton("Tolak")
        reject.clicked.connect(self.reject)
        accept = QPushButton("Setujui")
        accept.setObjectName("primary")
        accept.clicked.connect(self.accept)
        row.addWidget(reject)
        row.addWidget(accept)
        lay.addLayout(row)


class SetupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.brand = config.load_brand()
        app_name = self.brand.get("app_name") or "AI Clyve IDE Lite"
        self.locked = bool(self.brand.get("lock_base_url") and self.brand.get("base_url"))
        self.setWindowTitle(f"{app_name} - API key")
        self.resize(460, 240 if self.locked else 330)
        lay = QVBoxLayout(self)
        title = QLabel("Hubungkan ke Clyve")
        title.setStyleSheet("font-size:17px;font-weight:700;")
        lay.addWidget(title)
        note = QLabel("Masukkan API key dari akun ai.clyve.id. Key disimpan lokal di komputer ini.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{MUTED};")
        lay.addWidget(note)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("tempel key di sini")
        self.base = QLineEdit(self.brand.get("base_url") or config.DEFAULT_BASE)
        lay.addWidget(QLabel("API key"))
        lay.addWidget(self.key)
        if not self.locked:
            lay.addWidget(QLabel("Base URL"))
            lay.addWidget(self.base)
        else:
            fixed = QLabel(f"Server: {self.brand.get('base_url')}")
            fixed.setStyleSheet("color:#98a1b6;font-size:11.5px;font-family:monospace;")
            lay.addWidget(fixed)
        lay.addStretch(1)
        self.err = QLabel("")
        self.err.setStyleSheet(f"color:{ERR};")
        lay.addWidget(self.err)
        btn = QPushButton("Hubungkan")
        btn.setObjectName("primary")
        btn.clicked.connect(self._ok)
        lay.addWidget(btn)

    def _ok(self):
        key = self.key.text().strip()
        base = (self.brand.get("base_url") if self.locked else self.base.text().strip()) \
            or config.DEFAULT_BASE
        if not key:
            self.err.setText("API key kosong")
            return
        btn = self.sender()
        btn.setEnabled(False)
        btn.setText("Menghubungkan...")
        QApplication.processEvents()
        models = config.list_models(base, key)
        if models == config.FALLBACK_MODELS:
            self.err.setText("key tidak terverifikasi oleh gateway. Coba lagi.")
            btn.setEnabled(True)
            btn.setText("Hubungkan")
            return
        self.models = models
        self.result_key, self.result_base = key, base
        self.accept()


class SttDialog(QDialog):
    """STT settings: OpenRouter (default) or any OpenAI-compatible /audio/transcriptions."""

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pengaturan suara (STT)")
        self.resize(520, 340)
        s = stt_mod.settings(cfg)
        lay = QVBoxLayout(self)
        head = QLabel("Speech to Text")
        head.setStyleSheet("font-size:16px;font-weight:700;")
        lay.addWidget(head)
        note = QLabel("Default: OpenRouter + openai/gpt-4o-mini-transcribe:floor "
                      "(termurah, ~$0.0009/menit). Bisa juga endpoint OpenAI-compatible lain.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{MUTED};")
        lay.addWidget(note)

        lay.addWidget(QLabel("Base URL"))
        self.base = QLineEdit(s["base_url"])
        lay.addWidget(self.base)
        lay.addWidget(QLabel("Model"))
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItems(stt_mod.MODELS)
        self.model.setCurrentText(s["model"])
        lay.addWidget(self.model)
        lay.addWidget(QLabel("API key (OpenRouter)"))
        self.key = QLineEdit(s["api_key"])
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("sk-or-v1-...")
        lay.addWidget(self.key)
        self.err = QLabel("")
        self.err.setStyleSheet(f"color:{ERR};")
        lay.addWidget(self.err)
        lay.addStretch(1)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Batal")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Simpan")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        row.addWidget(cancel)
        row.addWidget(save)
        lay.addLayout(row)

    def _save(self):
        key = self.key.text().strip()
        if not key:
            self.err.setText("API key kosong")
            return
        self.result = (self.base.text().strip() or stt_mod.DEFAULT_BASE,
                       self.model.currentText().strip() or stt_mod.DEFAULT_MODEL, key)
        self.accept()


class CheckpointsDialog(QDialog):
    def __init__(self, store: CheckpointStore, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Checkpoint / rollback")
        self.resize(560, 460)
        lay = QVBoxLayout(self)
        info = QLabel("Setiap tulis/hapus file otomatis dibuatkan snapshot. Pilih satu untuk dikembalikan.")
        info.setWordWrap(True)
        info.setStyleSheet(f"color:{MUTED}")
        lay.addWidget(info)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Tutup")
        close.clicked.connect(self.reject)
        self.restore = QPushButton("Rollback ke sini")
        self.restore.setObjectName("primary")
        self.restore.clicked.connect(self._restore)
        row.addWidget(close)
        row.addWidget(self.restore)
        lay.addLayout(row)
        self._reload()

    def _reload(self):
        self.list.clear()
        for c in self.store.list():
            when = time.strftime("%d %b %H:%M", time.localtime(c.get("ts") or 0))
            it = QListWidgetItem(f"{when}  ·  {c.get('label')}  ·  {c.get('count')} file")
            it.setData(Qt.UserRole, c["id"])
            self.list.addItem(it)

    def _restore(self):
        it = self.list.currentItem()
        if not it:
            return
        ok, msg = self.store.restore(it.data(Qt.UserRole))
        QMessageBox.information(self, "Rollback", msg)
        self.accept()


# --------------------------------------------------------------------- window
class Bubble(QFrame):
    """Satu pesan chat: kartu berisi label peran + isi (rich text)."""

    def __init__(self, role: str, parent=None):
        super().__init__(parent)
        is_user = role == "user"
        self.setObjectName("userBubble" if is_user else "botBubble")
        self.setStyleSheet(
            "QFrame#userBubble{background:#22406f;border:1px solid #4f7cd6;border-radius:14px;}"
            "QFrame#botBubble{background:#151d2b;border:1px solid #2b3850;border-radius:14px;}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 11, 16, 13)
        lay.setSpacing(5)
        who = QLabel("KAMU" if is_user else "CLYVE")
        who.setStyleSheet(
            "color:#cfe0ff;font-size:10px;font-weight:700;letter-spacing:0.6px;"
            if is_user else
            f"color:{OK};font-size:10px;font-weight:700;letter-spacing:0.6px;")
        lay.addWidget(who)
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.RichText)
        self.body.setOpenExternalLinks(True)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        self.body.setStyleSheet(
            "color:#f4f8ff;font-size:13.5px;line-height:150%;background:transparent;" if is_user
            else f"color:{FG};font-size:13.5px;line-height:150%;background:transparent;")
        lay.addWidget(self.body)


class ChatView(QScrollArea):
    """Daftar bubble chat yang bisa discroll, dengan pemisahan kiri/kanan."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.inner = QWidget()
        self.inner.setObjectName("chatInner")
        self.lay = QVBoxLayout(self.inner)
        self.lay.setContentsMargins(20, 14, 20, 8)
        self.lay.setSpacing(10)
        self.lay.addStretch(1)
        self.setWidget(self.inner)
        self.setStyleSheet(
            f"QScrollArea{{background:{BG};border:none;}}"
            f"QWidget#chatInner{{background:{BG};}}")

    def clear(self):
        while self.lay.count():
            item = self.lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _row(self, widget, align_right: bool):
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        if align_right:
            rl.addStretch(1)
        rl.addWidget(widget)
        if not align_right:
            rl.addStretch(1)
        self.lay.addWidget(row)
        return row

    def add_bubble(self, role: str) -> Bubble:
        bubble = Bubble(role)
        avail = self.viewport().width() or self.width() or 900
        width = max(360, int(avail * (0.68 if role == "user" else 0.76)))
        bubble.setMinimumWidth(min(width, 520))
        bubble.setMaximumWidth(width)
        bubble.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._row(bubble, align_right=(role == "user"))
        self.scroll_bottom()
        return bubble

    def add_widget(self, widget):
        self._row(widget, align_right=False)
        self.scroll_bottom()

    def scroll_bottom(self):
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())


class MainWindow(QMainWindow):
    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self.session = AgentSession(cfg["api_key"], cfg["base_url"], cfg["model"],
                                    cfg["workspace"], extra_roots=cfg.get("extra_roots") or [],
                                    full_access=bool(cfg.get("full_access")))
        self.attachments: list[dict] = []
        self.worker: AgentWorker | None = None
        self.busy = False
        self.current_chat = ""
        self.bridge = ApprovalBridge(self)
        self.session.approval = self.bridge.request

        self.setWindowTitle(cfg.get("app_name") or "AI Clyve IDE Lite")
        self.resize(1280, 860)
        self._build()
        self._new_chat(first=True)
        self._refresh_models()

    # ------------------------------------------------------------------ build
    def _build(self):
        root = QWidget()
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---------------- sidebar
        side = QFrame()
        side.setObjectName("side")
        side.setFixedWidth(272)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 14, 14, 14)
        sl.setSpacing(10)

        brand = QLabel("◈  " + (self.cfg.get("app_name") or "AI Clyve IDE Lite"))
        brand.setObjectName("brand")
        sl.addWidget(brand)

        sl.addWidget(self._section("Workspace"))
        self.ws_label = QLabel(self.cfg["workspace"])
        self.ws_label.setWordWrap(False)
        self.ws_label.setStyleSheet(f"font-family:monospace;font-size:11.5px;color:#aab4c8;"
                                    f"background:{PANEL2};border:1px solid {LINE};"
                                    f"border-radius:7px;padding:6px 8px;")
        sl.addWidget(self.ws_label)
        row = QHBoxLayout()
        b_explore = QPushButton("Buka folder")
        b_explore.clicked.connect(self._open_workspace)
        b_switch = QPushButton("Ganti")
        b_switch.setFixedWidth(78)
        b_switch.clicked.connect(self._choose_workspace)
        row.setSpacing(6)
        row.addWidget(b_explore, 1)
        row.addWidget(b_switch)
        sl.addLayout(row)

        sl.addWidget(self._section("Akses folder"))
        self.roots_label = QLabel("hanya workspace")
        self.roots_label.setStyleSheet(f"color:#7d879f;font-size:11px;")
        sl.addWidget(self.roots_label)
        b_addroot = QPushButton("+ Folder ke izin")
        b_addroot.clicked.connect(self._add_root)
        sl.addWidget(b_addroot)
        self.full_access = QCheckBox("Akses seluruh disk")
        self.full_access.setStyleSheet(f"color:#aeb8cc;font-size:11.5px;spacing:8px;")
        self.full_access.setChecked(bool(self.cfg.get("full_access")))
        self.full_access.stateChanged.connect(self._toggle_full_access)
        sl.addWidget(self.full_access)

        sl.addWidget(self._section("Model"))
        self.model_box = QComboBox()
        self.model_box.setFixedHeight(34)
        self.model_box.currentTextChanged.connect(self._model_changed)
        sl.addWidget(self.model_box)

        sl.addWidget(self._section("Mode"))
        self.mode_box = QComboBox()
        self.mode_box.setFixedHeight(34)
        self.mode_box.addItems(["Auto", "Review", "Plan"])
        self.mode_box.currentTextChanged.connect(self._mode_changed)
        sl.addWidget(self.mode_box)

        sl.addWidget(self._section("Riwayat chat"))
        self.history = QListWidget()
        self.history.setMinimumHeight(120)
        self.history.setWordWrap(True)
        self.history.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.history.setStyleSheet(
            f"QListWidget{{background:{PANEL};border:none;color:#b8c2d6;}}"
            f"QListWidget::item{{padding:6px 8px;border-radius:6px;}}"
            f"QListWidget::item:selected{{background:#161f31;color:#dbe6ff;}}")
        pal2 = self.history.palette()
        pal2.setColor(QPalette.Base, QColor(PANEL))
        pal2.setColor(QPalette.Text, QColor("#b8c2d6"))
        self.history.setPalette(pal2)
        self.history.itemClicked.connect(self._open_chat)
        sl.addWidget(self.history, 1)

        sl.addSpacing(4)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{LINE};")
        sl.addWidget(sep)
        sl.addSpacing(2)

        b_cp = QPushButton("Checkpoint / rollback")
        b_cp.clicked.connect(self._open_checkpoints)
        sl.addWidget(b_cp)

        keyrow = QHBoxLayout()
        keyrow.setSpacing(6)
        self.key_label = QLabel(config.mask(self.cfg.get("api_key", "")))
        self.key_label.setStyleSheet("font-family:monospace;font-size:10.5px;color:#7c869c;"
                                     "border:none;background:transparent;")
        keyrow.addWidget(self.key_label)
        keyrow.addStretch(1)
        b_key = QPushButton("Ganti key")
        b_key.setFixedWidth(84)
        b_key.clicked.connect(self._change_key)
        keyrow.addWidget(b_key)
        sl.addLayout(keyrow)

        outer.addWidget(side)

        # ---------------- main
        main = QWidget()
        ml = QVBoxLayout(main)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)

        top = QFrame()
        top.setStyleSheet(f"border-bottom:1px solid {LINE};")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(18, 10, 18, 10)
        self.crumb = QLabel(self.cfg["workspace"])
        self.crumb.setStyleSheet("font-family:monospace;font-size:11.5px;color:#a8b3c7;")
        self.crumb.setToolTip(self.cfg["workspace"])
        tl.addWidget(self.crumb)
        tl.addStretch(1)
        self.status = QLabel("siap")
        self.status.setStyleSheet(f"color:{MUTED};font-size:11.5px;")
        self.status.setMinimumWidth(120)
        self.status.setMaximumWidth(320)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        tl.addWidget(self.status)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("danger")
        self.stop_btn.setVisible(False)
        self.stop_btn.clicked.connect(self._stop)
        tl.addWidget(self.stop_btn)
        ml.addWidget(top)

        self.chat = ChatView()
        ml.addWidget(self.chat, 1)

        # attachments strip
        self.att_row = QHBoxLayout()
        self.att_row.setContentsMargins(20, 0, 20, 0)
        self.att_row.setSpacing(6)
        self.att_row.addStretch(1)
        att_holder = QWidget()
        att_holder.setLayout(self.att_row)
        ml.addWidget(att_holder)

        bottom = QWidget()
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(20, 8, 20, 16)
        bl.setSpacing(8)
        b_att = QPushButton("Lampir")
        b_att.setFixedSize(64, 46)
        b_att.setToolTip("Lampirkan gambar/PDF/dokumen")
        b_att.clicked.connect(self._pick_files)
        bl.addWidget(b_att)

        self.input = ChatInput()
        self.input.setObjectName("input")
        self.input.image_pasted.connect(self._paste_image)
        self.input.setPlaceholderText("Tulis instruksi... (Enter kirim, Shift+Enter baris baru, Ctrl+V tempel gambar)")
        self.input.setFixedHeight(52)
        self.input.installEventFilter(self)
        bl.addWidget(self.input, 1)
        self.send_btn = QPushButton("Kirim")
        self.send_btn.setObjectName("primary")
        self.send_btn.setFixedWidth(96)
        self.send_btn.setFixedHeight(52)
        self.send_btn.clicked.connect(self._send)
        bl.addWidget(self.send_btn)
        ml.addWidget(bottom)

        outer.addWidget(main, 1)
        self.setCentralWidget(root)

        QShortcut(QKeySequence("Ctrl+N"), self, self._new_chat)
        QShortcut(QKeySequence("Ctrl+O"), self, self._choose_workspace)

    def _section(self, text: str) -> QLabel:
        lab = QLabel(text.upper())
        lab.setProperty("class", "section")
        lab.setStyleSheet("color:#93a0b8;font-size:10px;font-weight:700;letter-spacing:1.1px;")
        return lab

    # ------------------------------------------------------------------ events
    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if obj is self.input and ev.type() == QEvent.KeyPress:
            if ev.key() in (Qt.Key_Return, Qt.Key_Enter) and not (ev.modifiers() & Qt.ShiftModifier):
                self._send()
                return True
        return super().eventFilter(obj, ev)

    # ------------------------------------------------------------------ sidebar
    def _refresh_models(self):
        models = config.list_models(self.cfg["base_url"], self.cfg["api_key"]) or config.FALLBACK_MODELS
        brand_models = config.load_brand().get("models") or []
        if brand_models:
            allowed = [m for m in models if m in brand_models]
            models = allowed or list(brand_models)
        self.model_box.blockSignals(True)
        self.model_box.clear()
        self.model_box.addItems(models)
        idx = self.model_box.findText(self.session.model)
        if idx >= 0:
            self.model_box.setCurrentIndex(idx)
        self.model_box.blockSignals(False)
        self.history.clear()
        for c in list_chats():
            raw = (c["title"] or "(baru)").strip().replace("\n", " ")
            title = raw if len(raw) <= 44 else raw[:43].rstrip() + "…"
            it = QListWidgetItem(title)
            it.setData(Qt.UserRole, c["id"])
            it.setToolTip(c["title"])
            self.history.addItem(it)
            if c["id"] == self.current_chat:
                self.history.setCurrentItem(it)

    def _open_workspace(self):
        import subprocess
        p = self.session.workspace
        try:
            if platform.system() == "Windows":
                import os
                os.startfile(p)  # noqa: S606
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", p])
            else:
                subprocess.Popen(["xdg-open", p])
        except Exception as e:  # noqa: BLE001
            self._set_status(f"gagal buka folder: {e}")

    def _choose_workspace(self):
        path = QFileDialog.getExistingDirectory(self, "Pilih workspace", self.session.workspace)
        if not path:
            return
        self.cfg["workspace"] = path
        config.save(self.cfg)
        self.session.set_workspace(path)
        self.ws_label.setText(path)
        self.crumb.setText(path)

    def _add_root(self):
        path = QFileDialog.getExistingDirectory(self, "Izinkan folder", self.session.workspace)
        if not path:
            return
        roots = list(self.cfg.get("extra_roots") or [])
        if path not in roots and path != self.session.workspace:
            roots.append(path)
        self.cfg["extra_roots"] = roots
        config.save(self.cfg)
        self.session.set_access(extra_roots=roots, full_access=bool(self.cfg.get("full_access")))
        self._render_roots()

    def _render_roots(self):
        roots = [r for r in (self.cfg.get("extra_roots") or []) if r != self.cfg.get("workspace")]
        self.roots_label.setText("\n".join(roots) if roots else "hanya workspace")

    def _toggle_full_access(self, state):
        self.cfg["full_access"] = bool(state)
        config.save(self.cfg)
        self.session.set_access(extra_roots=self.cfg.get("extra_roots") or [],
                                full_access=bool(state))

    def _model_changed(self, name):
        if not name:
            return
        self.cfg["model"] = name
        config.save(self.cfg)
        self.session.set_model(name)
        self._set_status(f"model: {name}")

    def _mode_changed(self, name):
        self.session.set_mode(name.lower())
        self._set_status(f"mode: {name.lower()}")

    def _change_key(self):
        dlg = SetupDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self.cfg["api_key"] = dlg.result_key
            self.cfg["base_url"] = dlg.result_base
            config.save(self.cfg)
            self.session.api_key = dlg.result_key
            self.session.base_url = dlg.result_base
            self.key_label.setText(config.mask(dlg.result_key))
            self._refresh_models()

    def _open_checkpoints(self):
        CheckpointsDialog(CheckpointStore(config.config_dir(), self.current_chat), self).exec()

    # ------------------------------------------------------------------ chats
    def _new_chat(self, first=False):
        import uuid
        self.current_chat = uuid.uuid4().hex[:12]
        self.session.reset()
        self.session.set_chat(self.current_chat)
        self.chat.clear()
        self._show_welcome()
        self._welcome = True
        if not first:
            self._refresh_models()

    def _open_chat(self, item):
        cid = item.data(Qt.UserRole)
        chat = load_chat(cid)
        if not chat:
            return
        if self.busy:
            self._set_status("selesaikan/stop dulu")
            return
        self.current_chat = cid
        self.session.set_chat(cid)
        self.session.load(chat.get("messages") or [])
        self._welcome = False
        self.chat.clear()
        for m in chat.get("messages") or []:
            if m["role"] == "user":
                self._append_user(m["content"])
            else:
                self._append_bot_html(md_to_html(m["content"] or ""))

    def _show_welcome(self):
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 60, 0, 0)
        lay.setSpacing(8)
        title = QLabel("Apa yang mau dibangun?")
        title.setStyleSheet(f"color:{FG};font-size:22px;font-weight:700;")
        title.setAlignment(Qt.AlignCenter)
        sub = QLabel("Agent ini bisa baca/tulis file, jalankan terminal, install paket, "
                     "query SQLite, hit API, buka folder dan browser.")
        sub.setStyleSheet(f"color:{MUTED};font-size:12px;")
        sub.setWordWrap(True)
        sub.setAlignment(Qt.AlignCenter)
        hint = QLabel("Mode Review = minta persetujuan tiap perubahan. "
                      "Mode Plan = rencana saja, tanpa eksekusi.")
        hint.setStyleSheet(f"color:{MUTED};font-size:12px;")
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addWidget(hint)
        self.chat.add_widget(box)

    # ------------------------------------------------------------------ chat ui
    def _append_user(self, text: str):
        bubble = self.chat.add_bubble("user")
        safe = html.escape(text).replace("\n", "<br>")
        bubble.body.setText(safe)

    def _append_bot_html(self, body_html: str, streaming=False) -> Bubble:
        bubble = self.chat.add_bubble("bot")
        bubble.body.setText(body_html)
        return bubble

    def _append_tool(self, name: str, detail: str, ok: bool | None):
        color = MUTED if ok is None else (OK if ok else ERR)
        icon = "⋯" if ok is None else ("✓" if ok else "✗")
        chip = QLabel(
            f"<span style='font-family:monospace;font-size:11px;color:{color};'>"
            f"{icon} <b>{html.escape(name)}</b> "
            f"<span style='color:#7d879f'>{html.escape(detail[:150])}</span></span>")
        chip.setTextFormat(Qt.RichText)
        chip.setWordWrap(True)
        chip.setMinimumWidth(340)
        chip.setStyleSheet(
            "background:#0d1420;border:1px solid #1c2536;border-radius:8px;padding:6px 9px;")
        self.chat.add_widget(chip)

    def _scroll(self):
        self.chat.scroll_bottom()

    def _set_status(self, text: str, color: str = MUTED):
        self.status.setText(text)
        self.status.setStyleSheet(f"color:{color};font-size:11.5px;")

    # ------------------------------------------------------------------ attach
    def _pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Lampirkan file", str(Path.home()))
        for f in files:
            try:
                data = Path(f).read_bytes()
                info = attach.save_upload(Path(self.session.workspace), Path(f).name, data)
                self.attachments.append(info)
            except Exception as e:  # noqa: BLE001
                self._set_status(f"gagal lampirkan: {e}", ERR)
        self._render_atts()

    def _paste_image(self, img):
        """Simpan gambar dari clipboard sebagai lampiran (tanpa perlu simpan manual)."""
        if len(self.attachments) >= attach.MAX_FILES:
            self._set_status(f"maksimal {attach.MAX_FILES} file per pesan", WARN)
            return
        try:
            tmp = Path(self.session.workspace or Path.home()) / f"paste_{int(time.time() * 1000)}.png"
            tmp.parent.mkdir(parents=True, exist_ok=True)
            if not img.save(str(tmp), "PNG"):
                raise RuntimeError("gambar clipboard tidak bisa dibaca")
            info = attach.save_upload(Path(self.session.workspace or Path.home()), tmp.name, tmp.read_bytes())
            self.attachments.append(info)
            self._render_atts()
            self._set_status(f"gambar ditempel: {info['name']}")
            try:
                tmp.unlink()
            except OSError:
                pass
        except Exception as e:  # noqa: BLE001
            self._set_status(f"gagal tempel gambar: {e}", ERR)

    def _render_atts(self):
        while self.att_row.count() > 1:
            item = self.att_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for i, a in enumerate(self.attachments):
            chip = QPushButton(f"{a['name'][:22]}  ×")
            chip.setToolTip(a["path"])
            chip.clicked.connect(lambda _=False, idx=i: (self.attachments.pop(idx), self._render_atts()))
            self.att_row.insertWidget(self.att_row.count() - 1, chip)

    # ------------------------------------------------------------------ send
    def _send(self):
        if self.busy:
            return
        text = self.input.toPlainText().strip()
        atts = list(self.attachments)
        if not text and not atts:
            return
        if getattr(self, "_welcome", False):
            self.chat.clear()
            self._welcome = False
        self.input.clear()
        self.attachments = []
        self._render_atts()
        self._append_user(text + ("\n📎 " + ", ".join(a["name"] for a in atts) if atts else ""))
        self.busy = True
        self.send_btn.setEnabled(False)
        self.stop_btn.setVisible(True)
        self._set_status("berpikir...", WARN)
        self._buf = []
        self._tools: dict[str, dict] = {}
        self._stream_started = False
        self._stream_bubble = None

        self.worker = AgentWorker(self.session, text or "Tolong baca lampiran ini.", atts, self)
        self.worker.event.connect(self._on_event)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _stop(self):
        if self.session:
            self.session.cancel.set()
        self._set_status("dihentikan", WARN)
        self.stop_btn.setVisible(False)

    def _on_event(self, ev: dict):
        t = ev.get("type")
        if t == "delta":
            chunk = ev.get("text") or ""
            self._buf.append(chunk)
            if not self._stream_started:
                self._stream_bubble = self.chat.add_bubble("bot")
                self._stream_started = True
            bubble = getattr(self, "_stream_bubble", None)
            if bubble is not None:
                raw = "".join(self._buf)
                bubble.body.setText(
                    f"<div style='white-space:pre-wrap;'>{html.escape(raw)}</div>")
                self.chat.scroll_bottom()
        elif t == "tool_start":
            self._append_tool(ev.get("name", "?"),
                              arg_summary(ev.get("name", ""), ev.get("args") or {}), None)
            self._scroll()
        elif t == "tool_end":
            self._append_tool(ev.get("name", "?"), str(ev.get("result", ""))[:160],
                              bool(ev.get("ok")))
            self._scroll()
        elif t == "done":
            self._finalize_stream(ev.get("content") or "")
            self._set_status("siap")
        elif t == "error":
            self._finalize_stream("")
            self._append_bot_html(f"<span style='color:{ERR}'>{html.escape(str(ev.get('message')))}</span>")
            self._set_status("error", ERR)

    def _finalize_stream(self, fallback: str):
        """Ganti teks streaming dengan markdown yang sudah dirender."""
        text = "".join(getattr(self, "_buf", [])) or fallback
        bubble = getattr(self, "_stream_bubble", None)
        if not text.strip():
            self._stream_started = False
            self._stream_bubble = None
            return
        if bubble is not None:
            bubble.body.setText(md_to_html(text))
        else:
            self._append_bot_html(md_to_html(text))
        self._stream_started = False
        self._stream_bubble = None
        self.chat.scroll_bottom()

    def _on_finished(self):
        self.busy = False
        self.send_btn.setEnabled(True)
        self.stop_btn.setVisible(False)
        self._stream_started = False
        self._stream_bubble = None
        try:
            save_chat(self.current_chat, self.session.snapshot())
        except Exception:  # noqa: BLE001
            pass
        self._refresh_models()
        self._scroll()


def run(cfg: dict | None = None) -> int:
    cfg = cfg or config.load()
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(QSS)
    app.setApplicationName("Clyve IDE")

    if not cfg.get("api_key"):
        dlg = SetupDialog()
        if dlg.exec() != QDialog.Accepted:
            return 1
        cfg["api_key"] = dlg.result_key
        cfg["base_url"] = dlg.result_base
        cfg["models"] = getattr(dlg, "models", config.FALLBACK_MODELS)
        config.save(cfg)

    win = MainWindow(cfg)
    win.show()
    return app.exec()
