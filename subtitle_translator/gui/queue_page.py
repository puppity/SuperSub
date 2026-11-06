from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class QueuePage(QWidget):
    open_review = Signal(object)

    def __init__(self, settings, persist, parent=None):
        super().__init__(parent)
        QVBoxLayout(self).addWidget(QLabel("คิวงาน"))
