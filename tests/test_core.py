"""Test inti tanpa jaringan: version, updater (parsing), gitutil, dan antrian chat.

Jalankan:
    python -m unittest discover -s tests -v

Bagian Qt otomatis dilewati kalau PySide6 tidak terpasang.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clyve_ide import gitutil, updater, version  # noqa: E402


class VersionTest(unittest.TestCase):
    def test_tuple(self):
        self.assertEqual(version.tuple_of("v1.2.3"), (1, 2, 3))
        self.assertEqual(version.tuple_of("1.2.3-beta"), (1, 2, 3))
        self.assertEqual(version.tuple_of("garbage"), (0,))

    def test_newer(self):
        self.assertTrue(version.is_newer("v1.2.0", "1.1.0"))
        self.assertTrue(version.is_newer("1.10.0", "1.9.9"))
        self.assertFalse(version.is_newer("v1.1.0", "1.1.0"))
        self.assertFalse(version.is_newer("v1.0.9", "1.1.0"))

    def test_version_format(self):
        parts = version.__version__.split(".")
        self.assertGreaterEqual(len(parts), 2)
        self.assertTrue(all(p.isdigit() for p in parts))


class AttachmentsTest(unittest.TestCase):
    def test_upload_stored_outside_workspace(self):
        import os
        import tempfile
        from clyve_ide import attachments
        ws = Path(tempfile.mkdtemp(prefix="clyve-ws-"))
        old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="clyve-cfg-")
        try:
            info = attachments.save_upload(ws, "catatan.txt", b"halo")
            p = Path(info["path"])
            self.assertTrue(p.is_file())
            self.assertNotIn(str(ws), str(p))  # tidak mengotori workspace/git
        finally:
            if old is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = old


class WorkspaceTest(unittest.TestCase):
    def test_root_detection(self):
        import tempfile
        from clyve_ide import config
        self.assertTrue(config.is_root_path("/"))
        self.assertFalse(config.is_root_path(tempfile.mkdtemp(prefix="clyve-ws-")))
        self.assertFalse(config.is_root_path("/home"))

    def test_default_workspace_is_not_root(self):
        from clyve_ide import config
        cfg = config.load()
        self.assertFalse(config.is_root_path(cfg["workspace"]))


class ProcTest(unittest.TestCase):
    def test_no_window_kwargs_linux(self):
        from clyve_ide import proc
        old = proc.IS_WINDOWS
        try:
            proc.IS_WINDOWS = False
            self.assertEqual(proc.no_window_kwargs(), {})
            proc.IS_WINDOWS = True
            kw = proc.no_window_kwargs()
            self.assertEqual(kw["creationflags"], 0x08000000)
        finally:
            proc.IS_WINDOWS = old


class UpdaterTest(unittest.TestCase):
    def test_pick_setup_asset(self):
        rel = {"assets": [
            {"name": "app.zip", "browser_download_url": "z"},
            {"name": "AIClyveIDELite-Setup-1.2.0.exe", "browser_download_url": "setup"},
        ]}
        self.assertEqual(updater.pick_asset(rel)["browser_download_url"], "setup")

    def test_pick_fallback_exe(self):
        rel = {"assets": [{"name": "portable.exe", "browser_download_url": "p"}]}
        self.assertEqual(updater.pick_asset(rel)["browser_download_url"], "p")

    def test_pick_none(self):
        self.assertIsNone(updater.pick_asset({"assets": [{"name": "a.zip"}]}))
        self.assertIsNone(updater.pick_asset({}))

    def test_manifest_flow(self):
        old_url, old_get, old_status = (updater.manifest_url, updater._get_json,
                                        updater._http_status)
        try:
            updater.manifest_url = lambda: "http://x/latest.json"
            updater._get_json = lambda url, timeout=12: {
                "version": "9.9.9", "url": "http://x/Setup.exe", "notes": "n"}
            info, err = updater.check_detailed()
            self.assertEqual(err, "")
            self.assertEqual(info["version"], "9.9.9")
            self.assertEqual(info["source"], "manifest")

            updater._get_json = lambda url, timeout=12: None
            info, err = updater.check_detailed()
            self.assertIsNone(info)
            self.assertIn("manifest", err)
        finally:
            updater.manifest_url, updater._get_json, updater._http_status = (
                old_url, old_get, old_status)

    def test_private_repo_404_reports_error(self):
        old_url, old_get, old_status = (updater.manifest_url, updater._get_json,
                                        updater._http_status)
        try:
            updater.manifest_url = lambda: ""
            updater._get_json = lambda url, timeout=12: None
            updater._http_status = lambda url, timeout=12: "HTTP 404"
            info, err = updater.check_detailed()
            self.assertIsNone(info)
            self.assertIn("HTTP 404", err)
            self.assertIn("private", err)
        finally:
            updater.manifest_url, updater._get_json, updater._http_status = (
                old_url, old_get, old_status)

    def test_check_bad_network_is_none(self):
        # endpoint tidak valid -> None, bukan exception
        old = updater.API_LATEST
        try:
            updater.API_LATEST = "http://127.0.0.1:9/none"
            self.assertIsNone(updater.check(timeout=2))
        finally:
            updater.API_LATEST = old


@unittest.skipUnless(subprocess.run(["git", "--version"], capture_output=True).returncode == 0,
                     "git tidak terpasang")
class GitUtilTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="clyve-git-"))
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test")
        (self.dir / "a.txt").write_text("satu\ndua\n")
        self._git("add", "-A")
        self._git("commit", "-qm", "init")

    def _git(self, *args):
        return subprocess.run(["git", *args], cwd=str(self.dir), capture_output=True, text=True)

    def test_not_repo(self):
        other = Path(tempfile.mkdtemp(prefix="clyve-nogit-"))
        self.assertFalse(gitutil.is_repo(other))
        self.assertEqual(gitutil.status(other), [])
        self.assertEqual(gitutil.diff(other), "")

    def test_status_diff_commit(self):
        (self.dir / "a.txt").write_text("satu\nDUA\n")
        (self.dir / "b.txt").write_text("baru\n")
        rows = {r["path"]: r for r in gitutil.status(self.dir)}
        self.assertEqual(rows["a.txt"]["code"], "M")
        self.assertTrue(rows["b.txt"]["untracked"])
        d = gitutil.diff(self.dir, "a.txt")
        self.assertIn("-dua", d)
        self.assertIn("+DUA", d)
        self.assertIn("+baru", gitutil.diff(self.dir, "b.txt"))
        stats = {r["path"]: (r["added"], r["removed"]) for r in gitutil.files_with_diff(self.dir)}
        self.assertEqual(stats["a.txt"], (1, 1))
        self.assertEqual(stats["b.txt"], (1, 0))
        ok, msg = gitutil.commit(self.dir, "test: commit")
        self.assertTrue(ok, msg)
        self.assertEqual(gitutil.status(self.dir), [])
        self.assertIn("test: commit", self._git("log", "-1", "--pretty=%s").stdout)

    def test_binary_file_not_rendered_as_diff(self):
        (self.dir / "gambar.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
        self.assertTrue(gitutil.looks_binary(self.dir, "gambar.png"))
        self.assertEqual(gitutil.diff(self.dir, "gambar.png"), "")
        rows = {r["path"]: r for r in gitutil.files_with_diff(self.dir)}
        self.assertTrue(rows["gambar.png"]["binary"])
        self.assertFalse(gitutil.looks_binary(self.dir, "a.txt"))

    def test_commit_rejects_empty_message(self):
        ok, msg = gitutil.commit(self.dir, "   ")
        self.assertFalse(ok)
        self.assertIn("kosong", msg)

    def test_commit_nothing_to_commit(self):
        ok, msg = gitutil.commit(self.dir, "kosong")
        self.assertFalse(ok)
        self.assertIn("tidak ada perubahan", msg)


def _qt_available() -> bool:
    try:
        import PySide6  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


@unittest.skipUnless(_qt_available(), "PySide6 tidak terpasang")
class QueueTest(unittest.TestCase):
    """Pesan saat agent sibuk harus masuk antrian dan terkirim otomatis setelah selesai."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="clyve-qcfg-")
        from PySide6.QtWidgets import QApplication
        from clyve_ide import config, qt_app
        cls.app = QApplication.instance() or QApplication([])
        cf = config.load()
        cf.update({"api_key": "sk-test-0000", "workspace": tempfile.mkdtemp(prefix="clyve-qws-"),
                   "model": "deepseek-v4.1-flash"})
        # jangan tembak gateway saat test: model list dipalsukan
        config.list_models = lambda *a, **k: ["deepseek-v4.1-flash"]
        cls.w = qt_app.MainWindow(cf)

    def test_queue_when_busy_then_auto_dispatch(self):
        w = self.w
        sent = []
        w._dispatch = lambda text, atts: sent.append((text, atts))  # jangan panggil jaringan
        w.queue.clear()
        w._render_queue()
        w.busy = True
        for i in range(2):
            w.input.setPlainText(f"pesan {i}")
            w._send()
        self.assertEqual(len(w.queue), 2)
        self.assertEqual(w.input.toPlainText(), "")
        self.assertTrue(w.send_btn.isEnabled())

        w.busy = False
        w._on_finished()
        t0 = time.time()
        while time.time() - t0 < 0.6 and not sent:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertEqual(sent[0][0], "pesan 0")
        self.assertEqual(len(w.queue), 1)
        self.assertEqual(len(self._queue_chips()), 1)

    def _queue_chips(self):
        row = self.w.queue_row
        return [row.itemAt(i).widget() for i in range(row.count() - 1)
                if row.itemAt(i).widget() is not None]

    def test_dequeue_drops_one(self):
        w = self.w
        w.queue = [{"text": "a", "atts": []}, {"text": "b", "atts": []}]
        w._render_queue()
        w._dequeue(0)
        self.assertEqual([q["text"] for q in w.queue], ["b"])

    def test_send_button_has_text(self):
        self.assertEqual(self.w.send_btn.text(), "\u2191")

    def test_input_autogrow(self):
        w = self.w
        w.input.setFixedHeight(w.input.MIN_H)
        w.input.setPlainText("\n".join(f"baris {i}" for i in range(8)))
        self.app.processEvents()
        self.assertGreater(w.input.height(), w.input.MIN_H)
        self.assertLessEqual(w.input.height(), w.input.MAX_H)
        w.input.clear()
        self.app.processEvents()
        self.assertEqual(w.input.height(), w.input.MIN_H)

    def test_busy_timer_mentions_elapsed(self):
        w = self.w
        w._t0 = time.time() - 2.0
        w._first_delta_at = 0.0
        w._tick_busy()
        self.assertIn("berpikir", w.status.text())
        w._first_delta_at = time.time() - 0.5
        w._tick_busy()
        self.assertIn("membalas", w.status.text())

    def test_empty_done_shows_hint(self):
        w = self.w
        w._t0 = time.time()
        w._had_delta = False
        before = w.chat.lay.count()
        w._on_event({"type": "done", "content": ""})
        self.assertGreater(w.chat.lay.count(), before)
        self.assertIn("selesai", w.status.text())

    def test_reasoning_shown_then_warns_when_no_answer(self):
        w = self.w
        w.chat.clear()
        w._welcome = False
        w._stream_started = False
        w._stream_bubble = None
        w._reason_buf = []
        w._buf = []
        w._t0 = time.time()
        w._first_delta_at = 0.0
        w._had_delta = False
        w._on_event({"type": "reasoning", "text": "pertimbangkan dulu "})
        w._on_event({"type": "reasoning", "text": "lalu simpulkan"})
        w._tick_busy()
        self.assertIn("menalar", w.status.text())
        w._on_event({"type": "done", "content": ""})
        all_text = " ".join(self._bubble_texts())
        self.assertIn("tanpa jawaban akhir", all_text)

    def _bubble_texts(self):
        from PySide6.QtWidgets import QLabel
        out = []
        for lab in self.w.chat.inner.findChildren(QLabel):
            t = lab.text() or ""
            if t.strip():
                out.append(t)
        return out

    def test_worker_batches_deltas(self):
        """Delta dari agent digabung sebelum dikirim ke UI (bukan 1 signal/token)."""
        from clyve_ide.qt_app import AgentWorker

        class FakeSession:
            def run(self, text, atts):
                for ch in "abcdefghij" * 4:
                    yield 'data: {"type": "delta", "text": "%s"}' % ch
                yield 'data: {"type": "done", "content": "ok"}'

        worker = AgentWorker(FakeSession(), "x", [])
        events = []
        worker.event.connect(events.append)
        worker.run()
        self.app.processEvents()
        deltas = [e["text"] for e in events if e.get("type") == "delta"]
        self.assertEqual("".join(deltas), "abcdefghij" * 4)
        self.assertLess(len(deltas), 40)
        self.assertEqual(events[-1]["type"], "done")


if __name__ == "__main__":
    unittest.main(verbosity=2)
