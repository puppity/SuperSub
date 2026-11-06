from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..core.models import Cue

WINDOW = 60.0      # seconds shown
LABEL_W = 96
LANE_H = 20


class TimelineWidget(QWidget):
    cue_clicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cues: list[Cue] = []
        self.colors: dict[str | None, str] = {}
        self.names: dict[str | None, str] = {}
        self.selected: int | None = None
        self._hits: list[tuple[QRectF, int]] = []
        self.setMinimumHeight(40)

    def set_data(self, cues: list[Cue], colors: dict[str | None, str], names: dict[str | None, str]) -> None:
        self.cues = [c for c in cues if not c.skip]
        self.colors, self.names = colors, names
        lanes = len(names)
        self.setMinimumHeight(24 + lanes * (LANE_H + 4))
        self.update()

    def set_selected(self, cue_id: int | None) -> None:
        self.selected = cue_id
        self.update()

    def _window(self) -> tuple[float, float]:
        center = 0.0
        for c in self.cues:
            if c.id == self.selected:
                center = (c.start + c.end) / 2
        start = max(0.0, center - WINDOW / 2)
        return start, start + WINDOW

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#222222"))
        t0, t1 = self._window()
        width = max(self.width() - LABEL_W - 12, 10)
        p.setPen(QColor("#8F8F8F"))
        for k in range(5):
            t = t0 + (t1 - t0) * k / 4
            x = LABEL_W + width * k / 4
            p.drawText(int(x), 14, f"{int(t // 60):02d}:{int(t % 60):02d}")
        self._hits = []
        for lane, key in enumerate(self.names):
            y = 22 + lane * (LANE_H + 4)
            p.setPen(QColor(self.colors.get(key, "#A8A29E")))
            p.drawText(8, y + 15, self.names[key])
            p.fillRect(QRectF(LABEL_W, y, width, LANE_H), QColor("#1A1A1A"))
            for c in self.cues:
                if c.speaker != key or c.end < t0 or c.start > t1:
                    continue
                x0 = LABEL_W + (c.start - t0) / (t1 - t0) * width
                x1 = LABEL_W + (c.end - t0) / (t1 - t0) * width
                rect = QRectF(x0, y + 3, max(x1 - x0, 3), LANE_H - 6)
                p.fillRect(rect, QColor(self.colors.get(key, "#A8A29E")))
                if c.id == self.selected:
                    p.setPen(QPen(QColor("#FFFFFF"), 2))
                    p.drawRect(rect)
                self._hits.append((rect, c.id))
        p.end()

    def mousePressEvent(self, event) -> None:
        pos = event.position()
        for rect, cue_id in self._hits:
            if rect.adjusted(-2, -4, 2, 4).contains(pos):
                self.cue_clicked.emit(cue_id)
                return
