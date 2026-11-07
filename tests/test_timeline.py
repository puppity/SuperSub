from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent

from subtitle_translator.core.models import Cue
from subtitle_translator.gui.timeline_widget import LABEL_W, TOP, WINDOW, TimelineWidget


def _tl(qtbot):
    tl = TimelineWidget()
    qtbot.addWidget(tl)
    tl.resize(LABEL_W + 12 + 600, 120)
    cues = [Cue(i, i * 10.0, i * 10.0 + 4, f"l{i}", speaker="a") for i in range(1, 60)]
    tl.set_data(cues, {"a": "#FBBF24"}, {"a": "A"})
    tl.show()
    return tl


def test_selecting_a_far_line_glides_there(qtbot):
    tl = _tl(qtbot)
    tl.set_selected(40)                                   # 400 s: far outside the first minute
    assert tl.view()[0] < 1                               # it does not jump ...
    qtbot.waitUntil(lambda: abs(tl.view()[0] - (402 - WINDOW / 2)) < 0.01, timeout=3000)   # ... it glides


def test_follows_the_playhead_while_playing(qtbot):
    tl = _tl(qtbot)
    tl.set_following(True)
    tl.set_playhead(300.0)
    qtbot.waitUntil(lambda: abs(tl.view()[0] - (300 - WINDOW * 0.3)) < 0.01, timeout=3000)
    tl.set_following(False)
    tl.set_playhead(500.0)                                # paused: the view stays where it is
    qtbot.wait(100)
    assert abs(tl.view()[0] - (300 - WINDOW * 0.3)) < 0.01


def test_ctrl_wheel_zooms_and_click_seeks_or_selects(qtbot):
    tl = _tl(qtbot)
    x = LABEL_W + 300
    ev = QWheelEvent(QPointF(x, 40), QPointF(x, 40), QPoint(0, 0), QPoint(0, 240),
                     Qt.NoButton, Qt.ControlModifier, Qt.NoScrollPhase, False)
    tl.wheelEvent(ev)
    qtbot.waitUntil(lambda: abs(tl.view()[1] - tl.view()[0] - WINDOW * 0.64) < 0.01, timeout=3000)
    seeks, clicks = [], []
    tl.seek_requested.connect(seeks.append)
    tl.cue_clicked.connect(clicks.append)
    tl.repaint()
    lane_y = TOP + 2 + 11
    t0, t1 = tl.view()
    gap_t = next(t for t in (t0 + k * 0.5 for k in range(200)) if (t % 10) > 6)    # between lines
    gap_x = LABEL_W + (gap_t - t0) / (t1 - t0) * (tl.width() - LABEL_W - 12)
    qtbot.mouseClick(tl, Qt.LeftButton, pos=QPoint(int(gap_x), lane_y))
    assert seeks and abs(seeks[-1] - gap_t) < 0.5
    line_t = next(t for t in (t0 + k * 0.5 for k in range(200)) if 1 < (t % 10) < 3 and t > 10)
    line_x = LABEL_W + (line_t - t0) / (t1 - t0) * (tl.width() - LABEL_W - 12)
    qtbot.mouseClick(tl, Qt.LeftButton, pos=QPoint(int(line_x), lane_y))
    assert clicks == [int(line_t // 10)]
