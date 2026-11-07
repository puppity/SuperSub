import subprocess

import pytest

from subtitle_translator.core.media import tool_path
from subtitle_translator.core.models import Cue
from subtitle_translator.gui import mpv_preview as mp


def test_caption_event_escapes_and_colours():
    ev = mp.caption_event("a{b}\\c\nสอง", "#FBBF24")
    assert "\\1c&H24BFFB&" in ev and ev.endswith("a\\{b\\}\\\\c\\Nสอง")
    assert mp.caption_event("", "#fff") == ""


@pytest.fixture
def clip(tmp_path):
    if not mp.mpv_available():
        pytest.skip("libmpv-2.dll not in bin")
    v = tmp_path / "clip.mp4"
    r = subprocess.run([tool_path("ffmpeg"), "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=320x240:d=6:r=25",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(v)], capture_output=True)
    if r.returncode != 0:
        pytest.skip("ffmpeg cannot make a clip")
    return v


def test_mpv_preview_seeks_plays_and_reports(qtbot, clip):
    pv = mp.MpvPreview()
    qtbot.addWidget(pv)
    pv.show()
    durs, pos, play = [], [], []
    pv.duration_changed.connect(durs.append)
    pv.position_changed.connect(pos.append)
    pv.playing_changed.connect(play.append)
    try:
        pv.set_source(str(clip))
        pv.show_cue(Cue(id=1, start=3.0, end=4.0, src="x"), "ไทย", "#FFFFFF")   # before the file is open
        qtbot.waitUntil(lambda: bool(durs) and abs(durs[-1] - 6.0) < 0.3, timeout=20000)
        qtbot.waitUntil(lambda: bool(pos) and abs(pos[-1] - 3.0) < 0.2, timeout=10000)
        assert not pv.playing
        pv.toggle()
        qtbot.waitUntil(lambda: bool(play) and play[-1] is True and pos[-1] > 3.3, timeout=10000)
        pv.stop()
        qtbot.waitUntil(lambda: pos[-1] < 3.3 and play[-1] is False and abs(pos[-1] - 3.0) < 0.2, timeout=10000)
    finally:
        assert pv.shutdown()
