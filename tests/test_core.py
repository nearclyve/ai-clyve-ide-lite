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

    def test_default_manifest_url(self):
        self.assertIn("github.com", updater.DEFAULT_MANIFEST)
        self.assertTrue(updater.DEFAULT_MANIFEST.endswith("/latest.json"))
        self.assertNotIn("api.github.com", updater.DEFAULT_MANIFEST)

    def test_rate_limit_message_is_specific(self):
        old = (updater.manifest_url, updater._get_json, updater._http_status)
        try:
            updater.manifest_url = lambda: updater.DEFAULT_MANIFEST
            updater._get_json = lambda url, timeout=12: None
            updater._http_status = lambda url, timeout=12: (
                "HTTP 403: API rate limit exceeded (kuota API GitHub habis, reset 10:00)")
            info, err = updater.check_detailed()
            self.assertIsNone(info)
            self.assertIn("HTTP 403", err)
            self.assertIn("rate limit", err)
        finally:
            updater.manifest_url, updater._get_json, updater._http_status = old

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

    def test_paste_image_works_with_root_workspace(self):
        from PySide6.QtGui import QColor, QImage
        w = self.w
        old_ws = w.session.workspace
        w.session.workspace = "/"        # dulu: tulis temp ke root -> gagal
        w.attachments = []
        img = QImage(10, 10, QImage.Format_RGB32)
        img.fill(QColor("#ff0000"))
        w._paste_image(img)
        self.assertEqual(len(w.attachments), 1)
        self.assertTrue(Path(w.attachments[0]["path"]).is_file())
        self.assertIn("gambar ditempel", w.status.text())
        w.session.workspace = old_ws
        w.attachments = []
        w._render_atts()

    def test_paste_null_image_shows_hint(self):
        w = self.w
        w.attachments = []
        w._paste_image(None)
        self.assertIn("tidak berisi gambar", w.status.text())

    def test_attach_paths_from_file(self):
        import tempfile
        w = self.w
        w.attachments = []
        p = Path(tempfile.mkdtemp(prefix="clyve-paste-")) / "foto.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        w._attach_paths([str(p)])
        self.assertEqual(len(w.attachments), 1)
        w.attachments = []
        w._render_atts()

    def test_short_bubble_follows_text(self):
        from PySide6.QtCore import Qt
        w = self.w
        b = w.chat.add_bubble("user")
        b.set_stream_text("hallo")
        self.app.processEvents()
        self.assertLess(b.width(), 320)
        b.set_stream_text("jawaban " * 200)
        self.app.processEvents()
        self.assertGreater(b.width(), b.minimumWidth())

    def test_streaming_uses_plain_text(self):
        from PySide6.QtCore import Qt
        w = self.w
        w.chat.clear()
        w._welcome = False
        w._stream_started = False
        w._stream_bubble = None
        w._buf = []
        w._reason_buf = []
        w._had_delta = False
        w._first_delta_at = 0.0
        w._t0 = time.time()
        w._on_event({"type": "delta", "text": "halo **dunia**"})
        w._flush_stream()
        b = w._stream_bubble
        self.assertEqual(b.body.textFormat(), Qt.PlainText)
        self.assertNotIn("<div", b.body.text())
        w._finalize_stream("")
        self.assertEqual(b.body.textFormat(), Qt.RichText)

    def test_context_trim_caps_chars(self):
        import tempfile
        from clyve_ide.agent import AgentSession
        s = AgentSession("k", "http://127.0.0.1:9/v1", "m",
                         tempfile.mkdtemp(prefix="clyve-ctx-"))
        s.messages.append({"role": "user", "content": "x" * 50_000})
        s.messages.append({"role": "tool", "content": "y" * 50_000})
        self.assertGreater(s._context_chars(), 80_000)
        s._trim()
        self.assertLessEqual(s._context_chars(), 80_000)

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


@unittest.skipUnless(_qt_available(), "PySide6 tidak terpasang")
class QuickOpenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_fuzzy_score(self):
        from clyve_ide.ui.quickopen import fuzzy_score
        self.assertGreater(fuzzy_score("gitu", "clyve_ide/gitutil.py"),
                           fuzzy_score("gitu", "web/app.js"))
        self.assertGreater(fuzzy_score("ctrl", "app/Http/Controllers/Emotla/Controller.php"), 0)
        self.assertEqual(fuzzy_score("zzz", "clyve_ide/gitutil.py"), 0)
        self.assertGreater(fuzzy_score("", "apa/saja.py"), 0)

    def test_scan_skips_heavy_dirs(self):
        import tempfile
        from clyve_ide.ui.quickopen import ScanWorker
        root = Path(tempfile.mkdtemp(prefix="clyve-scan-"))
        (root / "app" / "Http").mkdir(parents=True)
        (root / "app" / "Http" / "Controller.php").write_text("x")
        (root / "node_modules" / "lib").mkdir(parents=True)
        (root / "node_modules" / "lib" / "index.js").write_text("x")
        (root / ".git").mkdir()
        (root / ".git" / "config").write_text("x")
        (root / ".github" / "workflows").mkdir(parents=True)
        (root / ".github" / "workflows" / "ci.yml").write_text("x")
        w = ScanWorker(str(root))
        got = []
        w.done.connect(got.extend)
        w.run()
        self.app.processEvents()
        self.assertIn("app/Http/Controller.php", got)
        self.assertFalse(any("node_modules" in g for g in got))
        self.assertFalse(any(g.startswith(".git/") for g in got))
        self.assertIn(".github/workflows/ci.yml", got)

    def test_dialog_filters_and_selects(self):
        import tempfile
        from clyve_ide.ui.quickopen import QuickOpenDialog
        root = tempfile.mkdtemp(prefix="clyve-qo-")
        dlg = QuickOpenDialog(root, files=["app/Http/Controller.php", "web/app.js", "README.md"])
        dlg.input.setText("controller")
        self.app.processEvents()
        self.assertGreater(dlg.list.count(), 0)
        self.assertIn("Controller.php", dlg.list.item(0).text())
        dlg._accept_current()
        self.assertTrue(dlg.selected_path.endswith("Controller.php"))


