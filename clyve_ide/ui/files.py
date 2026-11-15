"""File tree workspace untuk panel kanan (di bawah kotak commit).

Default semua folder terlipat. Klik dua kali file -> buka dengan aplikasi
default OS. Model filesystem Qt memuat isi folder saat di-expand, jadi
workspace besar tidak di-scan semua di awal.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QDir, QModelIndex, QSortFilterProxyModel, QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileSystemModel, QFrame, QHBoxLayout, QLabel, QPushButton, QTreeView,
    QVBoxLayout, QWidget,
)

from . import theme

HIDDEN = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
          ".idea", ".vscode", ".next", ".nuxt", "target"}


class _Filter(QSortFilterProxyModel):
    """Sembunyikan folder berat (node_modules, .git, dst) dari tree."""

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:  # noqa: N802
        idx = self.sourceModel().index(row, 0, parent)
        name = self.sourceModel().fileName(idx)
        if name in HIDDEN:
            return False
        return super().filterAcceptsRow(row, parent)


class FilesTree(QFrame):
    def __init__(self, root: str = "", parent=None):
        super().__init__(parent)
        self.root = root or ""
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

        # model memuat isi folder secara async: lipat ulang setelah data masuk,
        # kalau tidak folder pertama kadang masih terbuka.
        self.model.directoryLoaded.connect(lambda _p: self.view.collapseAll())

        if self.root:
            self.set_root(self.root)

    def set_root(self, root: str) -> None:
        if not root:
            return
        self.root = root
        self.model.setRootPath(root)
        idx = self.proxy.mapFromSource(self.model.index(root))
        self.view.setRootIndex(idx)
        self.view.collapseAll()  # default: semua folder terlipat
        QTimer.singleShot(0, self.view.collapseAll)

    def _open(self, index: QModelIndex) -> None:
        src = self.proxy.mapToSource(index)
        path = self.model.filePath(src)
        if path and Path(path).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
