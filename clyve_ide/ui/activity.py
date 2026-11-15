"""Blok aktivitas agent: satu grup 'Working · N' berisi baris tool yang update in-place.

Kenapa grup: kalau tiap event tool membuat widget baru, satu tool muncul dua kali
(start + end) dan chat penuh kartu duplikat. Di sini baris di-key oleh tool_call_id
dari agent, jadi `tool_end` hanya memperbarui baris yang sudah ada.
"""

from __future__ import annotations

import html

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from . import theme

VERBS = {
    "read_file": "Read file",
    "write_file": "Write file",
    "edit_file": "Edited",
    "make_dir": "Mkdir",
    "delete_path": "Delete",
    "list_dir": "List",
    "search_files": "Search",
    "run_shell": "Shell",
    "sqlite_query": "SQLite",
    "http_request": "HTTP",
    "open_path": "Open",
    "open_url": "Open URL",
    "system_info": "System",
}

GLYPHS = {
    "read_file": "\u25a4", "write_file": "\u25a4", "edit_file": "\u270e",
    "search_files": "\u2315", "run_shell": "$", "list_dir": "\u2261",
    "delete_path": "\u2715", "make_dir": "+", "sqlite_query": "\u25c7",
    "http_request": "\u21c5", "open_path": "\u2197", "open_url": "\u2197",
    "system_info": "\u2699",
}


