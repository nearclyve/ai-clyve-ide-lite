"""Panel terminal interaktif (QProcess + shell asli).

Catatan: ini BUKAN `run_shell` milik agent (itu subprocess sekali-jalan dengan
capture_output, tidak bisa dipakai untuk sesi interaktif). Di sini shell hidup
terus: stdin ditulis, stdout/stderr dibaca, cwd mengikuti workspace.
"""

from __future__ import annotations

import os
import platform

from PySide6.QtCore import QProcess, Qt
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QVBoxLayout,
)

from . import theme


def default_shell() -> tuple[str, list[str]]:
    if platform.system() == "Windows":
        return os.environ.get("COMSPEC", "cmd.exe"), ["/Q"]
    # tanpa -i: tidak ada warning job control, bash tetap baca perintah dari stdin.
    # Prompt digambar sendiri oleh UI, jadi PS1 tidak dibutuhkan.
    return os.environ.get("SHELL", "/bin/bash"), []


class TerminalPanel(QFrame):
    """Terminal mini: jalankan, ketik perintah, lihat output, restart/stop."""

    def __init__(self, cwd: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("termPanel")
        self.setStyleSheet(
            f"QFrame#termPanel{{background:{theme.CODE_BG};border-top:1px solid {theme.LINE};}}")
        self._cwd = cwd or os.getcwd()
        self._history: list[str] = []
        self._hist_idx = 0
        self.proc: QProcess | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(10, 5, 8, 5)
        head.setSpacing(6)
        title = QLabel("Terminal")
        title.setStyleSheet("font-size:11.5px;font-weight:600;")
        head.addWidget(title)
        self.status = QLabel("idle")
        self.status.setStyleSheet(f"color:{theme.DIM};font-size:10px;")
        head.addWidget(self.status)
        head.addStretch(1)
        self.run_btn = QPushButton("\u25b7 Run")
        self.run_btn.setObjectName("ghost")
        self.run_btn.setToolTip("Jalankan ulang shell di workspace")
        self.run_btn.clicked.connect(self.restart)
        head.addWidget(self.run_btn)
        self.stop_btn = QPushButton("\u25a0")
        self.stop_btn.setObjectName("ghost")
        self.stop_btn.setFixedWidth(26)
        self.stop_btn.setToolTip("Hentikan shell")
        self.stop_btn.clicked.connect(self.stop)
        head.addWidget(self.stop_btn)
        clear = QPushButton("Clear")
        clear.setObjectName("ghost")
        clear.clicked.connect(lambda: self.out.clear())
        head.addWidget(clear)
        lay.addLayout(head)

        self.out = QPlainTextEdit()
        self.out.setObjectName("termOut")
        self.out.setReadOnly(True)
        self.out.setFrameShape(QFrame.NoFrame)
        f = QFont("Consolas")
        f.setStyleHint(QFont.Monospace)
        f.setPointSizeF(9.0)
        self.out.setFont(f)
        self.out.setMaximumBlockCount(5000)
        self.out.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        # Windows: cmd /Q tidak menggemakan input, jadi UI yang mencetak perintah.
        # Unix: shell menggemakan input, jadi jangan dobel.
        self._echo_cmd = platform.system() == "Windows"
        lay.addWidget(self.out, 1)

        row = QHBoxLayout()
        row.setContentsMargins(10, 2, 10, 8)
        row.setSpacing(6)
        prompt = QLabel("\u276f")
        prompt.setStyleSheet(f"color:{theme.ACCENT};font-family:monospace;font-size:12px;")
        row.addWidget(prompt)
        self.input = QLineEdit()
        self.input.setPlaceholderText("ketik perintah, Enter untuk kirim")
        self.input.setStyleSheet(
            f"QLineEdit{{background:transparent;border:none;padding:2px;"
            f"font-family:monospace;font-size:11.5px;color:{theme.FG};}}")
        self.input.returnPressed.connect(self.send_command)
        self.input.installEventFilter(self)
        row.addWidget(self.input, 1)
        lay.addLayout(row)

        self.setMinimumHeight(90)

    # ------------------------------------------------------------------ proses
    def start(self) -> None:
        self.stop()
        prog, args = default_shell()
        self.proc = QProcess(self)
        self.proc.setWorkingDirectory(self._cwd)
        env = self.proc.processEnvironment()
        env.insert("TERM", "dumb")
        env.insert("PYTHONIOENCODING", "utf-8")
        self.proc.setProcessEnvironment(env)
        self.proc.setProgram(prog)
        self.proc.setArguments(args)
        if platform.system() == "Windows":
            # Tanpa CREATE_NO_WINDOW, shell console bisa memunculkan jendela
            # hitam sendiri walau app-nya windowed.
            def _no_console(proc_args):
                proc_args.flags |= 0x08000000  # CREATE_NO_WINDOW
            try:
                self.proc.setCreateProcessArgumentsModifier(_no_console)
            except Exception:  # noqa: BLE001
                pass
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.readyReadStandardError.connect(self._read)
        self.proc.finished.connect(self._finished)
        self.proc.start()
        if self.proc.waitForStarted(2500):
            self.status.setText("berjalan")
            self.status.setStyleSheet(f"color:{theme.OK};font-size:10px;")
            self._append(f"# shell: {prog} di {self._cwd}\n")
        else:
            self.status.setText("gagal start")
            self.status.setStyleSheet(f"color:{theme.ERR};font-size:10px;")
            self._append(f"# gagal menjalankan {prog}\n")

    def stop(self) -> None:
        if self.proc is not None:
            try:
                self.proc.kill()
                self.proc.waitForFinished(1000)
            except Exception:  # noqa: BLE001
                pass
            self.proc = None
        self.status.setText("idle")
        self.status.setStyleSheet(f"color:{theme.DIM};font-size:10px;")

    def restart(self) -> None:
        self.start()

    def set_cwd(self, path: str) -> None:
        if not path or path == self._cwd:
            return
        self._cwd = path
        self.restart()

    # ------------------------------------------------------------------ io
    def _read(self) -> None:
        if self.proc is None:
            return
        data = bytes(self.proc.readAllStandardOutput()) + bytes(self.proc.readAllStandardError())
        if data:
            self._append(data.decode("utf-8", errors="replace"))

    def _append(self, text: str) -> None:
        text = text.replace("\r\n", "\n").replace("\r", "")
        bar = self.out.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 8
        cur = self.out.textCursor()
        cur.movePosition(QTextCursor.End)
        self.out.setTextCursor(cur)
        self.out.insertPlainText(text)
        if at_bottom:
            bar.setValue(bar.maximum())

    def _finished(self, code: int, _status) -> None:
        self.status.setText(f"berhenti ({code})")
        self.status.setStyleSheet(f"color:{theme.WARN};font-size:10px;")
        self.proc = None

    def send_command(self) -> None:
        cmd = self.input.text()
        if not cmd.strip():
            return
        if self.proc is None or self.proc.state() == QProcess.NotRunning:
            self.start()
        if self.proc is None:
            return
        self._history.append(cmd)
        self._hist_idx = len(self._history)
        if self._echo_cmd:
            self._append(f"\u276f {cmd}\n")
        self.proc.write((cmd + "\n").encode("utf-8"))
        self.input.clear()

    def closeEvent(self, ev):  # noqa: N802
        self.stop()
        super().closeEvent(ev)

    def eventFilter(self, obj, ev):  # noqa: N802
        from PySide6.QtCore import QEvent
        if obj is self.input and ev.type() == QEvent.KeyPress:
            if ev.key() == Qt.Key_Up and self._history:
                self._hist_idx = max(0, self._hist_idx - 1)
                self.input.setText(self._history[self._hist_idx])
                return True
            if ev.key() == Qt.Key_Down and self._history:
                self._hist_idx = min(len(self._history), self._hist_idx + 1)
                self.input.setText("" if self._hist_idx >= len(self._history)
                                   else self._history[self._hist_idx])
                return True
        return super().eventFilter(obj, ev)
