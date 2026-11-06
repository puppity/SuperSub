from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class SettingsPage(QWidget):
    def __init__(self, settings, persist, parent=None):
        super().__init__(parent)
        QVBoxLayout(self).addWidget(QLabel("ตั้งค่า"))
