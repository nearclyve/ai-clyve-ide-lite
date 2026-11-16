"""Panel Changes: status git + diff per file + commit, gaya IDE.

Semua akses git lewat `clyve_ide.gitutil`, jadi panel ini tetap jalan (kosong)
kalau git tidak ada atau workspace bukan repo.
"""

from __future__ import annotations

import html
import time

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QScrollArea, QSizePolicy, QTextBrowser, QVBoxLayout, QWidget,
)

from .. import gitutil
from . import theme
from .activity import ElidedLabel
from .files import FilesTree

MAX_FILES = 12


def _mono(size: float = 9.0) -> QFont:
    f = QFont("Consolas")
    f.setStyleHint(QFont.Monospace)
    f.setPointSizeF(size)
    return f


def diff_to_html(text: str) -> str:
    """Unified diff -> HTML dengan gutter nomor baris lama/baru dan warna tambah/hapus."""
    rows: list[str] = []
    old = new = 0
    for line in text.splitlines():
        if line.startswith("@@"):
            head = line[2:].split("@@")[0].strip()
            try:
                old_part, new_part = head.split(" ")
                old = int(old_part[1:].split(",")[0])
                new = int(new_part[1:].split(",")[0])
            except Exception:  # noqa: BLE001
                old = new = 0
            rows.append(
                f"<div style='white-space:pre;color:{theme.ACCENT};'>"
                f"{' ' * 10}{html.escape(line)}</div>")
            continue
        if line.startswith(("diff --git", "index ", "--- ", "+++ ", "new file", "deleted file")):
            continue
        if line.startswith("+"):
            rows.append(_row(new, "+", line[1:], theme.OK, "#1d2b26"))
            new += 1
        elif line.startswith("-"):
            rows.append(_row(old, "-", line[1:], theme.ERR, "#2d1e24"))
            old += 1
        elif line.startswith("\\"):
            rows.append(f"<div style='white-space:pre;color:{theme.DIM};'>{' ' * 10}"
                        f"{html.escape(line)}</div>")
        else:
            body = line[1:] if line.startswith(" ") else line
            rows.append(_row(old, " ", body, theme.MUTED, None))
            old += 1
            new += 1
    return "".join(rows) or f"<div style='color:{theme.DIM}'>(diff kosong)</div>"


def _row(num: int, sign: str, body: str, color: str, bg: str | None) -> str:
    label = f"{num:>5}" if num else " " * 5
    pad = f"{' ' * 5}"
    bg_style = f"background-color:{bg};" if bg else ""
    return (f"<div style='white-space:pre;{bg_style}'>"
            f"<span style='color:{theme.DIM}'>{pad}</span>"
            f"<span style='color:{theme.DIM}'>{label}</span> "
            f"<span style='color:{color}'>{sign}</span>"
            f"<span style='color:{color}'>{html.escape(body)}</span></div>")


