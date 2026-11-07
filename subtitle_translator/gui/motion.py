"""Motion and interaction polish shared by every page.

- Respects the Windows "Show animations" setting: when it is off, nothing animates.
- Every clickable control gets a hand cursor.
- Buttons take focus from the keyboard only, so the focus ring shows when tabbing but a mouse
  click does not leave a ring behind.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPropertyAnimation, Qt
from PySide6.QtWidgets import QAbstractButton, QAbstractSlider, QComboBox, QProgressBar, QPushButton, QWidget

_reduced: bool | None = None


def reduced_motion() -> bool:
    """True when the user turned animations off in Windows (Accessibility > Visual effects)."""
    global _reduced
    if _reduced is None:
        _reduced = False
        if sys.platform == "win32":
            try:
                import ctypes  # noqa: PLC0415
                value = ctypes.c_bool(True)
                SPI_GETCLIENTAREAANIMATION = 0x1042
                if ctypes.windll.user32.SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0,
                                                              ctypes.byref(value), 0):
                    _reduced = not value.value
            except (OSError, AttributeError):
                pass
    return _reduced


class InteractionPolish(QObject):
    """Installed on the application: adjusts each control once, when Qt first polishes it."""

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Polish and isinstance(obj, QWidget):
            if isinstance(obj, (QAbstractButton, QComboBox, QAbstractSlider)) and not obj.testAttribute(
                    Qt.WA_SetCursor):
                obj.setCursor(Qt.PointingHandCursor)
            if isinstance(obj, QPushButton) and obj.focusPolicy() == Qt.StrongFocus:
                obj.setFocusPolicy(Qt.TabFocus)
        return False


def slide_in(page: QWidget, dx: int = 18, ms: int = 200) -> None:
    """Page entrance: a short slide from the right with ease-out. Moving a widget is safe for
    pages that hold a native video window, unlike an opacity effect."""
    if reduced_motion():
        return
    end = page.pos()
    anim = QPropertyAnimation(page, b"pos", page)
    anim.setDuration(ms)
    anim.setStartValue(end + QPoint(dx, 0))
    anim.setEndValue(end)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    anim.start(QPropertyAnimation.DeleteWhenStopped)


def animate_progress(bar: QProgressBar, value: int, ms: int = 260) -> None:
    """Glide a progress bar to a new value instead of jumping."""
    old = getattr(bar, "_glide", None)
    if old is not None:
        old.stop()
    if reduced_motion() or value < bar.value() or not bar.isVisible():
        bar.setValue(value)
        return
    anim = QPropertyAnimation(bar, b"value", bar)
    anim.setDuration(ms)
    anim.setStartValue(bar.value())
    anim.setEndValue(value)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    bar._glide = anim
    anim.start()
