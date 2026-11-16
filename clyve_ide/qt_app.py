"""Clyve IDE desktop UI - native Qt widgets (PySide6), no webview.

Backend is the same agent/tools used by the web UI; this module only replaces
the presentation layer and adds review/plan modes plus checkpoint rollback.
"""

from __future__ import annotations

import html
import json
import platform
import re
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QKeySequence, QPalette, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QTextBrowser, QVBoxLayout, QWidget,
)

from . import attachments as attach
from . import config
from . import gitutil
from . import stt as stt_mod
from .agent import AgentSession
from .checkpoints import CheckpointStore
from .server import list_chats, load_chat, save_chat
from .ui import theme
from .ui.activity import ActivityGroup, tool_summary
from .ui.changes import ChangesPanel
from .ui.terminal import TerminalPanel
from .version import __version__

# palet tunggal: semua warna berasal dari clyve_ide/ui/theme.py
BG = theme.BG
PANEL = theme.PANEL
PANEL2 = theme.PANEL2
LINE = theme.LINE
FG = theme.FG
MUTED = theme.MUTED
ACCENT = theme.ACCENT
OK = theme.OK
ERR = theme.ERR
WARN = theme.WARN

QSS = theme.QSS  # satu sumber gaya: clyve_ide/ui/theme.py


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


# --------------------------------------------------------------------- worker
class ChatInput(QPlainTextEdit):
    """Input chat: paste gambar (Ctrl+V) + tinggi ikut isi (auto-grow)."""

    image_pasted = Signal(object)
    MIN_H = 46
    MAX_H = 190

    def __init__(self, parent=None):
        super().__init__(parent)
        self.textChanged.connect(self._auto_grow)

    def _auto_grow(self) -> None:
        """Kotak input tumbuh mengikuti baris teks (termasuk wrap), maks MAX_H.

        QTextDocument.size() mengembalikan jumlah blok, bukan piksel, jadi tinggi
        dihitung dari font metrics per baris + wrap sesuai lebar viewport.
        """
        fm = self.fontMetrics()
        width = max(120, self.viewport().width() - 4)
        total = 0
        block = self.document().begin()
        while block.isValid():
            rect = fm.boundingRect(0, 0, width, 10000, int(Qt.TextWordWrap), block.text())
            total += max(fm.lineSpacing(), rect.height())
            if total > self.MAX_H:
                total = self.MAX_H
                break
            block = block.next()
        h = max(self.MIN_H, min(self.MAX_H, total + 16))
        if abs(h - self.height()) > 1:
            self.setFixedHeight(h)

    def reset_height(self) -> None:
        self.setFixedHeight(self.MIN_H)

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
        # Delta digabung dulu (~16x/detik) sebelum menyeberang ke UI thread.
        # Satu signal per token membuat event loop Qt banjir saat jawaban panjang.
        try:
            pending = ""
            last = time.time()
            for raw in self.session.run(self.text, self.atts):
                payload = raw
                if isinstance(raw, str):
                    body = raw[6:] if raw.startswith("data: ") else raw
                    try:
                        payload = json.loads(body)
                    except json.JSONDecodeError:
                        continue
                if not isinstance(payload, dict):
                    continue
                if payload.get("type") == "delta":
                    pending += payload.get("text") or ""
                    now = time.time()
                    if pending and now - last >= 0.06:
                        self.event.emit({"type": "delta", "text": pending})
                        pending = ""
                        last = now
                    continue
                if pending:
                    self.event.emit({"type": "delta", "text": pending})
                    pending = ""
                    last = time.time()
                self.event.emit(payload)
            if pending:
                self.event.emit({"type": "delta", "text": pending})
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
            f"QFrame#userBubble{{background:{theme.INPUT};border:1px solid #3d3357;"
            f"border-radius:13px;}}"
            f"QFrame#botBubble{{background:{theme.PANEL};border:1px solid {theme.LINE};"
            f"border-radius:13px;}}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 11, 16, 13)
        lay.setSpacing(5)
        who = QLabel("KAMU" if is_user else "CLYVE")
        who.setStyleSheet(
            f"color:{theme.MUTED};font-size:9.5px;font-weight:700;letter-spacing:0.7px;"
            if is_user else
            f"color:{theme.ACCENT};font-size:9.5px;font-weight:700;letter-spacing:0.7px;")
        lay.addWidget(who)
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.RichText)
        self.body.setOpenExternalLinks(True)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        self.body.setStyleSheet(
            f"color:{FG};font-size:13.5px;line-height:150%;background:transparent;")
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
        self._follow = True
        bar = self.verticalScrollBar()
        bar.valueChanged.connect(self._on_scroll)
        bar.rangeChanged.connect(self._on_range)
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

    def _row(self, widget, align_right: bool, follow: bool = True):
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        if align_right:
            rl.addStretch(1)
            rl.addWidget(widget)
        else:
            rl.addWidget(widget, 1)
        self.lay.addWidget(row)
        if follow:
            self.scroll_bottom(force=True)
        return row

    def add_bubble(self, role: str) -> Bubble:
        follow = self._at_bottom()
        bubble = Bubble(role)
        avail = self.viewport().width() or self.width() or 900
        width = max(360, int(avail * (0.68 if role == "user" else 0.76)))
        bubble.setMinimumWidth(min(width, 520))
        bubble.setMaximumWidth(width)
        bubble.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._row(bubble, align_right=(role == "user"), follow=follow)
        return bubble

    def add_widget(self, widget):
        follow = self._at_bottom()
        self._row(widget, align_right=False, follow=follow)

    def _on_scroll(self, value: int) -> None:
        bar = self.verticalScrollBar()
        if value >= bar.maximum() - 8:
            self._follow = True
        elif value < bar.maximum() - 32:
            self._follow = False

    def _on_range(self, _min: int, maximum: int) -> None:
        if self._follow:
            self.verticalScrollBar().setValue(maximum)

    def _at_bottom(self) -> bool:
        bar = self.verticalScrollBar()
        return self._follow or bar.value() >= bar.maximum() - 32

    def scroll_bottom(self, force: bool = False):
        """Scroll ke bawah, kecuali user sedang membaca pesan lama."""
        if not force and not self._at_bottom():
            return
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())
        # satu putaran event lagi: tinggi konten baru final setelah layout
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))


