from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton, QScrollBar, QStackedWidget, QWidget

from subtitle_translator.gui.icons import _PATHS, icon
from subtitle_translator.gui.motion import polish_controls


def test_every_icon_renders(qtbot):
    for name in _PATHS:
        ic = icon(name)
        assert not ic.isNull() and not ic.pixmap(24, 24).toImage().isNull(), name


def test_buttons_get_hand_cursor_and_keyboard_only_focus(qtbot):
    box = QWidget()
    qtbot.addWidget(box)
    b, bar = QPushButton("x", box), QScrollBar(box)
    polish_controls(box)
    assert b.cursor().shape() == Qt.PointingHandCursor and b.focusPolicy() == Qt.TabFocus
    assert bar.cursor().shape() != Qt.PointingHandCursor


def test_slide_in_ends_at_rest_even_when_restarted(qtbot, monkeypatch):
    from subtitle_translator.gui import motion
    monkeypatch.setattr(motion, "reduced_motion", lambda: False)
    stack = QStackedWidget()
    qtbot.addWidget(stack)
    page = QWidget()
    stack.addWidget(page)
    stack.resize(400, 300)
    stack.show()
    motion.slide_in(page)
    qtbot.wait(40)
    motion.slide_in(page)                      # switched away and back mid-slide
    qtbot.waitUntil(lambda: page.pos().x() == 0, timeout=2000)
