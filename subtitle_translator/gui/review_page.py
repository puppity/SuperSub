from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class ReviewPage(QWidget):
    def __init__(self, settings, persist, parent=None):
        super().__init__(parent)
        QVBoxLayout(self).addWidget(QLabel("ตรวจ/แก้"))

    def load(self, project):
        self.project = project
