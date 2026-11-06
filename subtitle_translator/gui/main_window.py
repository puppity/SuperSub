from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QMainWindow, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from ..core.settings import Settings, save_settings
from .queue_page import QueuePage
from .review_page import ReviewPage
from .settings_page import SettingsPage

NAV = [("คิวงาน", "☰"), ("ตรวจ/แก้", "✎"), ("ตั้งค่า", "⚙")]


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, settings_path: Path | None = None):
        super().__init__()
        self.settings = settings
        self.settings_path = settings_path
        self.setWindowTitle("แปลซับไทย")
        self.resize(1440, 900)

        rail = QFrame(objectName="rail")
        rail.setFixedWidth(76)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(6, 16, 6, 16)
        rail_layout.setSpacing(6)
        self.nav_group = QButtonGroup(self)
        self.nav_buttons: list[QToolButton] = []
        for i, (label, glyph) in enumerate(NAV):
            b = QToolButton(text=f"{glyph}\n{label}", checkable=True)
            b.setProperty("nav", True)
            b.setFixedSize(64, 56)
            b.setToolButtonStyle(Qt.ToolButtonTextOnly)
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
        self.stack.setCurrentIndex(index)
        self.nav_buttons[index].setChecked(True)

    def show_review(self, project) -> None:
        self.review_page.load(project)
        self.go(1)

    def closeEvent(self, event) -> None:
        pages = (self.queue_page, self.review_page, self.settings_page)
        results = [page.shutdown() for page in pages]     # stop every page, not just the first
        if all(results):
            event.accept()
            return
        for page in pages:
            if hasattr(page, "reopen"):
                page.reopen()
        self.statusBar().showMessage("กำลังหยุดงาน… กรุณารอสักครู่", 8000)
        event.ignore()

    def persist(self) -> None:
        save_settings(self.settings, self.settings_path)
