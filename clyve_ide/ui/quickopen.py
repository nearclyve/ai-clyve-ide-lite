"""Quick Open (Ctrl+P): cari file di workspace dengan fuzzy match.

Tree panel hanya menampilkan folder yang sudah dimuat, jadi mencari file
dalam (mis. app/Http/Controllers/Emotla/Controller.php) tidak praktis. Dialog
ini men-scan workspace sekali di background thread, menyimpan indeks, lalu
memfilter dengan skor fuzzy seperti quick open editor modern.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout,
)

from . import theme
from .files import HIDDEN

MAX_FILES = 20_000
# Folder yang tidak berguna untuk "buka file": dependency & artefak build.
SCAN_SKIP = HIDDEN | {"vendor", "storage/framework", ".git",
                      "Windows", "Program Files", "Program Files (x86)",
                      "ProgramData", "$Recycle.Bin", "System Volume Information",
                      "Recovery", "PerfLogs"}
MAX_RESULTS = 120
CACHE_SECONDS = 60


def fuzzy_score(needle: str, haystack: str) -> int:
    """Skor 0 = tidak cocok. Makin tinggi makin relevan.

    Prioritas: substring utuh, nama file cocok, lalu subsequence (huruf
    berurutan) supaya "gitu" tetap menemukan "gitutil.py".
    """
    n = (needle or "").strip().lower()
    h = (haystack or "").lower()
    if not n:
        return 1
    name = h.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    score = 0
    if n in name:
        score = 100 - max(0, len(name) - len(n))
    elif n in h:
        score = 60 - min(40, h.index(n))
    else:
        pos = -1
        for ch in n:
            pos = h.find(ch, pos + 1)
            if pos < 0:
                return 0
        score = 20
    if name.startswith(n):
        score += 25
    return score


class ScanWorker(QThread):
    done = Signal(list)

    def __init__(self, root: str, parent=None):
        super().__init__(parent)
        self.root = root
        self.cancel = False

    def run(self):
        out: list[str] = []
        root = self.root
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                # Hanya folder berat yang dilewati; .github/.env tetap bisa dicari.
                dirnames[:] = [d for d in dirnames if d not in SCAN_SKIP]
                for f in filenames:
                    full = os.path.join(dirpath, f)
                    rel = os.path.relpath(full, root).replace("\\", "/")
                    out.append(rel)
                    if len(out) >= MAX_FILES:
                        break
                if len(out) >= MAX_FILES or self.cancel:
                    break
        except Exception:  # noqa: BLE001
            pass
        out.sort()
        self.done.emit(out)


class QuickOpenDialog(QDialog):
    """Dialog pencarian file; `selected_path` diisi kalau user memilih."""

    def __init__(self, workspace: str, files: list[str] | None = None, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self.files = list(files or [])
        self.selected_path = ""
        self.worker: ScanWorker | None = None
        self.setWindowTitle("Cari file")
        self.resize(720, 460)
        self.setModal(True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Cari file di workspace... (ketik nama, Enter untuk buka)")
        self.input.setClearButtonEnabled(True)
        self.input.textChanged.connect(self._refresh)
        self.input.installEventFilter(self)
        lay.addWidget(self.input)

        self.list = QListWidget()
        self.list.setStyleSheet(
            f"QListWidget{{background:{theme.CODE_BG};border:1px solid {theme.LINE};"
            f"border-radius:8px;color:{theme.FG};font-family:monospace;font-size:11.5px;}}"
            f"QListWidget::item{{padding:4px 8px;}}"
            f"QListWidget::item:selected{{background:{theme.ACCENT_DARK};}}")
        self.list.itemActivated.connect(lambda _i: self._accept_current())
        lay.addWidget(self.list, 1)

        foot = QHBoxLayout()
        foot.setSpacing(6)
        self.hint = QLabel("memuat indeks file...")
        self.hint.setStyleSheet(f"color:{theme.DIM};font-size:10.5px;")
        foot.addWidget(self.hint)
        foot.addStretch(1)
        self.close_btn = QPushButton("Tutup")
        self.close_btn.setObjectName("ghost")
        self.close_btn.setToolTip("Tutup (Esc)")
        self.close_btn.clicked.connect(self.reject)
        foot.addWidget(self.close_btn)
        lay.addLayout(foot)

        if self.files:
            self._refresh()
        else:
            self._start_scan()

    def _start_scan(self):
        self.worker = ScanWorker(self.workspace, self)
        self.worker.done.connect(self._on_scan_done)
        self.worker.start()

    def _on_scan_done(self, files: list[str]) -> None:
        self.files = files
        self._refresh()

    def _refresh(self) -> None:
        needle = self.input.text().strip()
        self.list.clear()
        scored = []
        for rel in self.files:
            s = fuzzy_score(needle, rel)
            if s > 0:
                scored.append((s, rel))
        scored.sort(key=lambda t: (-t[0], len(t[1]), t[1]))
        for _score, rel in scored[:MAX_RESULTS]:
            item = QListWidgetItem(rel)
            item.setData(Qt.UserRole, rel)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        if not self.files:
            self.hint.setText("memuat indeks file...  (bisa ditutup kapan saja)")
        elif scored:
            self.hint.setText(f"{len(scored)} cocok dari {len(self.files)} file"
                              + (f" (menampilkan {MAX_RESULTS})" if len(scored) > MAX_RESULTS else ""))
        else:
            self.hint.setText(f"tidak ada file cocok dari {len(self.files)} indeks")

    def eventFilter(self, obj, ev):  # noqa: N802
        from PySide6.QtCore import QEvent
        if obj is self.input and ev.type() == QEvent.KeyPress:
            if ev.key() in (Qt.Key_Down, Qt.Key_Up):
                row = self.list.currentRow()
                row = min(self.list.count() - 1, row + 1) if ev.key() == Qt.Key_Down \
                    else max(0, row - 1)
                self.list.setCurrentRow(row)
                return True
            if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
                self._accept_current()
                return True
        return super().eventFilter(obj, ev)

    def _accept_current(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        rel = item.data(Qt.UserRole)
        self.selected_path = str(Path(self.workspace) / rel)
        self.accept()

    # ------------------------------------------------------------ close aman
    def _stop_worker(self) -> None:
        """Hentikan scan sebelum dialog dihancurkan.

        Tanpa ini QThread masih berjalan saat objeknya dihapus -> Qt abort
        ("QThread: Destroyed while thread is still running") dan app terlihat
        hang / tidak bisa ditutup di workspace besar.
        """
        if self.worker is not None and self.worker.isRunning():
            from PySide6.QtWidgets import QApplication
            self.worker.cancel = True
            deadline = time.time() + 3.0
            # tunggu sambil tetap memproses event supaya window tidak "not responding"
            while self.worker.isRunning() and time.time() < deadline:
                QApplication.processEvents()
                self.worker.wait(50)

    def reject(self) -> None:
        self._stop_worker()
        super().reject()

    def accept(self) -> None:
        self._stop_worker()
        super().accept()

    def closeEvent(self, ev):  # noqa: N802
        self._stop_worker()
        super().closeEvent(ev)

    def keyPressEvent(self, ev):  # noqa: N802
        if ev.key() == Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(ev)
