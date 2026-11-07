"""Speaker timeline under the review table.

One lane per character. The view glides to the selected line and follows the playhead while
the video plays, instead of jumping. Wheel scrolls time, Ctrl+wheel zooms around the cursor,
dragging pans, clicking a line selects it and clicking empty space seeks the video there.
"""
from __future__ import annotations

import bisect
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ..core.models import Cue
from .motion import reduced_motion
from .theme import COLORS

WINDOW = 60.0          # default seconds shown
MIN_SPAN, MAX_SPAN = 8.0, 900.0
LABEL_W = 104
LANE_H = 22
LANE_GAP = 4
TOP = 24               # ruler height
FRAME_MS = 16
GLIDE = 0.22           # share of the remaining distance covered per frame
FOLLOW_AT = 0.3        # the playhead sits this far into the view while playing
TICKS = (1, 2, 5, 10, 15, 30, 60, 120, 300, 600)


def _fmt(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t // 60):02d}:{int(t % 60):02d}"


class TimelineWidget(QWidget):
    cue_clicked = Signal(int)
    seek_requested = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cues: list[Cue] = []
        self.colors: dict[str | None, str] = {}
        self.names: dict[str | None, str] = {}
        self.selected: int | None = None
        self.playhead: float | None = None
        self.following = False                       # the video is playing: keep the playhead in view
        self._lanes: dict[str | None, tuple[list[float], list[Cue]]] = {}
        self._end = 0.0
        self._start = 0.0                            # what is drawn now
        self._target = 0.0                           # where the view is gliding to
        self._span = WINDOW
        self._span_target = WINDOW
        self._hover: int | None = None
        self._drag: tuple[float, float] | None = None
        self._dragged = False
        self._hits: list[tuple[QRectF, int]] = []
        self._anim = QTimer(self)
        self._anim.setInterval(FRAME_MS)
        self._anim.timeout.connect(self._step)
        self._last_frame = 0.0
        self.setMouseTracking(True)
        self.setMinimumHeight(48)
        self.setFocusPolicy(Qt.NoFocus)

    # ----- data -----
    def set_data(self, cues: list[Cue], colors: dict[str | None, str], names: dict[str | None, str]) -> None:
        self.cues = [c for c in cues if not c.skip]
        self.colors, self.names = colors, names
        self._lanes = {}
        for key in names:
            lane = sorted((c for c in self.cues if c.speaker == key), key=lambda c: c.start)
            self._lanes[key] = ([c.start for c in lane], lane)
        self._end = max((c.end for c in self.cues), default=0.0)
        content = TOP + 6 + max(len(names), 1) * (LANE_H + LANE_GAP)
        self.setMinimumHeight(min(content, TOP + 6 + 3 * (LANE_H + LANE_GAP)))
        self.setMaximumHeight(content + 8)          # never taller than its lanes
        self.update()

    def set_selected(self, cue_id: int | None) -> None:
        self.selected = cue_id
        cue = next((c for c in self.cues if c.id == cue_id), None)
        if cue is not None and not self.following:
            mid = (cue.start + cue.end) / 2
            if not (self._target + self._span * 0.15 <= mid <= self._target + self._span * 0.85):
                self._glide_to(mid - self._span / 2)
        self.update()

    def set_playhead(self, t: float | None) -> None:
        self.playhead = t
        if t is not None and self.following:
            self._glide_to(t - self._span * FOLLOW_AT)
        self.update()

    def set_following(self, on: bool) -> None:
        self.following = on
        if on and self.playhead is not None:
            self._glide_to(self.playhead - self._span * FOLLOW_AT)

    # ----- view motion -----
    def _clamp(self, start: float, span: float) -> float:
        return max(0.0, min(start, max(0.0, self._end + 5.0 - span)))

    def _glide_to(self, start: float) -> None:
        self._target = self._clamp(start, self._span_target)
        self._kick()

    def _kick(self) -> None:
        if reduced_motion():
            self._start, self._span = self._target, self._span_target
            self.update()
            return
        if not self._anim.isActive():
            self._last_frame = time.perf_counter()
            self._anim.start()

    def _step(self) -> None:
        now = time.perf_counter()
        # Frame-rate independent easing: a late frame moves further, so the glide keeps its pace.
        k = 1.0 - (1.0 - GLIDE) ** max(1.0, (now - self._last_frame) * 1000 / FRAME_MS)
        self._last_frame = now
        self._span += (self._span_target - self._span) * k
        self._start += (self._target - self._start) * k
        if abs(self._span - self._span_target) < 0.01 and abs(self._start - self._target) < 0.005:
            self._span, self._start = self._span_target, self._target
            self._anim.stop()
        self.update()

    def view(self) -> tuple[float, float]:
        return self._start, self._start + self._span

    # ----- geometry -----
    def _track_w(self) -> float:
        return max(self.width() - LABEL_W - 12, 10)

    def _x(self, t: float) -> float:
        return LABEL_W + (t - self._start) / self._span * self._track_w()

    def _t(self, x: float) -> float:
        return self._start + (x - LABEL_W) / self._track_w() * self._span

    def _hit(self, pos: QPointF) -> int | None:
        for rect, cue_id in self._hits:
            if rect.adjusted(-2, -4, 2, 4).contains(pos):
                return cue_id
        return None

    # ----- painting -----
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(COLORS["rail"]))
        t0, t1 = self.view()
        w = self._track_w()
        small = QFont(self.font())
        small.setPointSizeF(9.5)
        p.setFont(small)

        # Ruler: the tick step adapts to the zoom so labels never crowd.
        step = next((s for s in TICKS if w / (self._span / s) >= 70), TICKS[-1])
        k = int(t0 // step)
        while k * step <= t1:
            t = k * step
            x = self._x(t)
            if x >= LABEL_W:
                p.setPen(QColor("#2C2C2C"))
                p.drawLine(QPointF(x, TOP - 4), QPointF(x, self.height()))
                label = _fmt(t)
                if x + 4 + p.fontMetrics().horizontalAdvance(label) <= self.width() - 4:
                    p.setPen(QColor(COLORS["text3"]))
                    p.drawText(QPointF(x + 4, 15), label)
            k += 1

        self._hits = []
        for lane, key in enumerate(self.names):
            y = TOP + 2 + lane * (LANE_H + LANE_GAP)
            color = QColor(self.colors.get(key, "#A8A29E"))
            p.setPen(color)
            p.drawText(QRectF(10, y, LABEL_W - 16, LANE_H), Qt.AlignVCenter | Qt.AlignLeft,
                       p.fontMetrics().elidedText(self.names[key], Qt.ElideRight, LABEL_W - 16))
            track = QPainterPath()
            track.addRoundedRect(QRectF(LABEL_W, y, w, LANE_H), 6, 6)
            p.fillPath(track, QColor("#202020"))
            starts, lane_cues = self._lanes.get(key, ([], []))
            # Only the lines that can be on screen: find the first by start time, step back a few
            # for long lines that began before the view.
            i = max(0, bisect.bisect_left(starts, t0) - 8)
            p.setPen(Qt.NoPen)
            for c in lane_cues[i:]:
                if c.start > t1:
                    break
                if c.end < t0:
                    continue
                x0, x1 = max(self._x(c.start), LABEL_W), min(self._x(c.end), LABEL_W + w)
                rect = QRectF(x0, y + 3, max(x1 - x0, 3), LANE_H - 6)
                fill = QColor(color)
                if c.id == self.selected:
                    fill = fill.lighter(125)
                elif c.id == self._hover:
                    fill = fill.lighter(112)
                else:
                    fill.setAlphaF(0.72)
                p.setBrush(fill)
                p.drawRoundedRect(rect, 4, 4)
                if c.id == self.selected:
                    p.setBrush(Qt.NoBrush)
                    p.setPen(QPen(QColor(COLORS["text"]), 1.6))
                    p.drawRoundedRect(rect.adjusted(-1.5, -1.5, 1.5, 1.5), 5, 5)
                    p.setPen(Qt.NoPen)
                self._hits.append((rect, c.id))

        if self.playhead is not None and t0 <= self.playhead <= t1:
            x = self._x(self.playhead)
            p.setPen(QPen(QColor(COLORS["accent"]), 1.5))
            p.drawLine(QPointF(x, TOP - 2), QPointF(x, self.height()))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(COLORS["accent"]))
            head = QPainterPath()
            head.moveTo(x - 5, TOP - 8)
            head.lineTo(x + 5, TOP - 8)
            head.lineTo(x, TOP - 2)
            head.closeSubpath()
            p.drawPath(head)
        p.end()

    # ----- input -----
    def wheelEvent(self, event) -> None:
        dy = event.angleDelta().y() or event.angleDelta().x()
        if not dy:
            return
        steps = dy / 120.0
        if event.modifiers() & Qt.ControlModifier:
            # Zoom around the time under the cursor so it stays put.
            anchor_x = event.position().x()
            anchor_t = self._t(anchor_x)
            share = (anchor_x - LABEL_W) / self._track_w()
            self._span_target = max(MIN_SPAN, min(MAX_SPAN, self._span_target * (0.8 ** steps)))
            self._target = self._clamp(anchor_t - share * self._span_target, self._span_target)
            self._kick()
        else:
            self.following = False
            self._glide_to(self._target - steps * self._span_target * 0.15)
        event.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag = (event.position().x(), self._target)
            self._dragged = False

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        if self._drag is not None and event.buttons() & Qt.LeftButton:
            dx = pos.x() - self._drag[0]
            if abs(dx) > 4:
                self._dragged = True
                self.following = False
                self.setCursor(Qt.ClosedHandCursor)
                start = self._clamp(self._drag[1] - dx / self._track_w() * self._span, self._span)
                self._start = self._target = start          # panning tracks the hand directly
                self.update()
            return
        hover = self._hit(pos)
        if hover != self._hover:
            self._hover = hover
            cue = next((c for c in self.cues if c.id == hover), None)
            self.setToolTip(f"{_fmt(cue.start)}  {cue.thai or cue.src}" if cue else "")
            self.update()
        self.setCursor(Qt.PointingHandCursor if hover is not None or pos.x() > LABEL_W else Qt.ArrowCursor)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton or self._drag is None:
            return
        self._drag = None
        self.unsetCursor()
        if self._dragged:
            return
        pos = event.position()
        cue_id = self._hit(pos)
        if cue_id is not None:
            self.cue_clicked.emit(cue_id)
        elif pos.x() > LABEL_W:
            self.seek_requested.emit(max(0.0, self._t(pos.x())))

    def leaveEvent(self, _event) -> None:
        if self._hover is not None:
            self._hover = None
            self.update()
