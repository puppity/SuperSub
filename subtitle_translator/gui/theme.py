from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

COLORS = {
    "bg": "#1C1C1C", "rail": "#171717", "card": "#262626", "card2": "#2C2C2C",
    "field": "#1E1E1E", "stroke": "#333333", "stroke2": "#3C3C3C",
    "text": "#F3F3F3", "text2": "#C8C8C8", "text3": "#9A9A9A",
    "accent": "#60CDFF", "on_accent": "#0B1A22", "selected": "#1F3442",
    "good": "#8FE3A8", "warn": "#F5C46B", "bad": "#F28B82",
}

# Thai needs a larger size than Latin to stay legible: tone marks and upper vowels
# sit above the cap height, so 10pt Leelawadee renders cramped and clipped.
UI_FONT = "Leelawadee UI"
BASE_PT = 12

QSS = """
QWidget { background: %(bg)s; color: %(text)s; font-family: "Leelawadee UI", "Segoe UI"; font-size: 12pt; }
QToolTip { background: %(card2)s; color: %(text)s; border: 1px solid %(stroke2)s; padding: 6px; }
QFrame#rail { background: %(rail)s; border-right: 1px solid #2A2A2A; }
QToolButton[nav="true"] { background: transparent; color: %(text3)s; border: 0; border-radius: 10px; padding: 6px 2px; font-size: 10pt; }
QToolButton[nav="true"]:checked { background: #2D2D2D; color: %(text)s; }
QToolButton[nav="true"]:hover:!checked { background: #242424; color: %(text2)s; }
QToolButton[nav="true"]:pressed { background: #202020; padding-top: 8px; padding-bottom: 0; }
QFrame[card="true"] { background: %(card)s; border: 1px solid %(stroke)s; border-radius: 12px; }
QFrame[card="true"] QLabel, QFrame[card="true"] QCheckBox, QFrame[card="true"] QRadioButton { background: transparent; }
QLabel[role="h1"] { font-size: 22pt; font-weight: 600; }
QLabel[role="h2"] { font-size: 13pt; font-weight: 600; }
QLabel[role="stat"] { font-size: 15pt; font-weight: 600; color: %(text)s; padding: 4px 0 2px 0; }
QLabel[role="muted"] { color: %(text3)s; }
QLabel[role="field"] { color: %(text2)s; font-size: 10.5pt; padding-top: 6px; }
QLabel[role="hint"] { color: %(text2)s; background: #232B30; border: 1px solid #2F3D46; border-radius: 8px; padding: 10px; }
QLabel[role="hint"][tone="bad"] { color: %(bad)s; background: #33201F; border-color: #4A2B29; }
QLabel[role="empty"] { color: %(text3)s; font-size: 12pt; padding: 48px 0; }
QLabel[role="pill"] { border-radius: 10px; padding: 4px 12px; font-size: 10pt; background: #333333; color: %(text2)s; }
QLabel[role="pill"][tone="bad"] { background: #3A2422; color: %(bad)s; }
QLabel[role="pill"][tone="good"] { background: #1F3326; color: %(good)s; }
QLabel[role="pill"][tone="busy"] { background: %(selected)s; color: %(accent)s; }
QLabel[role="pill"][tone="warn"] { background: #3A3020; color: %(warn)s; }
QPushButton { background: #303030; border: 1px solid %(stroke2)s; border-radius: 8px; padding: 8px 16px; min-height: 24px; }
QPushButton:hover { background: #3A3A3A; border-color: #4A4A4A; }
QPushButton:pressed { background: #2A2A2A; padding-top: 9px; padding-bottom: 7px; }
QPushButton:focus { border: 1px solid %(accent)s; }
QPushButton:disabled { color: #6A6A6A; background: #262626; border-color: #2E2E2E; }
QPushButton:checked { background: %(selected)s; color: %(accent)s; border-color: #2F5266; }
QPushButton[accent="true"] { background: %(accent)s; color: %(on_accent)s; border: 0; font-weight: 600; }
QPushButton[accent="true"]:hover { background: #7AD6FF; }
QPushButton[accent="true"]:pressed { background: #4FB8E8; padding-top: 9px; padding-bottom: 7px; }
QPushButton[accent="true"]:focus { border: 2px solid %(text)s; }
QPushButton[accent="true"]:disabled { background: #2C4350; color: #7F98A6; }
QPushButton[flat="true"] { background: transparent; border: 0; color: %(text3)s; padding: 6px 10px; }
QPushButton[flat="true"]:hover { background: #333333; color: %(text)s; }
QPushButton[icon="true"] { padding: 8px 10px; }
QSlider::groove:horizontal { height: 4px; background: #3A3A3A; border-radius: 2px; }
QSlider::sub-page:horizontal { background: %(accent)s; border-radius: 2px; }
QSlider::handle:horizontal { background: %(text)s; width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }
QSlider::handle:horizontal:hover { background: #FFFFFF; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }
QSlider::sub-page:horizontal:disabled { background: #3A3A3A; }
QSlider::handle:horizontal:disabled { background: #555555; }
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox { background: %(field)s; border: 1px solid %(stroke2)s; border-bottom: 2px solid #4A4A4A; border-radius: 8px; padding: 7px 10px; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus { border-bottom: 2px solid %(accent)s; }
QComboBox::drop-down { border: 0; width: 26px; }
QComboBox QAbstractItemView { background: %(card2)s; border: 1px solid %(stroke2)s; selection-background-color: %(selected)s; padding: 4px; outline: 0; }
QTableWidget { background: %(card)s; border: 1px solid %(stroke)s; border-radius: 12px; gridline-color: transparent; selection-background-color: %(selected)s; selection-color: %(text)s; alternate-background-color: #292929; }
QTableWidget::item { padding: 4px 8px; border-bottom: 1px solid #2E2E2E; }
QTableWidget::item:hover:!selected { background: #2E2E2E; }
QHeaderView::section { background: %(card)s; color: %(text3)s; border: 0; border-bottom: 1px solid %(stroke)s; padding: 8px; font-size: 10.5pt; }
QRadioButton, QCheckBox { spacing: 10px; padding: 4px 0; }
QRadioButton::indicator, QCheckBox::indicator { width: 18px; height: 18px; border: 2px solid #6A6A6A; background: %(field)s; }
QRadioButton::indicator { border-radius: 11px; }
QCheckBox::indicator { border-radius: 5px; }
QRadioButton::indicator:hover, QCheckBox::indicator:hover { border-color: %(text2)s; }
QRadioButton::indicator:checked { border-color: %(accent)s; background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 %(accent)s, stop:0.5 %(accent)s, stop:0.56 %(field)s, stop:1 %(field)s); }
QCheckBox::indicator:checked { border-color: %(accent)s; background: %(accent)s; }
QSpinBox::up-button, QSpinBox::down-button { width: 0; border: 0; }
QListWidget { background: transparent; border: 0; outline: 0; }
QListWidget::item { padding: 6px 4px; border-radius: 6px; }
QListWidget::item:hover { background: #2E2E2E; }
QProgressBar { background: #333333; border: 0; border-radius: 3px; max-height: 6px; }
QProgressBar::chunk { background: %(accent)s; border-radius: 3px; }
QScrollArea { border: 0; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #444444; border-radius: 3px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #5A5A5A; }
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page { height: 0; background: transparent; }
QStatusBar { background: %(rail)s; color: %(text2)s; }
""" % COLORS


def set_tone(widget, tone: str | None) -> None:
    """Switch a [tone=...] style variant without re-parsing a stylesheet.

    Calling setStyleSheet() on every progress tick forces a full style re-resolve and is
    what made the queue stutter; a dynamic property + repolish is cheap, and a no-op
    when the tone has not changed.
    """
    if widget.property("tone") == tone:
        return
    widget.setProperty("tone", tone)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    font = QFont(UI_FONT, BASE_PT)
    font.setHintingPreference(QFont.PreferNoHinting)
    font.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(font)
    app.setStyleSheet(QSS)
    from .motion import InteractionPolish  # noqa: PLC0415
    if not hasattr(app, "_polish"):
        app._polish = InteractionPolish(app)
        app.installEventFilter(app._polish)
