"""Small line icons drawn from inline SVG, tinted to the theme.

One consistent set (24x24 grid, 2px round strokes) replaces emoji and text glyphs, which render
differently on every Windows version and cannot follow the theme colours.
"""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .theme import COLORS

_PATHS = {
    "play": '<path d="M7 4.5v15l12-7.5z" fill="{c}" stroke-linejoin="round"/>',
    "pause": '<rect x="6" y="4.5" width="4" height="15" rx="1" fill="{c}" stroke="none"/>'
             '<rect x="14" y="4.5" width="4" height="15" rx="1" fill="{c}" stroke="none"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2" fill="{c}" stroke="none"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "folder": '<path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2h9A1.5 1.5 0 0 1 21 9.5v8A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5z"/>',
    "file": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
    "export": '<path d="M12 3v12M7 10l5 5 5-5"/><path d="M5 19h14"/>',
    "refresh": '<path d="M20 11a8 8 0 0 0-14.5-4.6L4 8"/><path d="M4 4v4h4"/>'
               '<path d="M4 13a8 8 0 0 0 14.5 4.6L20 16"/><path d="M20 20v-4h-4"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "spark": '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>',
    "coins": '<ellipse cx="12" cy="7" rx="7" ry="3"/><path d="M5 7v5c0 1.7 3.1 3 7 3s7-1.3 7-3V7"/>'
             '<path d="M5 12v5c0 1.7 3.1 3 7 3s7-1.3 7-3v-5"/>',
    "terminal": '<rect x="3" y="4.5" width="18" height="15" rx="2"/><path d="M7 9.5l3 2.5-3 2.5M12.5 15H17"/>',
}


def _svg(name: str, color: str) -> bytes:
    body = _PATHS[name].replace("{c}", color)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{body}</svg>').encode()


def _pixmap(name: str, color: str, px: int) -> QPixmap:
    pix = QPixmap(px, px)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(_svg(name, color))).render(painter, QRectF(0, 0, px, px))
    painter.end()
    return pix


@lru_cache(maxsize=None)
def icon(name: str, color: str | None = None, disabled: str = "#6A6A6A") -> QIcon:
    """A crisp icon in the given colour (default: body text), greyed out when the button is disabled."""
    color = color or COLORS["text"]
    out = QIcon()
    for px in (16, 20, 24, 32, 48):
        out.addPixmap(_pixmap(name, color, px), QIcon.Normal)
        out.addPixmap(_pixmap(name, disabled, px), QIcon.Disabled)
    return out


def set_icon(button, name: str, color: str | None = None, size: int = 16) -> None:
    button.setIcon(icon(name, color))
    button.setIconSize(QSize(size, size))
