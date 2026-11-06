import pysubs2
import pytest

from helpers import make_episode
from test_subio import ASS
from subtitle_translator.core.models import Character, Project
from subtitle_translator.core.settings import Settings
from subtitle_translator.core.subio import load_cues
from subtitle_translator.export import export_project, output_paths
from subtitle_translator.export.ass_writer import style_name, write_ass
from subtitle_translator.export.srt_writer import srt_timestamp, write_srt


def test_srt_timestamp():
    assert srt_timestamp(3725.4567) == "01:02:05,457"


def test_write_srt_bom_colors_and_fallback(tmp_path):
    ep = make_episode(3)
    ep.cues[0].thai, ep.cues[0].speaker = "พี่ ตื่นได้แล้ว!\nเดี๋ยวสาย", "haru"
    ep.cues[1].skip = True
    path = write_srt(ep.cues, tmp_path / "a.th.srt", colors={"haru": "#F472B6"})
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    assert text.startswith("1\n00:00:02,000 --> 00:00:03,500\n<font color=\"#F472B6\">พี่ ตื่นได้แล้ว!\nเดี๋ยวสาย</font>\n")
    assert "2\n00:00:06,000 --> 00:00:07,500\nline 3\n" in text
    assert "line 2" not in text


def test_write_ass_styles_per_character_and_keeps_effects(tmp_path):
    src = tmp_path / "orig.ass"
    src.write_text(ASS, encoding="utf-8")
    cues, _ = load_cues(src)
    ep = make_episode(1)
    ep.source, ep.cues = str(src), cues
    ep.characters = [Character(key="haru", name_src="ハル", name_th="ฮารุ", color="#F472B6")]
    cues[0].speaker, cues[0].thai = "haru", "พี่ ตื่นได้แล้ว!"
    cues[1].thai = "เสียงดัง\nขออีกห้านาที"
    out = write_ass(ep, tmp_path / "x.th.ass", "Leelawadee UI", 52)
    assert out.read_bytes().startswith(b"\xef\xbb\xbf")
    subs = pysubs2.load(str(out), encoding="utf-8-sig")
    st = subs.styles[style_name("haru")]
    assert (st.primarycolor.r, st.primarycolor.g, st.primarycolor.b) == (0xF4, 0x72, 0xB6)
    assert st.fontname == "Leelawadee UI"
    texts = [e.text for e in subs.events]
    assert "พี่ ตื่นได้แล้ว!" in texts
    assert "เสียงดัง\\Nขออีกห้านาที" in texts
    assert any("\\k20" in t for t in texts)              # karaoke line copied untouched
    assert subs.events[[e.text for e in subs.events].index("พี่ ตื่นได้แล้ว!")].style == style_name("haru")


def test_export_project_paths_and_overwrite(tmp_path):
    ep = make_episode(2)
    ep.video_path = str(tmp_path / "EP01.mkv")
    ep.source = str(tmp_path / "missing.srt")
    for c in ep.cues:
        c.thai = "ไทย"
    p = Project(episode=ep, backend="api", model="claude-opus-5-5")
    srt, ass = export_project(p, Settings())
    assert (srt.name, ass.name) == ("EP01.th.srt", "EP01.th.ass")
    with pytest.raises(FileExistsError):
        export_project(p, Settings())
    export_project(p, Settings(), overwrite=True)
    assert output_paths(ep.video_path, str(tmp_path / "out"))[0] == tmp_path / "out" / "EP01.th.srt"
