"""Editor file bawaan app: tab, nomor baris, syntax highlight ringan, save.

Bukan VS Code, tapi cukup untuk buka file dari tree workspace, edit, dan
simpan tanpa keluar app. File biner / terlalu besar ditolak dengan pesan.
"""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QObject, QRect, QSize, Qt, QFileSystemWatcher, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QPainter, QSyntaxHighlighter, QTextCharFormat,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QMessageBox, QPlainTextEdit, QTabWidget, QTextEdit, QWidget,
)

from . import theme

MAX_BYTES = 5 * 1024 * 1024

LANG_BY_EXT = {
    ".py": "python", ".pyw": "python",
    ".js": "js", ".jsx": "js", ".ts": "js", ".tsx": "js", ".mjs": "js",
    ".php": "php", ".json": "json", ".html": "html", ".htm": "html",
    ".css": "css", ".scss": "css", ".md": "md", ".sql": "sql",
    ".yml": "yaml", ".yaml": "yaml", ".sh": "shell", ".bat": "shell",
    ".ps1": "shell", ".ini": "ini", ".toml": "ini", ".env": "ini", ".xml": "html",
}

KEYWORDS = {
    "python": "and as assert async await break class continue def del elif else except "
              "finally for from global if import in is lambda nonlocal not or pass raise "
              "return try while with yield True False None self",
    "js": "const let var function return if else for while do switch case break continue "
          "class extends new this import export from default async await try catch finally "
          "throw typeof instanceof null undefined true false interface type enum",
    "php": "function class public private protected static return if else elseif foreach "
           "for while switch case break continue new echo namespace use extends implements "
           "try catch finally throw null true false array",
    "sql": "select from where insert into values update set delete join left right inner "
           "outer on group by order limit offset as and or not null primary key create "
           "table alter drop index",
    "shell": "if then else fi for while do done case esac function export local echo cd "
             "set unset return exit",
}

COMMENTS = {
    "python": ["#"],
    "shell": ["#"],
    "ini": ["#", ";"],
    "js": ["//"],
    "php": ["//", "#"],
    "sql": ["--"],
}


def _fmt(color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Bold)
    if italic:
        f.setFontItalic(True)
    return f


class Highlighter(QSyntaxHighlighter):
    """Keyword/string/comment/number highlight, sengaja ringan (regex)."""

    def __init__(self, document: QTextDocument, ext: str):
        super().__init__(document)
        lang = LANG_BY_EXT.get(ext.lower(), "")
        self.rules: list[tuple[re.Pattern, QTextCharFormat]] = []
        words = KEYWORDS.get(lang, "")
        if words:
            kw_fmt = _fmt("#c792ea", bold=True)
            for w in words.split():
                self.rules.append((re.compile(rf"\b{re.escape(w)}\b"), kw_fmt))
        self.rules.append((re.compile(r"\b\d+(\.\d+)?\b"), _fmt("#f2b56d")))
        self.rules.append((re.compile(r'"[^"\\]*(\\.[^"\\]*)*"'), _fmt("#9ad7a0")))
        self.rules.append((re.compile(r"'[^'\\]*(\\.[^'\\]*)*'"), _fmt("#9ad7a0")))
        self.string_openers = COMMENTS.get(lang, [])

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        for pattern, fmt in self.rules:
            for m in pattern.finditer(text or ""):
                self.setFormat(m.start(), m.end() - m.start(), fmt)
        for opener in self.string_openers:
            idx = (text or "").find(opener)
            if idx >= 0:
                fmt = _fmt("#6f6688", italic=True)
                self.setFormat(idx, len(text) - idx, fmt)
                break


class LineNumberArea(QWidget):
    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.editor.line_number_width(), 0)

    def paintEvent(self, ev):  # noqa: N802
        self.editor.paint_line_numbers(ev)


class CodeEditor(QPlainTextEdit):
    """QPlainTextEdit + nomor baris + tab 4 spasi + auto-indent."""

    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.path = Path(path)
        f = QFont("Consolas")
        f.setStyleHint(QFont.Monospace)
        f.setPointSizeF(10.0)
        self.setFont(f)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setTabStopDistance(QFontMetrics(f).horizontalAdvance(" ") * 4)
        self.gutter = LineNumberArea(self)
        self.blockCountChanged.connect(lambda _n: self._update_margins())
        self.updateRequest.connect(self._on_update_request)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self.setStyleSheet(
            f"QPlainTextEdit{{background:{theme.CODE_BG};border:none;color:{theme.FG};"
            f"selection-background-color:{theme.ACCENT_DARK};}}")
        self.highlighter = Highlighter(self.document(), self.path.suffix)
        self._update_margins()
        self._highlight_current_line()

    # ------------------------------------------------------------ gutter
    def line_number_width(self) -> int:
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 12 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_margins(self) -> None:
        self.setViewportMargins(self.line_number_width(), 0, 0, 0)

    def _on_update_request(self, rect: QRect, dy: int) -> None:
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_margins()

    def resizeEvent(self, ev):  # noqa: N802
        super().resizeEvent(ev)
        cr = self.contentsRect()
        self.gutter.setGeometry(QRect(cr.left(), cr.top(),
                                      self.line_number_width(), cr.height()))

    def paint_line_numbers(self, ev) -> None:
        painter = QPainter(self.gutter)
        painter.fillRect(ev.rect(), QColor(theme.CODE_BG))
        painter.setPen(QColor(theme.DIM))
        block = self.firstVisibleBlock()
        num = block.blockNumber() + 1
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        while block.isValid() and top <= ev.rect().bottom():
            if block.isVisible() and bottom >= ev.rect().top():
                painter.drawText(0, top, self.gutter.width() - 6, self.fontMetrics().height(),
                                 Qt.AlignRight, str(num))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            num += 1

    def _highlight_current_line(self) -> None:
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(QColor("#1d1729"))
        sel.format.setProperty(0x100000, True)  # FullWidthSelection
        sel.cursor = self.textCursor()
        sel.cursor.clearSelection()
        self.setExtraSelections([sel])

    # ------------------------------------------------------------ input
    def keyPressEvent(self, ev):  # noqa: N802
        if ev.key() == Qt.Key_Tab:
            self.insertPlainText(" " * 4)
            return
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
            cur = self.textCursor()
            line = cur.block().text()
            indent = len(line) - len(line.lstrip(" "))
            super().keyPressEvent(ev)
            if indent:
                self.insertPlainText(" " * indent)
            return
        super().keyPressEvent(ev)


