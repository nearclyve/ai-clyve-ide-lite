"""File tree workspace untuk panel kanan (di bawah kotak commit).

Default semua folder terlipat. Klik dua kali file -> buka dengan aplikasi
default OS. Model filesystem Qt memuat isi folder saat di-expand, jadi
workspace besar tidak di-scan semua di awal.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QDir, QModelIndex, QSortFilterProxyModel, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QFileSystemModel, QFrame, QHBoxLayout, QLabel, QPushButton, QTreeView,
    QVBoxLayout, QWidget,
)

from . import theme

HIDDEN = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__",
          "dist", "build", ".idea", ".vscode", ".next", ".nuxt", "target",
          ".pytest_cache", ".mypy_cache", ".cache"}


class _Filter(QSortFilterProxyModel):
    """Sembunyikan folder berat (node_modules, .git, dst) dari tree."""

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:  # noqa: N802
        idx = self.sourceModel().index(row, 0, parent)
        name = self.sourceModel().fileName(idx)
        if name in HIDDEN:
            return False
        return super().filterAcceptsRow(row, parent)


class FilesTree(QFrame):
    """Tree workspace yang mengingat folder mana yang terbuka.

    Sebelumnya `directoryLoaded` selalu memicu collapseAll, jadi setiap folder
    yang baru di-expand langsung menutup lagi. Sekarang state disimpan per
    workspace dan dipulihkan saat app dibuka atau workspace diganti.
    """

    stateChanged = Signal(list)      # daftar path relatif folder yang terbuka
    fileOpenRequested = Signal(str)  # klik dua kali file -> buka di editor app
    searchRequested = Signal()       # tombol cari / Ctrl+P

    def __init__(self, root: str = "", parent=None):
        super().__init__(parent)
        self.root = root or ""
        self._saved: dict[str, list[str]] = {}
        self._pending: set[str] = set()
        self._first_collapse = True
        self.setObjectName("filesTree")
        self.setStyleSheet(f"QFrame#filesTree{{background:transparent;border:none;}}")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 6)
        lay.setSpacing(3)

        head = QHBoxLayout()
        head.setSpacing(4)
        title = QLabel("Workspace")
        title.setStyleSheet(f"color:{theme.DIM};font-size:9.5px;font-weight:700;"
                            f"letter-spacing:1px;")
        head.addWidget(title)
        head.addStretch(1)
        self.search_btn = QPushButton("\u2315")
        self.search_btn.setObjectName("ghost")
        self.search_btn.setFixedWidth(22)
        self.search_btn.setToolTip("Cari file di workspace (Ctrl+P)")
        self.search_btn.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;color:{theme.DIM};font-size:12px;}}"
            f"QPushButton:hover{{color:{theme.FG};}}")
        self.search_btn.clicked.connect(lambda: self.searchRequested.emit())
        head.addWidget(self.search_btn)
        self.collapse_btn = QPushButton("\u25b4")
        self.collapse_btn.setObjectName("ghost")
        self.collapse_btn.setFixedWidth(22)
        self.collapse_btn.setToolTip("Lipat semua folder")
        self.collapse_btn.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;color:{theme.DIM};font-size:10px;}}"
            f"QPushButton:hover{{color:{theme.FG};}}")
        self.collapse_btn.clicked.connect(lambda: self.view.collapseAll())
        head.addWidget(self.collapse_btn)
        lay.addLayout(head)

        self.model = QFileSystemModel(self)
        self.model.setFilter(QDir.AllEntries | QDir.NoDotAndDotDot | QDir.AllDirs)
        self.proxy = _Filter(self)
        self.proxy.setSourceModel(self.model)

        self.view = QTreeView(self)
        self.view.setModel(self.proxy)
        self.view.setHeaderHidden(True)
        self.view.setRootIsDecorated(True)
        self.view.setIndentation(12)
        self.view.setUniformRowHeights(True)
        self.view.setFrameShape(QFrame.NoFrame)
        self.view.setMaximumHeight(232)
        self.view.setMinimumHeight(120)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setStyleSheet(
            f"QTreeView{{background:{theme.PANEL};border:none;color:{theme.MUTED};"
            f"font-size:11px;}}"
            f"QTreeView::item{{padding:2px 0;border:none;}}"
            f"QTreeView::item:hover{{background:{theme.PANEL2};}}"
            f"QTreeView::item:selected{{background:{theme.PANEL2};color:{theme.FG};}}")
        self.view.doubleClicked.connect(self._open)
        for col in (1, 2, 3):
            self.view.setColumnHidden(col, True)
        lay.addWidget(self.view)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._emit_state)
        self.view.expanded.connect(lambda _i: self._save_timer.start())
        self.view.collapsed.connect(lambda _i: self._save_timer.start())

        if self.root:
            self.set_root(self.root)

    def set_root(self, root: str) -> None:
        if not root:
            return
        self.root = root
        self.model.setRootPath(root)
        idx = self.proxy.mapFromSource(self.model.index(root))
        self.view.setRootIndex(idx)
        self._first_collapse = True
        self._pending = set(self._saved.get(root, []))
        QTimer.singleShot(0, self._restore_or_collapse)

    # ------------------------------------------------------------- state
    def set_state(self, paths: list[str]) -> None:
        """Terapkan state tersimpan untuk root aktif (dipanggil dari config)."""
        if not self.root:
            return
        self._saved[self.root] = [str(p) for p in (paths or [])][:120]
        self._pending = set(self._saved[self.root])
        self._first_collapse = False
        QTimer.singleShot(0, self._restore_or_collapse)

    def _restore_or_collapse(self) -> None:
        if self._pending:
            self._restore_next()
            return
        if self._first_collapse:
            self.view.collapseAll()
            self._first_collapse = False

    def _restore_next(self) -> None:
        """Buka folder yang tersimpan; index anak baru valid setelah model load."""
        if not self._pending or not self.root:
            return
        root_path = Path(self.root)
        for rel in list(self._pending):
            src = self.model.index(str(root_path / rel))
            if not src.isValid():
                continue
            idx = self.proxy.mapFromSource(src)
            if not idx.isValid():
                continue
            self.view.expand(idx)
            self._pending.discard(rel)
        if self._pending:
            QTimer.singleShot(150, self._restore_next)

    def _emit_state(self) -> None:
        if not self.root:
            return
        expanded: list[str] = []
        root_path = Path(self.root)

        def walk(parent: QModelIndex) -> None:
            for row in range(self.proxy.rowCount(parent)):
                idx = self.proxy.index(row, 0, parent)
                src = self.proxy.mapToSource(idx)
                if not self.model.isDir(src):
                    continue
                if not self.view.isExpanded(idx):
                    continue
                import os
                src_path = Path(self.model.filePath(src))
                rel = ""
                # realpath me-resolve nama pendek 8.3 (RUNNER~1) dan symlink di
                # Windows, sehingga perbandingan dengan root tidak meleset.
                try:
                    real_root = Path(os.path.realpath(str(root_path)))
                    real_src = Path(os.path.realpath(str(src_path)))
                    rel = str(real_src.relative_to(real_root))
                except ValueError:
                    rel = ""
                if not rel or rel.startswith(".."):
                    continue
                expanded.append(rel)
                walk(idx)

        walk(self.view.rootIndex())
        self._saved[self.root] = expanded[:120]
        self.stateChanged.emit(list(self._saved[self.root]))

    def _open(self, index: QModelIndex) -> None:
        src = self.proxy.mapToSource(index)
        path = self.model.filePath(src)
        if path and Path(path).is_file():
            self.fileOpenRequested.emit(path)