class MainWindow(QMainWindow):
    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self.session = AgentSession(cfg["api_key"], cfg["base_url"], cfg["model"],
                                    cfg["workspace"], extra_roots=cfg.get("extra_roots") or [],
                                    full_access=bool(cfg.get("full_access")))
        self.attachments: list[dict] = []
        self.queue: list[dict] = []
        self.worker: AgentWorker | None = None
        self.busy = False
        self.current_chat = ""
        self.bridge = ApprovalBridge(self)
        self.session.approval = self.bridge.request

        # Streaming di-throttle: render maksimal ~14x/detik. Dulu tiap token
        # memanggil setText + relayout bubble, dan itu yang membuat PC lag saat
        # agent membalas panjang.
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(70)
        self._flush_timer.timeout.connect(self._flush_stream)

        # Timer status: user bisa lihat apakah yang lama itu jaringan/model
        # ("berpikir 12.4s") atau app-nya ("membalas 2.1s").
        self._t0 = 0.0
        self._first_delta_at = 0.0
        self._had_delta = False
        self._reason_buf: list[str] = []
        self._busy_timer = QTimer(self)
        self._busy_timer.setInterval(250)
        self._busy_timer.timeout.connect(self._tick_busy)

        self.setWindowTitle(cfg.get("app_name") or "AI Clyve IDE Lite")
        self.resize(1280, 860)
        self._build()
        self.setMinimumSize(1024, 700)
        self.showMaximized()
        self._new_chat(first=True)
        self._refresh_models()
        self._ensure_workspace()

    # ------------------------------------------------------------------ build
    def _build(self):
        root = QWidget()
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_sidebar())

        main = QWidget()
        ml = QVBoxLayout(main)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)
        ml.addWidget(self._build_topbar())

        self.chat = ChatView()
        ml.addWidget(self.chat, 1)
        ml.addWidget(self._build_composer())

        self.terminal = TerminalPanel(self.session.workspace)
        self.terminal.setVisible(False)
        self.terminal.setFixedHeight(190)
        ml.addWidget(self.terminal)
        outer.addWidget(main, 1)

        self.changes = ChangesPanel(self.session.workspace)
        self.changes.message.connect(self._on_git_message)
        outer.addWidget(self.changes)

        self.setCentralWidget(root)
        QShortcut(QKeySequence("Ctrl+N"), self, self._new_chat)
        QShortcut(QKeySequence("Ctrl+O"), self, self._choose_workspace)
        QShortcut(QKeySequence("Ctrl+J"), self, self._toggle_terminal)
        QShortcut(QKeySequence("Ctrl+K"), self, lambda: self.search.setFocus())
        QTimer.singleShot(250, self.changes.refresh)
        # cek update otomatis hanya untuk build terpasang (.exe), bukan `python app.py`
        if getattr(sys, "frozen", False):
            QTimer.singleShot(1500, lambda: self._check_update(silent=True))

    # ---------------------------------------------------------- build: sidebar
    def _build_sidebar(self) -> QFrame:
        side = QFrame()
        side.setObjectName("side")
        side.setFixedWidth(252)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 12, 12, 10)
        sl.setSpacing(8)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(4)
        brand = QLabel("◈  " + (self.cfg.get("app_name") or "AI Clyve IDE Lite"))
        brand.setObjectName("brand")
        brand_row.addWidget(brand)
        brand_row.addStretch(1)
        ver = QLabel("v" + __version__)
        ver.setStyleSheet(f"color:{theme.DIM};font-family:monospace;font-size:9.5px;")
        brand_row.addWidget(ver)
        sl.addLayout(brand_row)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search threads")
        self.search.setFixedHeight(28)
        self.search.setStyleSheet(
            f"QLineEdit{{background:{theme.PANEL2};border:1px solid {theme.LINE};"
            f"border-radius:7px;padding:3px 9px;font-size:11.5px;}}")
        self.search.textChanged.connect(self._filter_threads)
        sl.addWidget(self.search)

        self.new_chat_btn = QPushButton("+  New thread")
        self.new_chat_btn.setCursor(Qt.PointingHandCursor)
        self.new_chat_btn.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;border-radius:6px;"
            f"color:{theme.MUTED};text-align:left;padding:4px 8px;font-size:12px;}}"
            f"QPushButton:hover{{background:{theme.PANEL2};color:{FG};}}")
        self.new_chat_btn.clicked.connect(self._new_chat)
        sl.addWidget(self.new_chat_btn)

        sl.addWidget(self._section("Threads"))
        self.history = QListWidget()
        self.history.setMinimumHeight(120)
        self.history.setWordWrap(True)
        self.history.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.history.setStyleSheet(
            f"QListWidget{{background:transparent;border:none;color:{theme.MUTED};}}"
            f"QListWidget::item{{padding:5px 8px;border-radius:6px;}}"
            f"QListWidget::item:selected{{background:{theme.PANEL2};color:{FG};}}")
        pal2 = self.history.palette()
        pal2.setColor(QPalette.Base, QColor(theme.PANEL))
        pal2.setColor(QPalette.Text, QColor(theme.MUTED))
        self.history.setPalette(pal2)
        self.history.itemClicked.connect(self._open_chat)
        sl.addWidget(self.history, 1)

        sl.addWidget(self._divider())

        ws_head = QHBoxLayout()
        ws_head.setSpacing(4)
        ws_head.addWidget(self._section("Workspace"))
        ws_head.addStretch(1)
        b_open = QPushButton("Buka")
        b_open.setObjectName("ghost")
        b_open.setFixedHeight(20)
        b_open.setStyleSheet(f"QPushButton{{font-size:10.5px;color:{theme.MUTED};"
                             f"background:transparent;border:none;padding:0 4px;}}"
                             f"QPushButton:hover{{color:{FG};}}")
        b_open.clicked.connect(self._open_workspace)
        ws_head.addWidget(b_open)
        b_switch = QPushButton("Ganti")
        b_switch.setObjectName("ghost")
        b_switch.setFixedHeight(20)
        b_switch.setStyleSheet(f"QPushButton{{font-size:10.5px;color:{theme.MUTED};"
                               f"background:transparent;border:none;padding:0 4px;}}"
                               f"QPushButton:hover{{color:{FG};}}")
        b_switch.clicked.connect(self._choose_workspace)
        ws_head.addWidget(b_switch)
        sl.addLayout(ws_head)

        self.ws_label = QLabel(self.cfg["workspace"])
        self.ws_label.setWordWrap(False)
        self.ws_label.setStyleSheet(
            f"font-family:monospace;font-size:10.5px;color:{theme.MUTED};"
            f"background:{theme.PANEL2};border:1px solid {theme.LINE};"
            f"border-radius:6px;padding:4px 7px;")
        self.ws_label.setToolTip(self.cfg["workspace"])
        self.ws_label.setMinimumWidth(0)
        sl.addWidget(self.ws_label)
        QTimer.singleShot(0, lambda: self._set_ws_path(self.cfg["workspace"]))

        acc = QHBoxLayout()
        acc.setSpacing(6)
        b_addroot = QPushButton("+ Folder")
        b_addroot.setObjectName("ghost")
        b_addroot.setFixedHeight(22)
        b_addroot.setStyleSheet(f"QPushButton{{font-size:10.5px;color:{theme.MUTED};"
                                f"background:transparent;border:none;padding:0 4px;}}"
                                f"QPushButton:hover{{color:{FG};}}")
        b_addroot.setToolTip("Izinkan folder tambahan di luar workspace")
        b_addroot.clicked.connect(self._add_root)
        acc.addWidget(b_addroot)
        acc.addStretch(1)
        sl.addLayout(acc)

        self.roots_label = QLabel("hanya workspace")
        self.roots_label.setStyleSheet(f"color:{theme.DIM};font-size:10px;")
        self.roots_label.setWordWrap(True)
        sl.addWidget(self.roots_label)

        self.full_access = QCheckBox("Akses seluruh disk")
        self.full_access.setStyleSheet(f"color:{theme.MUTED};font-size:10.5px;spacing:6px;")
        self.full_access.setChecked(bool(self.cfg.get("full_access")))
        self.full_access.stateChanged.connect(self._toggle_full_access)
        sl.addWidget(self.full_access)

        sl.addWidget(self._divider())

        foot = QHBoxLayout()
        foot.setSpacing(4)
        b_cp = QPushButton("Checkpoint")
        b_cp.setObjectName("ghost")
        b_cp.setStyleSheet(f"QPushButton{{font-size:10.5px;color:{theme.MUTED};"
                           f"background:transparent;border:none;padding:2px 4px;}}"
                           f"QPushButton:hover{{color:{FG};}}")
        b_cp.setToolTip("Checkpoint / rollback file")
        b_cp.clicked.connect(self._open_checkpoints)
        foot.addWidget(b_cp)
        b_key = QPushButton("Ganti key")
        b_key.setObjectName("ghost")
        b_key.setStyleSheet(f"QPushButton{{font-size:10.5px;color:{theme.MUTED};"
                            f"background:transparent;border:none;padding:2px 4px;}}"
                            f"QPushButton:hover{{color:{FG};}}")
        b_key.clicked.connect(self._change_key)
        foot.addWidget(b_key)
        foot.addStretch(1)
        sl.addLayout(foot)

        self.key_label = QLabel(config.mask(self.cfg.get("api_key", "")))
        self.key_label.setStyleSheet(f"font-family:monospace;font-size:9.5px;color:{theme.DIM};")
        sl.addWidget(self.key_label)
        return side

    # ---------------------------------------------------------- build: topbar
    def _build_topbar(self) -> QFrame:
        top = QFrame()
        top.setObjectName("topbar")
        top.setFixedHeight(42)
        tl = QHBoxLayout(top)
        tl.setContentsMargins(14, 0, 8, 0)
        tl.setSpacing(8)

        self.title_label = QLabel("New thread")
        self.title_label.setStyleSheet("font-size:12.5px;font-weight:600;")
        tl.addWidget(self.title_label)

        self.ws_badge = QLabel(Path(self.cfg["workspace"]).name or "workspace")
        self.ws_badge.setStyleSheet(
            f"color:{theme.MUTED};font-size:10px;background:{theme.PANEL2};"
            f"border:1px solid {theme.LINE};border-radius:5px;padding:1px 6px;")
        tl.addWidget(self.ws_badge)
        tl.addStretch(1)

        self.status = QLabel("siap")
        self.status.setStyleSheet(f"color:{theme.DIM};font-size:11px;")
        self.status.setMinimumWidth(80)
        self.status.setMaximumWidth(320)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        tl.addWidget(self.status)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("danger")
        self.stop_btn.setVisible(False)
        self.stop_btn.clicked.connect(self._stop)
        tl.addWidget(self.stop_btn)

        self.update_btn = QPushButton("Cek update")
        self.update_btn.setObjectName("ghost")
        self.update_btn.setStyleSheet(f"QPushButton{{font-size:10.5px;color:{theme.MUTED};"
                                      f"background:transparent;border:none;padding:2px 6px;}}"
                                      f"QPushButton:hover{{color:{FG};}}")
        self.update_btn.setToolTip("Periksa versi terbaru")
        self.update_btn.clicked.connect(lambda: self._check_update(silent=False))
        tl.addWidget(self.update_btn)

        self.btn_changes = QPushButton("▤")
        self.btn_changes.setObjectName("ghost")
        self.btn_changes.setCheckable(True)
        self.btn_changes.setChecked(True)
        self.btn_changes.setFixedWidth(26)
        self.btn_changes.setToolTip("Panel Changes (Ctrl+Shift+G)")
        self.btn_changes.clicked.connect(self._toggle_changes)
        tl.addWidget(self.btn_changes)

        self.btn_term = QPushButton("❯_")
        self.btn_term.setObjectName("ghost")
        self.btn_term.setCheckable(True)
        self.btn_term.setFixedWidth(30)
        self.btn_term.setToolTip("Terminal (Ctrl+J)")
        self.btn_term.clicked.connect(self._toggle_terminal)
        tl.addWidget(self.btn_term)
        return top

    # -------------------------------------------------------- build: composer
    def _build_composer(self) -> QWidget:
        holder = QWidget()
        vl = QVBoxLayout(holder)
        vl.setContentsMargins(16, 0, 16, 10)
        vl.setSpacing(6)

        self.queue_row = QHBoxLayout()
        self.queue_row.setContentsMargins(0, 0, 0, 0)
        self.queue_row.setSpacing(6)
        self.queue_row.addStretch(1)
        queue_holder = QWidget()
        queue_holder.setLayout(self.queue_row)
        vl.addWidget(queue_holder)

        self.att_row = QHBoxLayout()
        self.att_row.setContentsMargins(0, 0, 0, 0)
        self.att_row.setSpacing(6)
        self.att_row.addStretch(1)
        att_holder = QWidget()
        att_holder.setLayout(self.att_row)
        vl.addWidget(att_holder)

        frame = QFrame()
        frame.setObjectName("composer")
        frame.setStyleSheet(
            f"QFrame#composer{{background:{theme.INPUT};border:1px solid {theme.LINE};"
            f"border-radius:12px;}}")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(8, 4, 8, 4)
        fl.setSpacing(0)

        r1 = QHBoxLayout()
        r1.setSpacing(6)
        b_att = QPushButton("+")
        b_att.setObjectName("ghost")
        b_att.setFixedSize(26, 26)
        b_att.setToolTip("Lampirkan gambar/PDF/dokumen (Ctrl+V untuk tempel gambar)")
        b_att.setStyleSheet(f"QPushButton{{background:transparent;border:1px solid {theme.LINE};"
                            f"border-radius:6px;color:{theme.MUTED};font-size:14px;}}"
                            f"QPushButton:hover{{color:{FG};border-color:#3d3357;}}")
        b_att.clicked.connect(self._pick_files)
        r1.addWidget(b_att, 0, Qt.AlignBottom)

        self.input = ChatInput()
        self.input.setObjectName("input")
        self.input.image_pasted.connect(self._paste_image)
        self.input.setPlaceholderText("Tulis instruksi...")
        self.input.setFixedHeight(46)
        self.input.setStyleSheet(
            f"QPlainTextEdit#input{{background:transparent;border:none;padding:4px 2px;"
            f"font-size:13px;}}")
        self.input.installEventFilter(self)
        r1.addWidget(self.input, 1)

        self.send_btn = QPushButton("\u2191")
        self.send_btn.setObjectName("primary")
        self.send_btn.setFixedSize(32, 30)
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setToolTip("Kirim (Enter). Saat agent masih membalas, pesan masuk antrian.")
        # padding 0 penting: QPushButton global punya padding 6px 11px, bikin glyph
        # "↑" terpotong sampai tombol terlihat kosong.
        self.send_btn.setStyleSheet(
            f"QPushButton{{background:{theme.ACCENT_DARK};border:none;border-radius:8px;"
            f"color:white;font-size:16px;font-weight:700;padding:0;min-height:0;"
            f"min-width:0;}}"
            f"QPushButton:hover{{background:{theme.ACCENT};}}"
            f"QPushButton:pressed{{background:#6d28d9;}}")
        self.send_btn.clicked.connect(self._send)
        r1.addWidget(self.send_btn, 0, Qt.AlignBottom)
        fl.addLayout(r1)

        r2 = QHBoxLayout()
        r2.setSpacing(2)
        self.model_box = QComboBox()
        self.model_box.setObjectName("mini")
        self.model_box.setMinimumWidth(148)
        self.model_box.setMaximumWidth(260)
        self.model_box.currentTextChanged.connect(self._model_changed)
        r2.addWidget(self.model_box)

        self.mode_box = QComboBox()
        self.mode_box.setObjectName("mini")
        self.mode_box.addItems(["Auto", "Review", "Plan"])
        self.mode_box.setToolTip("Auto = langsung jalan · Review = minta setuju tiap ubah · "
                                 "Plan = read-only")
        self.mode_box.currentTextChanged.connect(self._mode_changed)
        r2.addWidget(self.mode_box)

        ask = QLabel("Ask for approval")
        ask.setStyleSheet(f"color:{theme.DIM};font-size:10px;padding-left:6px;")
        r2.addWidget(ask)
        r2.addStretch(1)
        hint = QLabel("Enter kirim · Shift+Enter baris baru")
        hint.setStyleSheet(f"color:{theme.DIM};font-size:9.5px;")
        r2.addWidget(hint)
        fl.addLayout(r2)

        vl.addWidget(frame)
        return holder

    def _divider(self) -> QFrame:
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{theme.LINE};")
        return sep

    def _filter_threads(self, text: str) -> None:
        needle = (text or "").strip().lower()
        for i in range(self.history.count()):
            it = self.history.item(i)
            it.setHidden(bool(needle) and needle not in it.text().lower())

    def _toggle_changes(self) -> None:
        visible = not self.changes.isVisible()
        self.changes.setVisible(visible)
        self.btn_changes.setChecked(visible)
        if visible:
            self.changes.refresh()

    def _toggle_terminal(self) -> None:
        visible = not self.terminal.isVisible()
        self.terminal.setVisible(visible)
        self.btn_term.setChecked(visible)
        if visible and (self.terminal.proc is None
                        or self.terminal.proc.state() == self.terminal.proc.NotRunning):
            self.terminal.start()

    def _on_git_message(self, text: str, ok: bool) -> None:
        self._set_status(text[:80], OK if ok else ERR)

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
        self._refresh_history()

    def _refresh_history(self) -> None:
        """Isi daftar thread dari disk. Tanpa network: dipanggil tiap giliran selesai."""
        if getattr(self, "history", None) is None:
            return
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

    def _ensure_workspace(self) -> None:
        """Buat folder workspace default kalau belum ada + peringatan root drive."""
        ws = self.session.workspace
        try:
            Path(ws).mkdir(parents=True, exist_ok=True)
        except Exception as e:  # noqa: BLE001
            self._set_status(f"workspace tidak bisa dibuat: {e}", ERR)
            return
        if config.is_root_path(ws):
            self._set_status(f"peringatan: workspace {ws} adalah root drive", WARN)
            self.ws_label.setToolTip(
                f"{ws}\n\nRoot drive: agent bisa membaca/menulis seluruh drive. "
                "Sebaiknya pilih folder project.")

    def _set_ws_path(self, path: str) -> None:
        """Tampilkan path ter-elide di tengah supaya ekor folder tetap terbaca."""
        self.ws_label.ensurePolished()
        fm = self.ws_label.fontMetrics()
        avail = max(120, self.ws_label.width() - 18)
        self.ws_label.setText(fm.elidedText(path, Qt.ElideMiddle, avail))
        self.ws_label.setToolTip(path)

    def _choose_workspace(self):
        path = QFileDialog.getExistingDirectory(self, "Pilih workspace", self.session.workspace)
        if not path:
            return
        if config.is_root_path(path):
            ans = QMessageBox.question(
                self, "Workspace root drive",
                f"{path} adalah root drive.\n\nAgent akan boleh membaca dan menulis "
                "di seluruh drive itu. Yakin mau pakai ini?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans != QMessageBox.Yes:
                return
        self.cfg["workspace"] = path
        config.save(self.cfg)
        self.session.set_workspace(path)
        self._set_ws_path(path)
        self.ws_badge.setText(Path(path).name or "workspace")
        if getattr(self, "changes", None) is not None:
            self.changes.set_root(path)
        if getattr(self, "terminal", None) is not None:
            self.terminal.set_cwd(path)

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
        # QPushButton mengirim bool `checked`; jangan sampai dianggap `first`
        if not isinstance(first, bool):
            first = False
        import uuid
        self.current_chat = uuid.uuid4().hex[:12]
        self.title_label.setText("New thread")
        self.session.reset()
        self.session.set_chat(self.current_chat)
        self.chat.clear()
        self._show_welcome()
        self._welcome = True
        if not first:
            self._refresh_models()
            if getattr(self, "history", None) is not None:
                self.history.blockSignals(True)
                self.history.clearSelection()
                self.history.blockSignals(False)

    def _open_chat(self, item):
        cid = item.data(Qt.UserRole)
        chat = load_chat(cid)
        if not chat:
            return
        if self.busy:
            self._set_status("selesaikan/stop dulu")
            return
        self.current_chat = cid
        self.title_label.setText((item.text() or "Thread")[:60])
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

        chips = QHBoxLayout()
        chips.setSpacing(6)
        chips.addStretch(1)
        for label, prompt in (
            ("Audit repo ini", "Audit repo ini: cari bug, jelaskan temuan, lalu usulkan perbaikan."),
            ("Baca & ringkas", "Baca struktur repo ini dan ringkas arsitektur, entry point, serta risikonya."),
            ("Setup fitur baru", "Bantu saya menambah fitur baru di repo ini. Tanya dulu kebutuhan detailnya."),
        ):
            b = QPushButton(label)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                f"QPushButton{{background:{theme.PANEL2};border:1px solid {theme.LINE};"
                f"border-radius:14px;color:{theme.MUTED};font-size:11px;padding:5px 12px;}}"
                f"QPushButton:hover{{color:{theme.FG};border-color:{theme.ACCENT};}}")
            b.clicked.connect(lambda _=False, text=prompt: (self.input.setPlainText(text),
                                                            self.input.setFocus()))
            chips.addWidget(b)
        chips.addStretch(1)
        lay.addLayout(chips)
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

    def _activity_group(self) -> ActivityGroup:
        """Satu grup 'Working · N' per giliran; baris tool update in-place by tool_call_id."""
        if getattr(self, "_activity", None) is None:
            self._activity = ActivityGroup()
            self.chat.add_widget(self._activity)
        return self._activity

    def _scroll(self):
        self.chat.scroll_bottom()

    def _tick_busy(self) -> None:
        if not self._t0:
            return
        elapsed = time.time() - self._t0
        if self._first_delta_at:
            waited = self._first_delta_at - self._t0
            self._set_status(f"membalas {elapsed:.1f}s (tunggu awal {waited:.1f}s)", WARN)
        elif self._reason_buf:
            n = sum(len(x) for x in self._reason_buf)
            self._set_status(f"menalar {elapsed:.1f}s \u00b7 {n} karakter", WARN)
        else:
            self._set_status(f"berpikir {elapsed:.1f}s", WARN)

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
        """Kirim sekarang, atau masuk antrian kalau agent masih membalas."""
        text = self.input.toPlainText().strip()
        atts = list(self.attachments)
        if not text and not atts:
            return
        self.input.clear()
        self.input.reset_height()
        self.attachments = []
        self._render_atts()
        if self.busy:
            self._enqueue(text, atts)
            return
        self._dispatch(text, atts)

    def _enqueue(self, text: str, atts: list[dict]) -> None:
        self.queue.append({"text": text, "atts": atts})
        self._render_queue()
        self._set_status(f"antrian {len(self.queue)} pesan - dikirim otomatis setelah balasan", WARN)

    def _dequeue(self, idx: int) -> None:
        if 0 <= idx < len(self.queue):
            self.queue.pop(idx)
            self._render_queue()
            if not self.queue:
                self._set_status("berpikir..." if self.busy else "siap", WARN if self.busy else MUTED)

    def _render_queue(self) -> None:
        while self.queue_row.count() > 1:
            item = self.queue_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for i, q in enumerate(self.queue):
            body = (q["text"] or "").strip().splitlines()
            label = (body[0] if body else ("lampiran" if q["atts"] else "pesan"))[:30]
            chip = QPushButton(f"\u23f3 {i + 1}. {label}   \u00d7")
            chip.setToolTip("Masih dalam antrian - klik untuk membatalkan")
            chip.setStyleSheet(
                f"QPushButton{{background:{theme.PANEL2};border:1px solid {theme.LINE};"
                f"border-radius:6px;color:{theme.MUTED};font-size:10.5px;padding:2px 8px;}}"
                f"QPushButton:hover{{color:{theme.FG};border-color:#3d3357;}}")
            chip.clicked.connect(lambda _=False, idx=i: self._dequeue(idx))
            self.queue_row.insertWidget(self.queue_row.count() - 1, chip)

    def _dispatch(self, text: str, atts: list[dict]) -> None:
        """Mulai satu giliran agent. Dipanggil langsung atau dari antrian."""
        if getattr(self, "_welcome", False):
            self.chat.clear()
            self._welcome = False
        if self.title_label.text() in ("", "New thread"):
            first = (text or (atts[0]["name"] if atts else "")).strip().splitlines()
            self.title_label.setText((first[0] if first else "Thread")[:60])
        self._append_user(text + ("\nLampiran: " + ", ".join(a["name"] for a in atts) if atts else ""))
        self.chat.scroll_bottom(force=True)
        self.busy = True
        self.stop_btn.setVisible(True)
        self.send_btn.setToolTip("Kirim (Enter) - pesan berikutnya masuk antrian")
        self._t0 = time.time()
        self._first_delta_at = 0.0
        self._had_delta = False
        self._busy_timer.start()
        self._set_status("berpikir 0.0s", WARN)
        self._activity = None
        self._flush_timer.stop()
        self._reason_buf = []
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
        self._busy_timer.stop()
        if self.queue:
            self.queue.clear()
            self._render_queue()
            self._set_status("dihentikan - antrian dibatalkan", WARN)
        else:
            self._set_status("dihentikan", WARN)
        self.stop_btn.setVisible(False)

    def _on_event(self, ev: dict):
        t = ev.get("type")
        if t == "reasoning":
            think = ev.get("text") or ""
            if not think:
                return
            self._reason_buf.append(think)
            if not self._stream_started:
                self._stream_bubble = self.chat.add_bubble("bot")
                self._stream_started = True
            if not self._flush_timer.isActive():
                self._flush_timer.start()
            return
        if t == "delta":
            chunk = ev.get("text") or ""
            if not chunk:
                return
            self._buf.append(chunk)
            if not self._had_delta:
                self._had_delta = True
                self._first_delta_at = time.time()
            if not self._stream_started:
                self._stream_bubble = self.chat.add_bubble("bot")
                self._stream_started = True
            if not self._flush_timer.isActive():
                self._flush_timer.start()
        elif t == "tool_start":
            name = ev.get("name", "?")
            self._activity_group().start_tool(
                ev.get("id"), name, tool_summary(name, ev.get("args") or {}))
            self._scroll()
        elif t == "tool_end":
            name = ev.get("name", "?")
            self._activity_group().end_tool(
                ev.get("id"), name, bool(ev.get("ok")), ev.get("ms"),
                str(ev.get("result") or ""))
            self._scroll()
        elif t == "done":
            if getattr(self, "_activity", None) is not None:
                self._activity.finish()
            text = ev.get("content") or ""
            had_bubble = getattr(self, "_stream_bubble", None) is not None
            self._finalize_stream(text)
            elapsed = (time.time() - self._t0) if self._t0 else 0.0
            steps = ev.get("steps")
            tail = f" \u00b7 {steps} langkah" if steps else ""
            self._set_status(f"selesai {elapsed:.1f}s{tail}", OK)
            if not text.strip() and not self._had_delta and not had_bubble:
                # contoh nyata: clyve-free membalas [DONE] tanpa konten sama sekali
                self._append_bot_html(
                    f"<span style='color:{WARN}'>Model <b>{html.escape(self.session.model)}</b> "
                    "selesai tanpa mengirim teks. Coba model lain "
                    "(mis. <code>deepseek-v4.1-flash</code>) atau kirim ulang pesan.</span>")
        elif t == "error":
            self._finalize_stream("")
            self._append_bot_html(f"<span style='color:{ERR}'>{html.escape(str(ev.get('message')))}</span>")
            self._busy_timer.stop()
            elapsed = (time.time() - self._t0) if self._t0 else 0.0
            self._set_status(f"error setelah {elapsed:.1f}s", ERR)

    def _flush_stream(self) -> None:
        """Render isi buffer ke bubble streaming (dipanggil timer, bukan per token)."""
        bubble = getattr(self, "_stream_bubble", None)
        if bubble is None:
            return
        raw = "".join(self._buf)
        if raw.strip():
            preview = raw if len(raw) <= 20000 else "...\n" + raw[-20000:]
            bubble.body.setText(
                f"<div style='white-space:pre-wrap;'>{html.escape(preview)}</div>")
        else:
            reason = "".join(self._reason_buf)
            if not reason:
                return
            tail = reason[-600:]
            bubble.body.setText(
                f"<span style='color:{MUTED};font-style:italic;font-size:11.5px;'>"
                f"menalar... {html.escape(tail)}</span>")
        self.chat.scroll_bottom()

    def _finalize_stream(self, fallback: str):
        """Ganti teks streaming dengan markdown yang sudah dirender."""
        self._flush_timer.stop()
        text = "".join(getattr(self, "_buf", [])) or fallback
        bubble = getattr(self, "_stream_bubble", None)
        if not text.strip():
            reason = "".join(getattr(self, "_reason_buf", [])).strip()
            if bubble is not None and reason:
                # model reasoning yang berhenti tanpa jawaban akhir (mis. clyve-free)
                tail = reason[-1200:]
                bubble.body.setText(
                    f"<span style='color:{WARN}'>Model <b>{html.escape(self.session.model)}</b> "
                    "berhenti setelah menalar tanpa jawaban akhir. Coba kirim ulang "
                    "atau ganti model.</span><br><br>"
                    f"<span style='color:{MUTED};font-size:11px;font-style:italic;'>"
                    f"{html.escape(tail)}</span>")
                self._had_delta = True
            self._stream_started = False
            self._stream_bubble = None
            self._reason_buf = []
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
        self._busy_timer.stop()
        self.send_btn.setEnabled(True)
        self.stop_btn.setVisible(False)
        self._stream_started = False
        self._stream_bubble = None
        try:
            save_chat(self.current_chat, self.session.snapshot())
        except Exception:  # noqa: BLE001
            pass
        self._refresh_history()
        self._scroll()
        if getattr(self, "changes", None) is not None and self.changes.isVisible():
            self.changes.refresh()
        if self.queue:
            nxt = self.queue.pop(0)
            self._render_queue()
            self._set_status(f"melanjutkan antrian ({len(self.queue)} sisa)", WARN)
            QTimer.singleShot(120, lambda q=nxt: self._dispatch(q["text"], q["atts"]))
        else:
            self.send_btn.setToolTip("Kirim (Enter). Saat agent masih membalas, pesan masuk antrian.")

    def closeEvent(self, ev):  # noqa: N802
        # shell QProcess harus dimatikan eksplisit, kalau tidak Qt membunuhnya
        # saat interpreter keluar (warning "Destroyed while process is running").
        if getattr(self, "terminal", None) is not None:
            self.terminal.stop()
        super().closeEvent(ev)

    # ------------------------------------------------------------------ update
    def _check_update(self, silent: bool = True) -> None:
        if isinstance(getattr(self, "_upd", None), UpdateCheckWorker) and self._upd.isRunning():
            return
        self._upd_manual = not silent
        self._upd = UpdateCheckWorker(self)
        self._upd.checked.connect(self._on_update_checked)
        if not silent:
            self._set_status("cek update...", WARN)
        self._upd.start()

    def _on_update_checked(self, info: dict, error: str) -> None:
        if info:
            self._on_update_found(info)
            return
        if error:
            self._set_status(f"cek update gagal: {error[:48]}", ERR)
            if getattr(self, "_upd_manual", False):
                QMessageBox.warning(
                    self, "Cek update",
                    "Tidak bisa memeriksa update.\n\n" + error +
                    "\n\nKalau repo masih private, API GitHub membalas 404 untuk "
                    "pengguna anonim. Isi `update_url` di brand.json ke latest.json "
                    "publik, atau buka akses repo.")
            return
        self._set_status(f"v{__version__} terbaru", OK)
        if getattr(self, "_upd_manual", False):
            QMessageBox.information(self, "Cek update",
                                    f"Versi {__version__} sudah yang terbaru.")

    def _on_update_found(self, info: dict) -> None:
        dlg = UpdateDialog(info, self)
        if dlg.exec() == QDialog.Accepted and dlg.downloaded:
            try:
                from . import updater
                updater.launch_installer(dlg.downloaded)
            except Exception as e:  # noqa: BLE001
                self._set_status(f"gagal jalankan installer: {e}", ERR)
                return
            QApplication.quit()


