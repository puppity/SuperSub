from __future__ import annotations

from PySide6.QtWidgets import QApplication

COLORS = {
    "bg": "#1C1C1C", "rail": "#171717", "card": "#2A2A2A", "card2": "#262626",
    "field": "#1E1E1E", "stroke": "#353535", "stroke2": "#3A3A3A",
    "text": "#F3F3F3", "text2": "#C8C8C8", "text3": "#A8A8A8",
    "accent": "#60CDFF", "on_accent": "#0B1A22", "selected": "#1F3442",
    "good": "#8FE3A8", "warn": "#F5C46B", "bad": "#F28B82",
}

QSS = """
QWidget { background: %(bg)s; color: %(text)s; font-family: "Leelawadee UI", "Segoe UI", "Yu Gothic UI"; font-size: 10pt; }
QFrame#rail { background: %(rail)s; border-right: 1px solid #2E2E2E; }
QToolButton[nav="true"] { background: transparent; color: %(text2)s; border: 0; border-radius: 8px; padding: 8px 4px; font-size: 8pt; }
QToolButton[nav="true"]:checked { background: #2D2D2D; color: %(text)s; }
QToolButton[nav="true"]:hover { background: #262626; }
QFrame[card="true"] { background: %(card)s; border: 1px solid %(stroke)s; border-radius: 10px; }
QFrame[card="true"] QLabel, QFrame[card="true"] QCheckBox, QFrame[card="true"] QRadioButton { background: transparent; }
QLabel[role="h1"] { font-size: 20pt; font-weight: 600; }
QLabel[role="h2"] { font-size: 12pt; font-weight: 600; }
QLabel[role="muted"] { color: %(text3)s; }
QPushButton { background: #323232; border: 1px solid %(stroke2)s; border-radius: 6px; padding: 7px 14px; min-height: 22px; }
QPushButton:hover { background: #3A3A3A; }
QPushButton:disabled { color: #6F6F6F; }
QPushButton[accent="true"] { background: %(accent)s; color: %(on_accent)s; border: 0; font-weight: 600; }
QPushButton[accent="true"]:hover { background: #7AD6FF; }
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox { background: %(field)s; border: 1px solid %(stroke2)s; border-bottom: 2px solid #4A4A4A; border-radius: 6px; padding: 6px 8px; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus { border-bottom: 2px solid %(accent)s; }
QComboBox QAbstractItemView { background: %(card)s; selection-background-color: %(selected)s; }
QTableWidget { background: %(card)s; border: 1px solid %(stroke)s; border-radius: 10px; gridline-color: #323232; selection-background-color: %(selected)s; selection-color: %(text)s; }
QHeaderView::section { background: %(card)s; color: %(text3)s; border: 0; border-bottom: 1px solid %(stroke)s; padding: 6px; }
QListWidget { background: transparent; border: 0; }
QProgressBar { background: #3A3A3A; border: 0; border-radius: 2px; max-height: 4px; }
QProgressBar::chunk { background: %(accent)s; border-radius: 2px; }
QScrollBar:vertical { background: transparent; width: 10px; }
QScrollBar::handle:vertical { background: #4A4A4A; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
""" % COLORS


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)