class EditorTabs(QObject):
    """Kelola tab editor di QTabWidget utama (tab 0 = Chat)."""

    status = Signal(str)

    def __init__(self, tabs: QTabWidget, parent=None):
        super().__init__(parent)
        self.tabs = tabs
        self._editors: dict[str, CodeEditor] = {}
        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self._on_file_changed)
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)

    # ------------------------------------------------------------ open/save
    def open_path(self, path: str) -> CodeEditor | None:
        p = Path(path)
        try:
            p = p.resolve()
        except OSError:
            pass
        key = str(p)
        if key in self._editors:
            self._focus(key)
            return self._editors[key]
        if not p.is_file():
            self.status.emit(f"bukan file: {p}")
            return None
        try:
            if p.stat().st_size > MAX_BYTES:
                self.status.emit(f"file terlalu besar untuk editor (> {MAX_BYTES // 1024 // 1024} MB)")
                return None
            raw = p.read_bytes()
            if b"\x00" in raw[:8192]:
                self.status.emit("file biner tidak bisa dibuka di editor")
                return None
            text = raw.decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            self.status.emit(f"gagal membuka {p.name}: {e}")
            return None

        ed = CodeEditor(p)
        ed.setPlainText(text)
        ed.document().setModified(False)
        ed.document().modificationChanged.connect(
            lambda mod, widget=ed: self._on_modified(widget, mod))
        idx = self.tabs.addTab(ed, p.name)
        self.tabs.setTabToolTip(idx, key)
        self._editors[key] = ed
        self.tabs.setCurrentIndex(idx)
        self.watcher.addPath(key)
        self.status.emit(f"dibuka: {p.name}")
        return ed

    def _focus(self, key: str) -> None:
        ed = self._editors.get(key)
        if ed is not None:
            self.tabs.setCurrentWidget(ed)

    def _on_modified(self, editor: CodeEditor, modified: bool) -> None:
        idx = self.tabs.indexOf(editor)
        if idx < 0:
            return
        name = editor.path.name
        self.tabs.setTabText(idx, ("\u25cf " if modified else "") + name)
        if modified:
            self.status.emit(f"{name} * belum disimpan")

    def save(self, editor: CodeEditor | None = None) -> bool:
        ed = editor or self.current_editor()
        if ed is None:
            return False
        try:
            ed.path.write_text(ed.toPlainText(), encoding="utf-8")
            ed.document().setModified(False)
            self.status.emit(f"disimpan: {ed.path.name}")
            return True
        except Exception as e:  # noqa: BLE001
            self.status.emit(f"gagal simpan {ed.path.name}: {e}")
            return False

    def current_editor(self) -> CodeEditor | None:
        w = self.tabs.currentWidget()
        return w if isinstance(w, CodeEditor) else None

    def close_tab(self, index: int) -> None:
        w = self.tabs.widget(index)
        if not isinstance(w, CodeEditor):
            return
        if w.document().isModified():
            ans = QMessageBox.question(
                self.tabs, "Simpan perubahan?",
                f"{w.path.name} belum disimpan. Simpan sekarang?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save)
            if ans == QMessageBox.Cancel:
                return
            if ans == QMessageBox.Save and not self.save(w):
                return
        key = str(w.path)
        self._editors.pop(key, None)
        if key in self.watcher.files():
            self.watcher.removePath(key)
        self.tabs.removeTab(index)

    # ------------------------------------------------------------ external
    def reload_path(self, path: str) -> bool:
        """Muat ulang isi file dari disk kalau tab terbuka dan tidak ada edit lokal."""
        key = str(Path(path).resolve())
        ed = self._editors.get(key)
        if ed is None:
            return False
        if ed.document().isModified():
            self.status.emit(f"{ed.path.name} berubah di disk (versi editor belum disimpan)")
            return False
        try:
            text = ed.path.read_text("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return False
        if text != ed.toPlainText():
            ed.setPlainText(text)
            ed.document().setModified(False)
            self.status.emit(f"dimuat ulang: {ed.path.name}")
        return True

    def _on_file_changed(self, path: str) -> None:
        key = str(Path(path).resolve())
        if key in self._editors:
            self.reload_path(key)
        # watcher bisa kehilangan file setelah save/rename: pasang ulang
        if Path(key).exists() and key not in self.watcher.files():
            self.watcher.addPath(key)