# --------------------------------------------------------------- update app
class UpdateCheckWorker(QThread):
    """Cek rilis di thread sendiri; hasilnya (info, error) — error harus terlihat."""

    checked = Signal(dict, str)

    def run(self):
        try:
            from . import updater
            info, err = updater.check_detailed()
        except Exception as e:  # noqa: BLE001
            info, err = None, f"{type(e).__name__}: {e}"
        self.checked.emit(info or {}, err or "")


class UpdateDownloadWorker(QThread):
    progress = Signal(int, int)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.url = url

    def run(self):
        try:
            from . import updater
            path = updater.download(self.url, progress=lambda got, total: self.progress.emit(got, total))
            self.done.emit(str(path))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}")


class UpdateDialog(QDialog):
    """Prompt update: tampilkan versi + catatan rilis, unduh, lalu jalankan installer."""

    def __init__(self, info: dict, parent=None):
        super().__init__(parent)
        self.info = info
        self.downloaded = ""
        self.setWindowTitle("Update tersedia")
        self.resize(520, 380)
        lay = QVBoxLayout(self)
        title = QLabel(f"Versi {info.get('version')} tersedia")
        title.setStyleSheet("font-size:16px;font-weight:700;")
        lay.addWidget(title)
        sub = QLabel(f"Versi terpasang: {__version__}. Update diunduh, dipasang, "
                     "lalu app dibuka lagi otomatis.")
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color:{theme.MUTED};font-size:11.5px;")
        lay.addWidget(sub)
        notes = QTextBrowser()
        notes.setStyleSheet(f"QTextBrowser{{background:{theme.CODE_BG};border:1px solid {theme.LINE};"
                            f"border-radius:8px;padding:8px;color:{theme.MUTED};font-size:11px;}}")
        notes.setPlainText(info.get("notes") or "(tidak ada catatan rilis)")
        lay.addWidget(notes, 1)
        self.progress = QLabel("")
        self.progress.setStyleSheet(f"color:{theme.MUTED};font-size:11px;")
        lay.addWidget(self.progress)
        row = QHBoxLayout()
        row.addStretch(1)
        later = QPushButton("Nanti")
        later.clicked.connect(self.reject)
        row.addWidget(later)
        self.go = QPushButton("Update sekarang")
        self.go.setObjectName("primary")
        self.go.clicked.connect(self._start)
        row.addWidget(self.go)
        lay.addLayout(row)
        self.worker = None

    def _start(self):
        url = self.info.get("url") or ""
        if not url:
            self.progress.setText("URL installer tidak ada di rilis ini.")
            return
        self.go.setEnabled(False)
        self.progress.setText("mengunduh...")
        self.worker = UpdateDownloadWorker(url, self)
        self.worker.progress.connect(self._on_progress)
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_progress(self, got: int, total: int) -> None:
        if total:
            self.progress.setText(f"mengunduh... {got * 100 // total}% "
                                  f"({got / 1048576:.1f} / {total / 1048576:.1f} MB)")
        else:
            self.progress.setText(f"mengunduh... {got / 1048576:.1f} MB")

    def _on_done(self, path: str) -> None:
        self.downloaded = path
        self.progress.setText("installer siap. App akan ditutup lalu update dipasang.")
        self.accept()

    def _on_failed(self, msg: str) -> None:
        self.go.setEnabled(True)
        self.progress.setText(f"gagal unduh: {msg[:160]}")


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
