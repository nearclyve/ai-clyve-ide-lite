"""Palet + stylesheet tunggal untuk UI Qt.

Semua warna diambil dari sini supaya tema konsisten: satu accent ungu, elevasi
lewat perbedaan fill (bukan shadow), border 1px kontras rendah.
"""

from __future__ import annotations

BG = "#151020"        # kanvas
PANEL = "#1b1529"     # sidebar / panel
PANEL2 = "#221a33"    # hover, kontrol
INPUT = "#2a2140"     # field
LINE = "#2e2542"      # border
FG = "#efeaff"
MUTED = "#a99fc4"
DIM = "#6f6688"
ACCENT = "#8b5cf6"
ACCENT_DARK = "#7c3aed"
OK = "#34d399"
ERR = "#f87171"
WARN = "#fbbf24"
CODE_BG = "#120d1c"

QSS = f"""
QWidget {{ background:{BG}; color:{FG}; font-size:13px; }}
QFrame#side {{ background:{PANEL}; border-right:1px solid {LINE}; }}
QFrame#panel {{ background:{PANEL}; border-left:1px solid {LINE}; }}
QFrame#topbar {{ background:{BG}; border-bottom:1px solid {LINE}; }}
QLabel#brand {{ font-size:13px; font-weight:700; letter-spacing:0.2px; }}
QLabel.section {{ color:{DIM}; font-size:9.5px; font-weight:700; letter-spacing:1.1px; }}
QLabel#muted {{ color:{MUTED}; }}
QLabel#dim {{ color:{DIM}; }}
QPushButton {{ background:{PANEL2}; border:1px solid {LINE}; border-radius:7px;
    padding:6px 11px; min-height:18px; }}
QPushButton:hover {{ border-color:#3d3357; background:#281e3d; }}
QPushButton:pressed {{ background:#1f1830; }}
QPushButton#primary {{ background:{ACCENT_DARK}; border-color:{ACCENT_DARK}; color:white;
    font-weight:600; padding:7px 15px; }}
QPushButton#primary:hover {{ background:{ACCENT}; border-color:{ACCENT}; }}
QPushButton#danger {{ background:#3a1a24; border-color:#5c2536; color:#ffd9e2; }}
QPushButton#ghost {{ background:transparent; border:1px solid transparent; color:{MUTED};
    padding:4px 8px; }}
QPushButton#ghost:hover {{ background:{PANEL2}; color:{FG}; }}
QPushButton#ghost:checked {{ background:{PANEL2}; color:{FG}; }}
QPushButton:disabled {{ color:#5d5473; border-color:#241c36; background:{PANEL}; }}
QComboBox, QLineEdit {{ background:{PANEL2}; border:1px solid {LINE}; border-radius:7px;
    padding:5px 9px; min-height:20px; selection-background-color:{ACCENT_DARK}; }}
QComboBox:focus, QLineEdit:focus {{ border-color:{ACCENT}; }}
QComboBox::drop-down {{ border:none; width:16px; }}
QComboBox QAbstractItemView {{ background:{PANEL2}; border:1px solid {LINE};
    selection-background-color:{ACCENT_DARK}; outline:none; }}
QComboBox#mini {{ background:transparent; border:1px solid transparent; color:{MUTED};
    padding:2px 6px; min-height:16px; font-size:11px; }}
QComboBox#mini:hover {{ background:{PANEL2}; color:{FG}; }}
QPlainTextEdit#input {{ background:{INPUT}; border:1px solid {LINE}; border-radius:12px;
    padding:9px 11px; font-size:13px; }}
QPlainTextEdit#input:focus {{ border-color:{ACCENT}; }}
QPlainTextEdit, QTextBrowser {{ background:{BG}; border:none;
    selection-background-color:{ACCENT_DARK}; }}
QPlainTextEdit#termOut {{ background:{CODE_BG}; border:none; font-family:monospace;
    font-size:11px; color:#c9c0e4; padding:6px 10px; }}
QListWidget {{ background:transparent; border:none; outline:none; }}
QListWidget::item {{ padding:5px 8px; border-radius:6px; color:{MUTED}; }}
QListWidget::item:selected {{ background:{PANEL2}; color:{FG}; }}
QListWidget::item:hover {{ background:#241c36; }}
QScrollBar:vertical {{ background:transparent; width:9px; margin:2px; }}
QScrollBar::handle:vertical {{ background:#342a4d; border-radius:4px; min-height:26px; }}
QScrollBar::handle:vertical:hover {{ background:#43365f; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height:0; width:0; }}
QScrollBar:horizontal {{ background:transparent; height:9px; margin:2px; }}
QScrollBar::handle:horizontal {{ background:#342a4d; border-radius:4px; min-width:26px; }}
QCheckBox {{ color:{MUTED}; spacing:7px; }}
QCheckBox::indicator {{ width:14px; height:14px; border:1px solid #3d3357;
    border-radius:4px; background:{PANEL2}; }}
QCheckBox::indicator:checked {{ background:{ACCENT_DARK}; border-color:{ACCENT}; }}
QDialog {{ background:{BG}; }}
QSplitter::handle {{ background:{LINE}; }}
QSplitter::handle:horizontal {{ width:1px; }}
QSplitter::handle:vertical {{ height:1px; }}
QTabBar::tab {{ background:transparent; color:{DIM}; padding:5px 11px; margin-right:2px;
    border-radius:6px; }}
QTabBar::tab:selected {{ background:{PANEL2}; color:{FG}; }}
QTabWidget::pane {{ border:none; }}
QToolTip {{ background:{PANEL2}; color:{FG}; border:1px solid {LINE}; padding:4px 6px; }}
"""
