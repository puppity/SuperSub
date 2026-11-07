from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton

from subtitle_translator.gui.icons import _PATHS, icon
from subtitle_translator.gui.motion import InteractionPolish


def test_every_icon_renders(qtbot):
    for name in _PATHS:
        ic = icon(name)
        assert not ic.isNull() and not ic.pixmap(24, 24).toImage().isNull(), name


def test_buttons_get_hand_cursor_and_keyboard_only_focus(qtbot):
    app = QApplication.instance()
    polish = InteractionPolish(app)
    app.installEventFilter(polish)
    try:
        b = QPushButton("x")
        qtbot.addWidget(b)
        b.show()
        assert b.cursor().shape() == Qt.PointingHandCursor
        assert b.focusPolicy() == Qt.TabFocus
    finally:
        app.removeEventFilter(polish)
