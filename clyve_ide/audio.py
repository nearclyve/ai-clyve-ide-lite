"""Microphone capture for the Qt UI (16 kHz mono WAV) + transcription worker."""

from __future__ import annotations

import tempfile
import wave
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

try:
    from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices
    HAVE_AUDIO = True
except Exception:  # noqa: BLE001  (missing QtMultimedia backend)
    HAVE_AUDIO = False


class MicRecorder(QObject):
    """Push-to-record: start() opens the mic, stop() writes a WAV and returns its path."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source = None
        self._io = None
        self._buf = bytearray()
        self._fmt = None
        self._path: Path | None = None

    def available(self) -> bool:
        if not HAVE_AUDIO:
            return False
        try:
            return QMediaDevices.defaultAudioInput() is not None
        except Exception:  # noqa: BLE001
            return False

    def start(self) -> tuple[bool, str]:
        if not HAVE_AUDIO:
            return False, "QtMultimedia tidak tersedia di build ini"
        try:
            dev = QMediaDevices.defaultAudioInput()
            if dev is None or dev.isNull():
                return False, "tidak ada mikrofon"
            fmt = QAudioFormat()
            fmt.setSampleRate(16000)
            fmt.setChannelCount(1)
            fmt.setSampleFormat(QAudioFormat.Int16)
            if not dev.isFormatSupported(fmt):
                fmt = dev.preferredFormat()
            self._fmt = fmt
            self._buf = bytearray()
            self._source = QAudioSource(dev, fmt)
            self._io = self._source.start()
            if self._io is None:
                return False, "gagal membuka mikrofon"
            self._io.readyRead.connect(self._drain)
            return True, ""
        except Exception as e:  # noqa: BLE001
            self._source = None
            return False, f"{type(e).__name__}: {e}"

    def _drain(self):
        try:
            data = self._io.readAll()
            if data is not None:
                self._buf += bytes(data)
        except Exception:  # noqa: BLE001
            pass

    def stop(self) -> tuple[Path | None, str]:
        if not self._source:
            return None, "tidak sedang merekam"
        try:
            self._io.readyRead.disconnect(self._drain)
        except Exception:  # noqa: BLE001
            pass
        try:
            self._buf += bytes(self._io.readAll() or b"")
        except Exception:  # noqa: BLE001
            pass
        try:
            self._source.stop()
        except Exception:  # noqa: BLE001
            pass
        self._source = None
        if len(self._buf) < 3200:  # <0.1s of audio
            return None, "rekaman terlalu pendek"
        fmt = self._fmt
        path = Path(tempfile.mkstemp(prefix="clyve-mic-", suffix=".wav")[1])
        with wave.open(str(path), "wb") as w:
            w.setnchannels(fmt.channelCount())
            w.setsampwidth(2)
            w.setframerate(fmt.sampleRate())
            w.writeframes(bytes(self._buf))
        self._buf = bytearray()
        return path, ""


class TranscribeWorker(QThread):
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, path: str, cfg: dict, parent=None):
        super().__init__(parent)
        self.path, self.cfg = path, cfg

    def run(self):
        from . import stt
        try:
            self.done.emit(stt.transcribe(self.path, self.cfg))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
        finally:
            try:
                Path(self.path).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
