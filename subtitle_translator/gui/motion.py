"""Motion and interaction polish shared by every page.

- Respects the Windows "Show animations" setting: when it is off, nothing animates.
- Every clickable control gets a hand cursor.
- Buttons take focus from the keyboard only, so the focus ring shows when tabbing but a mouse
  click does not leave a ring behind.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt
from PySide6.QtWidgets import QAbstractButton, QAbstractSlider, QComboBox, QProgressBar, QPushButton, QScrollBar, QWidget

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


def polish_controls(root: QWidget) -> None:
    """Hand cursor on every clickable control under root, and keyboard-only focus for buttons.

    Called once per window or card when it is built. An application-wide event filter would do
    the same but runs Python code for every event of every widget, which costs frame time.
    """
    for w in [root, *root.findChildren(QWidget)]:
        clickable = isinstance(w, (QAbstractButton, QComboBox, QAbstractSlider)) and not isinstance(w, QScrollBar)
        if clickable and not w.testAttribute(Qt.WA_SetCursor):
            w.setCursor(Qt.PointingHandCursor)
        if isinstance(w, QPushButton) and w.focusPolicy() == Qt.StrongFocus:
            w.setFocusPolicy(Qt.TabFocus)


def slide_in(page: QWidget, dx: int = 18, ms: int = 200) -> None:
    """Page entrance: a short slide from the right with ease-out. Moving a widget is safe for
    pages that hold a native video window, unlike an opacity effect."""
    if reduced_motion():
        return
    old = getattr(page, "_slide", None)
    if old is not None:
        old.stop()
    # The resting place comes from the parent, not page.pos(): a slide still running from a
    # quick switch back and forth would otherwise leave the page shifted for good.
    parent = page.parentWidget()
    end = parent.contentsRect().topLeft() if parent is not None else page.pos()
    anim = QPropertyAnimation(page, b"pos", page)
    page._slide = anim
    anim.setDuration(ms)
    anim.setStartValue(end + QPoint(dx, 0))
    anim.setEndValue(end)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    anim.start()


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