class ElidedLabel(QLabel):
    """QLabel yang memotong teks dengan '…' sesuai lebar, bukan meluber."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = text
        self._color = None
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(40)
        self.setToolTip(text)
        self.setText(text)

    def setTextColor(self, color: str) -> None:  # noqa: N802
        """Warna teks eksplisit: paintEvent custom tidak membaca stylesheet color."""
        self._color = color
        self.update()

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text or ""
        self.setToolTip(self._full)
        super().setText(self._full)

    def paintEvent(self, ev):  # noqa: N802
        painter = QPainter(self)
        color = self._color or self.palette().color(self.foregroundRole()).name()
        painter.setPen(QColor(color))
        fm: QFontMetrics = self.fontMetrics()
        rect = self.contentsRect()
        text = fm.elidedText(self._full, Qt.ElideMiddle, rect.width())
        painter.drawText(rect, int(self.alignment()), text)
        painter.end()


class ToolRow(QFrame):
    """Satu langkah agent: ikon, verb, chip argumen, durasi, detail yang bisa dibuka."""

    def __init__(self, name: str, summary: str, parent=None):
        super().__init__(parent)
        self.name = name
        self._open = False
        self.setObjectName("toolRow")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(6, 2, 6, 2)
        head.setSpacing(8)

        self.glyph = QLabel(GLYPHS.get(name, "\u2022"))
        self.glyph.setFixedWidth(14)
        self.glyph.setAlignment(Qt.AlignCenter)
        self.glyph.setStyleSheet(f"color:{theme.DIM};font-family:monospace;font-size:11.5px;")
        head.addWidget(self.glyph)

        self.verb = QLabel(VERBS.get(name, name))
        self.verb.setFixedWidth(64)
        self.verb.setStyleSheet(f"color:{theme.MUTED};font-size:11.5px;")
        head.addWidget(self.verb)

        self.chip = ElidedLabel(summary)
        self.chip.setStyleSheet(
            f"color:#cfc6e8;font-family:monospace;font-size:11px;"
            f"background:{theme.PANEL2};border:1px solid {theme.LINE};"
            f"border-radius:5px;padding:1px 6px;")
        head.addWidget(self.chip, 1)

        self.stat = QLabel("")
        self.stat.setStyleSheet("font-family:monospace;font-size:10.5px;")
        head.addWidget(self.stat)

        self.dur = QLabel("")
        self.dur.setFixedWidth(46)
        self.dur.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.dur.setStyleSheet(f"color:{theme.DIM};font-family:monospace;font-size:10.5px;")
        head.addWidget(self.dur)

        outer.addLayout(head)

        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFont(self._mono())
        self.detail.setStyleSheet(
            f"QPlainTextEdit{{background:{theme.CODE_BG};border:1px solid {theme.LINE};"
            f"border-radius:6px;color:#c2b9dc;font-size:10.5px;padding:6px;}}")
        self.detail.setFixedHeight(132)
        self.detail.setVisible(False)
        box = QHBoxLayout()
        box.setContentsMargins(22, 2, 6, 6)
        box.addWidget(self.detail, 1)
        wrap = QWidget()
        wrap.setLayout(box)
        self.detail_holder = wrap
        wrap.setVisible(False)
        outer.addWidget(wrap)
        self._wrap = wrap

        self.setStyleSheet(
            "QFrame#toolRow{border:none;border-radius:6px;background:transparent;}"
            f"QFrame#toolRow:hover{{background:{theme.PANEL2};}}")

    @staticmethod
    def _mono() -> QFont:
        f = QFont("Consolas")
        f.setStyleHint(QFont.Monospace)
        f.setPointSizeF(9.0)
        return f

    def mousePressEvent(self, ev):  # noqa: N802
        if self.detail.toPlainText():
            self._open = not self._open
            self._wrap.setVisible(self._open)
        super().mousePressEvent(ev)

    def finish(self, ok: bool, ms: int | None, result: str = "", added: int = 0,
               removed: int = 0) -> None:
        color = theme.OK if ok else theme.ERR
        self.glyph.setText("\u2713" if ok else "\u2717")
        self.glyph.setStyleSheet(f"color:{color};font-family:monospace;font-size:11.5px;")
        if added or removed:
            self.stat.setText(f'<span style="color:{theme.OK}">+{added}</span> '
                              f'<span style="color:{theme.ERR}">-{removed}</span>')
            self.stat.setTextFormat(Qt.RichText)
        if ms is not None:
            self.dur.setText(f"{ms} ms" if ms < 1000 else f"{ms / 1000:.1f} s")
        if result:
            text = result if len(result) <= 4000 else result[:4000] + "\n… (dipotong)"
            self.detail.setPlainText(text)
            self.chip.setToolTip(result[:2000])


class ActivityGroup(QWidget):
    """Grup langkah agent untuk satu giliran percakapan."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: dict[str, ToolRow] = {}
        self._auto = 0
        self._done = False
        self._spin = 0
        self._frames = "\u25d0\u25d3\u25d1\u25d2"

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(1)

        self.head = QPushButton("Working  \u00b7  0")
        self.head.setObjectName("ghost")
        self.head.setCursor(Qt.PointingHandCursor)
        self.head.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;color:{theme.MUTED};"
            f"font-size:11.5px;text-align:left;padding:1px 4px;}}"
            f"QPushButton:hover{{color:{theme.FG};}}")
        self.head.clicked.connect(self._toggle)
        lay.addWidget(self.head)

        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 0, 0, 0)
        self.body_lay.setSpacing(1)
        lay.addWidget(self.body)

        self._timer = QTimer(self)
        self._timer.setInterval(420)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self) -> None:
        if self._done:
            return
        self._spin = (self._spin + 1) % len(self._frames)
        self._paint_header()

    def _paint_header(self) -> None:
        icon = "\u25be" if self._done else self._frames[self._spin]
        label = "Worked" if self._done else "Working"
        n = len(self._rows)
        self.head.setText(f"{icon}  {label}  \u00b7  {n}")

    def _toggle(self) -> None:
        self.body.setVisible(not self.body.isVisible())

    def _resolved_id(self, tid: str | None) -> str:
        if tid:
            return str(tid)
        self._auto += 1
        return f"auto-{self._auto}"

    def start_tool(self, tid: str | None, name: str, summary: str) -> ToolRow:
        key = self._resolved_id(tid)
        row = ToolRow(name, summary)
        self._rows[key] = row
        self.body_lay.addWidget(row)
        self._paint_header()
        return row

    def end_tool(self, tid: str | None, name: str, ok: bool, ms: int | None,
                 result: str = "", added: int = 0, removed: int = 0) -> None:
        key = str(tid) if tid else None
        row = self._rows.get(key) if key else None
        if row is None:
            # tool_end tanpa tool_start (mis. stream terputus): tetap tampilkan hasilnya
            row = self.start_tool(tid, name, (result or "").splitlines()[0][:120])
        row.finish(ok, ms, result, added, removed)
        if ok:
            self.body.setVisible(True)

    def finish(self) -> None:
        self._done = True
        self._timer.stop()
        self._paint_header()

    @property
    def count(self) -> int:
        return len(self._rows)


def tool_summary(name: str, args: dict) -> str:
    """Ringkasan argumen untuk chip monospace (menggantikan arg_summary lama)."""
    a = args or {}
    if not isinstance(a, dict):
        return str(a)[:120]
    if name in ("read_file", "write_file", "edit_file", "delete_path", "make_dir",
                "list_dir", "open_path"):
        return str(a.get("path") or ".")
    if name == "search_files":
        return f"{a.get('pattern', '')} {a.get('glob', '')}".strip()
    if name == "run_shell":
        return str(a.get("cmd") or "")
    if name == "sqlite_query":
        return f"{a.get('db', '')}: {str(a.get('sql', ''))[:80]}"
    if name == "http_request":
        return f"{str(a.get('method', 'GET')).upper()} {a.get('url', '')}"
    if name == "open_url":
        return str(a.get("url") or "")
    try:
        import json
        return json.dumps(a, ensure_ascii=False)[:120]
    except Exception:  # noqa: BLE001
        return html.escape(str(a))[:120]