class FileHeader(QWidget):
    """Baris header file yang bisa diklik (QLabel biasa, bukan QPushButton:
    QPushButton tidak merender rich text, jadi warna +N/-N hilang)."""

    clicked = Signal()

    def __init__(self, info: dict, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(f"background:transparent;border-radius:5px;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 3, 6, 3)
        lay.setSpacing(6)

        self.path = ElidedLabel(info["path"])
        f = _mono(9.5)
        self.path.setFont(f)
        self.path.setTextColor(theme.FG)
        self.path.setToolTip(info["path"])
        lay.addWidget(self.path, 1)

        code = html.escape(info.get("code") or "M")
        badge_color = theme.WARN if info.get("untracked") else theme.MUTED
        if info.get("binary"):
            code = "BIN"
            badge_color = theme.DIM
        self.badge = QLabel(code)
        self.badge.setStyleSheet(f"color:{badge_color};font-family:monospace;font-size:9.5px;")
        lay.addWidget(self.badge)

        self.stat = QLabel(
            f"<span style='color:{theme.OK}'>&#43;{info.get('added', 0)}</span>"
            f"<span style='color:{theme.ERR}'>&#8722;{info.get('removed', 0)}</span>")
        self.stat.setTextFormat(Qt.RichText)
        self.stat.setStyleSheet("font-family:monospace;font-size:9.5px;")
        lay.addWidget(self.stat)

    def mousePressEvent(self, ev):  # noqa: N802
        self.clicked.emit()
        super().mousePressEvent(ev)

    def enterEvent(self, ev):  # noqa: N802
        self.setStyleSheet(f"background:{theme.PANEL2};border-radius:5px;")

    def leaveEvent(self, ev):  # noqa: N802
        self.setStyleSheet("background:transparent;border-radius:5px;")


class FileBlock(QWidget):
    """Header file + diff yang bisa dilipat."""

    def __init__(self, info: dict, diff_text: str, parent=None):
        super().__init__(parent)
        self._open = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        self.head = FileHeader(info)
        self.head.clicked.connect(self._toggle)
        lay.addWidget(self.head)

        self.view = QWidget()
        self._inner = QVBoxLayout(self.view)
        self._inner.setContentsMargins(0, 0, 0, 0)
        if info.get("binary"):
            note = QLabel("File biner (gambar/arsip) - isi tidak ditampilkan sebagai diff.")
            note.setStyleSheet(f"color:{theme.DIM};font-size:10.5px;padding:6px 8px;"
                               f"background:{theme.CODE_BG};border:1px solid {theme.LINE};"
                               f"border-radius:6px;")
            note.setWordWrap(True)
            self._inner.addWidget(note)
        else:
            browser = QTextBrowser()
            browser.setOpenExternalLinks(False)
            browser.setFont(_mono())
            browser.setStyleSheet(
                f"QTextBrowser{{background:{theme.CODE_BG};border:1px solid {theme.LINE};"
                f"border-radius:6px;padding:6px;}}")
            browser.setMaximumHeight(300)
            browser.setHtml(diff_to_html(diff_text))
            self._inner.addWidget(browser)
        self.view.setVisible(bool(self._open))
        lay.addWidget(self.view)

    def _toggle(self) -> None:
        self._open = not self._open
        self.view.setVisible(self._open)

    def open_diff(self, open_it: bool = True) -> None:
        self._open = open_it
        self.view.setVisible(open_it)


class _StatusWorker(QThread):
    """Hitung status git di thread terpisah.

    `git status`/`git diff` pada repo besar bisa makan detik-an (vendor,
    node_modules). Dulu dipanggil langsung di UI thread saat ganti workspace,
    sehingga app "Not Responding".
    """

    done = Signal(str, list, str, bool)

    def __init__(self, root: str, parent=None):
        super().__init__(parent)
        self.root = root

    def run(self):
        root = self.root
        try:
            rows = gitutil.files_with_diff(root) if root else []
            branch = gitutil.branch(root) if root else ""
            repo = gitutil.is_repo(root) if root else False
        except Exception:  # noqa: BLE001
            rows, branch, repo = [], "", False
        self.done.emit(root, rows, branch, repo)


class ChangesPanel(QFrame):
    """Kolom kanan: daftar perubahan + commit."""

    message = Signal(str, bool)  # teks, ok?
    refreshed = Signal(int)      # jumlah file berubah

    def __init__(self, root: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.setFixedWidth(344)
        self.root = root
        self._blocks: list[FileBlock] = []
        self._worker: _StatusWorker | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(12, 10, 8, 10)
        title = QLabel("Changes")
        title.setStyleSheet("font-size:12.5px;font-weight:600;")
        head.addWidget(title)
        self.count = QLabel("0")
        self.count.setStyleSheet(
            f"color:{theme.MUTED};font-size:10px;background:{theme.PANEL2};"
            f"border:1px solid {theme.LINE};border-radius:5px;padding:0 5px;")
        head.addWidget(self.count)
        head.addStretch(1)
        self.branch = QLabel("")
        self.branch.setStyleSheet(f"color:{theme.DIM};font-family:monospace;font-size:10px;")
        head.addWidget(self.branch)
        refresh = QPushButton("\u21bb")
        refresh.setObjectName("ghost")
        refresh.setFixedWidth(26)
        refresh.setToolTip("Refresh status git")
        refresh.clicked.connect(self.refresh)
        head.addWidget(refresh)
        lay.addLayout(head)

        commit_row = QHBoxLayout()
        commit_row.setContentsMargins(12, 0, 12, 8)
        commit_row.setSpacing(6)
        self.msg = QLineEdit()
        self.msg.setPlaceholderText("Commit message...")
        self.msg.setFixedHeight(28)
        self.msg.returnPressed.connect(self.commit)
        commit_row.addWidget(self.msg, 1)
        self.commit_btn = QPushButton("Commit")
        self.commit_btn.setObjectName("primary")
        self.commit_btn.setFixedHeight(28)
        self.commit_btn.setFixedWidth(88)
        self.commit_btn.clicked.connect(self.commit)
        commit_row.addWidget(self.commit_btn)
        lay.addLayout(commit_row)

        self.files = FilesTree(root)
        lay.addWidget(self.files)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(f"QScrollArea{{background:{theme.PANEL};border:none;}}")
        self.inner = QWidget()
        self.inner.setStyleSheet(f"background:{theme.PANEL};")
        self.inner_lay = QVBoxLayout(self.inner)
        self.inner_lay.setContentsMargins(8, 0, 8, 10)
        self.inner_lay.setSpacing(2)
        self.inner_lay.addStretch(1)
        self.scroll.setWidget(self.inner)
        lay.addWidget(self.scroll, 1)

        self.empty = QLabel("Tidak ada perubahan.\nEdit file lewat agent, diff muncul di sini.")
        self.empty.setStyleSheet(f"color:{theme.DIM};font-size:11px;padding:14px 8px;")
        self.empty.setWordWrap(True)
        self.inner_lay.insertWidget(0, self.empty)

    # ------------------------------------------------------------------ api
    def set_root(self, root: str) -> None:
        self.root = root or ""
        self.files.set_root(self.root)
        self.refresh()

    def refresh(self) -> None:
        """Mulai hitung status git di background; UI tidak diblokir."""
        self._clear_blocks()
        self._loading_root = self.root
        self.count.setText("\u2026")
        self.empty = QLabel("memuat status git...")
        self.empty.setStyleSheet(f"color:{theme.DIM};font-size:11px;padding:14px 8px;")
        self.empty.setWordWrap(True)
        self.inner_lay.insertWidget(0, self.empty)
        if not self.root:
            self.count.setText("0")
            self.empty.setText("Pilih workspace dulu.")
            return
        self._worker = _StatusWorker(self.root, self)
        self._worker.done.connect(self._on_status)
        self._worker.start()

    def _clear_blocks(self) -> None:
        while self.inner_lay.count() > 1:  # sisakan stretch terakhir
            item = self.inner_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._blocks = []

    def _on_status(self, root: str, rows: list, branch: str, repo: bool) -> None:
        if root != self.root:      # hasil dari workspace lama, buang
            return
        self._clear_blocks()
        self.count.setText(str(len(rows)))
        self.branch.setText(branch)
        if not rows:
            self.empty = QLabel("Tidak ada perubahan." if repo
                                else "Workspace ini bukan repo git.")
            self.empty.setStyleSheet(f"color:{theme.DIM};font-size:11px;padding:14px 8px;")
            self.empty.setWordWrap(True)
            self.inner_lay.insertWidget(0, self.empty)
            self.refreshed.emit(0)
            return
        for i, info in enumerate(rows[:MAX_FILES]):
            block = FileBlock(info, gitutil.diff(self.root, info["path"], staged=info["staged"]))
            block.open_diff(i == 0)
            self._blocks.append(block)
            self.inner_lay.insertWidget(self.inner_lay.count() - 1, block)
        if len(rows) > MAX_FILES:
            more = QLabel(f"+{len(rows) - MAX_FILES} file lain")
            more.setStyleSheet(f"color:{theme.DIM};font-size:10.5px;padding:4px 6px;")
            self.inner_lay.insertWidget(self.inner_lay.count() - 1, more)
        self.refreshed.emit(len(rows))

    def stop(self) -> None:
        """Hentikan worker sebelum panel dihancurkan (hindari QThread abort)."""
        w = getattr(self, "_worker", None)
        if w is not None and w.isRunning():
            from PySide6.QtWidgets import QApplication
            deadline = time.time() + 2.0
            while w.isRunning() and time.time() < deadline:
                QApplication.processEvents()
                w.wait(50)

    def commit(self) -> None:
        ok, msg = gitutil.commit(self.root, self.msg.text())
        self.message.emit(msg, ok)
        if ok:
            self.msg.clear()
            self.refresh()