@unittest.skipUnless(_qt_available(), "PySide6 tidak terpasang")
class EditorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QTabWidget, QWidget
        from clyve_ide.ui.editor import EditorTabs
        cls.app = QApplication.instance() or QApplication([])
        cls.dir = Path(tempfile.mkdtemp(prefix="clyve-edit-"))
        cls.tabs = QTabWidget()
        cls.tabs.addTab(QWidget(), "Chat")
        cls.ed = EditorTabs(cls.tabs)
        cls.messages = []
        cls.ed.status.connect(cls.messages.append)
        cls.tabs.show()

    def _make(self, name, text):
        p = self.dir / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_open_edit_save(self):
        p = self._make("contoh.py", "print('halo')\n")
        ed = self.ed.open_path(str(p))
        self.assertIsNotNone(ed)
        self.assertIn("print", ed.toPlainText())
        self.assertIsNotNone(ed.highlighter)
        self.assertGreater(ed.line_number_width(), 10)
        ed.selectAll()
        ed.insertPlainText("print('berubah')\n")   # jalur edit nyata
        self.app.processEvents()
        self.assertTrue(ed.document().isModified())
        idx = self.tabs.indexOf(ed)
        self.assertTrue(self.tabs.tabText(idx).startswith("\u25cf"))
        self.assertTrue(self.ed.save(ed))
        self.assertEqual(p.read_text(encoding="utf-8"), "print('berubah')\n")
        self.assertFalse(ed.document().isModified())

    def test_reject_binary(self):
        p = self.dir / "gambar.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00")
        self.assertIsNone(self.ed.open_path(str(p)))

    def test_reload_from_disk(self):
        p = self._make("data.json", '{"a": 1}')
        ed = self.ed.open_path(str(p))
        p.write_text('{"a": 2}', encoding="utf-8")
        self.assertTrue(self.ed.reload_path(str(p)))
        self.assertIn('"a": 2', ed.toPlainText())
        # kalau ada edit lokal, jangan ditimpa
        ed.selectAll()
        ed.insertPlainText('{"lokal": true}')
        p.write_text('{"a": 3}', encoding="utf-8")
        self.assertFalse(self.ed.reload_path(str(p)))
        self.assertIn("lokal", ed.toPlainText())
        self.ed.save(ed)

    def test_open_same_file_twice_reuses_tab(self):
        p = self._make("ulang.txt", "x")
        before = self.tabs.count()
        self.ed.open_path(str(p))
        self.ed.open_path(str(p))
        self.assertEqual(self.tabs.count(), before + 1)


@unittest.skipUnless(_qt_available(), "PySide6 tidak terpasang")
class FilesTreeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.root = tempfile.mkdtemp(prefix="clyve-tree-")
        (Path(cls.root) / "alpha" / "sub").mkdir(parents=True)
        (Path(cls.root) / "alpha" / "file.txt").write_text("x")
        (Path(cls.root) / "beta").mkdir()

    def _wait(self, ms=800):
        t0 = time.time()
        while (time.time() - t0) * 1000 < ms:
            self.app.processEvents()
            time.sleep(0.02)

    def _wait_until(self, pred, ms=6000):
        t0 = time.time()
        while (time.time() - t0) * 1000 < ms:
            self.app.processEvents()
            if pred():
                return True
            time.sleep(0.05)
        return bool(pred())

    def _index_of(self, tree, name):
        for row in range(tree.proxy.rowCount(tree.view.rootIndex())):
            idx = tree.proxy.index(row, 0, tree.view.rootIndex())
            if tree.proxy.data(idx) == name:
                return idx
        return None

    def test_expand_persists_across_reopen(self):
        from clyve_ide.ui.files import FilesTree
        tree = FilesTree(self.root)
        tree.resize(300, 240)
        tree.show()
        self.assertTrue(
            self._wait_until(lambda: self._index_of(tree, "alpha") is not None),
            f"model tidak memuat folder alpha di {self.root}")
        idx = self._index_of(tree, "alpha")
        tree.view.expand(idx)
        # jangan bergantung timer debounce: picu penyimpanan langsung
        tree._emit_state()
        self.assertIn("alpha", tree._saved.get(self.root, []))
        tree.close()

        tree2 = FilesTree(self.root)
        tree2.resize(300, 240)
        tree2.show()
        tree2.set_state(["alpha"])
        def restored() -> bool:
            i = self._index_of(tree2, "alpha")
            return i is not None and tree2.view.isExpanded(i)

        self.assertTrue(self._wait_until(restored), "state folder terbuka tidak dipulihkan")
        tree2.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
