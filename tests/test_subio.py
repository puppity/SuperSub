import pytest

from subtitle_translator.core.subio import (
    SubtitleReadError, guess_lang, load_cues, read_text_any,
)

ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1
Style: OP,Arial,40,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,8,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.00,Default,Haru,0,0,0,,お姉ちゃん、起きて！
Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,{\\i1}うるさいなぁ{\\i0}\\Nあと五分だけ。
Dialogue: 0,0:00:07.00,0:00:09.00,OP,,0,0,0,,{\\k20}ra{\\k30}la
Comment: 0,0:00:10.00,0:00:11.00,Default,,0,0,0,,note
Dialogue: 0,0:00:12.00,0:00:13.00,Default,,0,0,0,,{\\p1}m 0 0 l 10 10{\\p0}
"""

SRT = "1\n00:00:01,000 --> 00:00:02,500\nWake up, sis!\n\n2\n00:00:03,000 --> 00:00:04,000\nFive more minutes.\n"


def test_load_ass_cues_marks_effects_and_keeps_order(tmp_path):
    p = tmp_path / "a.ass"
    p.write_text(ASS, encoding="utf-8")
    cues, subs = load_cues(p)
    assert [c.src for c in cues][:3] == ["お姉ちゃん、起きて！", "うるさいなぁ\nあと五分だけ。", "rala"]
    assert [c.id for c in cues] == [1, 2, 3, 4]          # comment line dropped, drawing kept as skip
    assert cues[0].actor == "Haru" and cues[0].style == "Default"
    assert cues[0].start == 1.0 and cues[0].end == 3.0
    assert [c.skip for c in cues] == [False, False, True, True]
    assert subs.events[cues[2].src_index].style == "OP"


def test_load_srt_cp932_and_utf16(tmp_path):
    jp = "1\n00:00:01,000 --> 00:00:02,000\nこんにちは\n"
    p1 = tmp_path / "jp.srt"
    p1.write_bytes(jp.encode("cp932"))
    assert load_cues(p1)[0][0].src == "こんにちは"
    p2 = tmp_path / "en.srt"
    p2.write_bytes(SRT.encode("utf-16"))
    cues, _ = load_cues(p2)
    assert [c.src for c in cues] == ["Wake up, sis!", "Five more minutes."]


def test_read_text_any_rejects_garbage(tmp_path):
    p = tmp_path / "bad.srt"
    p.write_bytes(b"")
    with pytest.raises(SubtitleReadError):
        load_cues(p)


def test_guess_lang(tmp_path):
    p = tmp_path / "e.srt"
    p.write_text(SRT, encoding="utf-8")
    assert guess_lang(load_cues(p)[0]) == "en"
    p2 = tmp_path / "j.ass"
    p2.write_text(ASS, encoding="utf-8")
    assert guess_lang(load_cues(p2)[0]) == "ja"
    assert read_text_any(p2).startswith("[Script Info]")
