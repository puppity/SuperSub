from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QMainWindow, QStackedWidget, QToolButton,
    QVBoxLayout, QWidget,
)

from ..core.proxy import stop_all as stop_proxies
from ..core.settings import Settings, save_settings
from .queue_page import QueuePage
from .review_page import ReviewPage
from .settings_page import SettingsPage
from .theme import COLORS

# Glyphs from Segoe Fluent Icons (Windows 11) / Segoe MDL2 Assets (Windows 10): same code points.
NAV = [("คิวงาน", ""), ("ตรวจ/แก้", ""), ("ตั้งค่า", "")]


def glyph_icon(glyph: str) -> QIcon:
    """A two-state icon drawn from an icon-font glyph: dim when off, bright when selected."""
    icon = QIcon()
    for state, color in ((QIcon.Off, COLORS["text3"]), (QIcon.On, COLORS["accent"])):
        pix = QPixmap(48, 48)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.TextAntialiasing)
        font = QFont("Segoe Fluent Icons")
        font.setFamilies(["Segoe Fluent Icons", "Segoe MDL2 Assets", "Segoe UI Symbol"])
        font.setPixelSize(30)
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(pix.rect(), Qt.AlignCenter, glyph)
        painter.end()
        icon.addPixmap(pix, QIcon.Normal, state)
    return icon


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, settings_path: Path | None = None):
        super().__init__()
        self.settings = settings
        self.settings_path = settings_path
        self.setWindowTitle("แปลซับไทย")
        self.resize(1440, 900)

        rail = QFrame(objectName="rail")
        rail.setFixedWidth(84)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(8, 16, 8, 16)
        rail_layout.setSpacing(8)
        self.nav_group = QButtonGroup(self)
        self.nav_buttons: list[QToolButton] = []
        for i, (label, glyph) in enumerate(NAV):
            b = QToolButton(text=label, checkable=True)
            b.setProperty("nav", True)
            b.setIcon(glyph_icon(glyph))
            b.setIconSize(QSize(24, 24))
            b.setFixedSize(68, 64)
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setCursor(Qt.PointingHandCursor)
            b.setAccessibleName(label)
            self.nav_group.addButton(b, i)
            self.nav_buttons.append(b)
            if i == 2:
                rail_layout.addStretch(1)
            rail_layout.addWidget(b)
        self.nav_group.idClicked.connect(self.go)

        self.queue_page = QueuePage(settings, self.persist)
        self.review_page = ReviewPage(settings, self.persist)
        self.settings_page = SettingsPage(settings, self.persist)
        self.stack = QStackedWidget()
        for page in (self.queue_page, self.review_page, self.settings_page):
            self.stack.addWidget(page)
        self.queue_page.open_review.connect(self.show_review)

        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(rail)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        self.go(0)

    def go(self, index: int) -> None:
        self.nav_buttons[index].setChecked(True)
        if self.stack.currentIndex() == index:
            return
        self.stack.setCurrentIndex(index)
        self._fade_in(self.stack.currentWidget())

    def _fade_in(self, page: QWidget) -> None:
        """Short fade on page switch. The effect is removed afterwards: a live opacity
        effect forces offscreen rendering of the whole page (and its video view)."""
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", page)
        anim.setDuration(160)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.finished.connect(lambda: page.setGraphicsEffect(None))
        anim.start(QPropertyAnimation.DeleteWhenStopped)

    def show_review(self, project) -> None:
        self.review_page.load(project)
        self.go(1)

    def closeEvent(self, event) -> None:
        pages = (self.queue_page, self.review_page, self.settings_page)
        results = [page.shutdown() for page in pages]     # stop every page, not just the first
        if all(results):
            stop_proxies()                 # never leave a background ffmpeg running
            event.accept()
            return
        for page in pages:
            if hasattr(page, "reopen"):
                page.reopen()
        self.statusBar().showMessage("กำลังหยุดงาน… กรุณารอสักครู่", 8000)
        event.ignore()

    def persist(self) -> None:
        save_settings(self.settings, self.settings_path)
