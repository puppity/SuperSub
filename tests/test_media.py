import shutil
import subprocess
from pathlib import Path

import pytest

from subtitle_translator.core.media import (
    MediaError, SubTrack, extract_sub, grab_frame, list_sub_tracks, pick_track,
    sidecar_subs, tool_path,
)
from subtitle_translator.core.subio import load_cues


def T(i, codec, lang):
    return SubTrack(sub_index=i, codec=codec, language=lang, title="")


def test_pick_track_prefers_japanese_then_english():
    tracks = [T(0, "subrip", "eng"), T(1, "ass", "jpn"), T(2, "hdmv_pgs_subtitle", "jpn")]
    assert pick_track(tracks) == (tracks[1], [])
    assert pick_track(tracks, prefer=("en", "ja")) == (tracks[0], [])


def test_pick_track_asks_when_ambiguous_or_unknown():
    two = [T(0, "ass", "jpn"), T(1, "subrip", "jpn")]
    assert pick_track(two) == (None, two)
    und = [T(0, "subrip", "und")]
    assert pick_track(und) == (und[0], [])
    many_und = [T(0, "subrip", "und"), T(1, "ass", "fre")]
    assert pick_track(many_und) == (None, many_und)
    assert pick_track([T(0, "hdmv_pgs_subtitle", "jpn")]) == (None, [])


def test_sidecar_subs(tmp_path):
    v = tmp_path / "EP01 [x].mkv"
    v.write_bytes(b"")
    (tmp_path / "EP01 [x].srt").write_text("x")
    (tmp_path / "EP01 [x].ja.ass").write_text("x")
    (tmp_path / "EP01 [x].th.srt").write_text("x")
    (tmp_path / "EP02.srt").write_text("x")
    names = sorted(p.name for p in sidecar_subs(v))
    assert names == ["EP01 [x].ja.ass", "EP01 [x].srt"]


def test_sidecar_subs_ignores_other_episodes(tmp_path):
    v = tmp_path / "EP1.mkv"
    v.write_bytes(b"")
    (tmp_path / "EP1.srt").write_text("x")
    (tmp_path / "EP1.ja.ass").write_text("x")
    (tmp_path / "EP10.srt").write_text("x")
    names = sorted(p.name for p in sidecar_subs(v))
    assert names == ["EP1.ja.ass", "EP1.srt"]


def _have_ffmpeg():
    try:
        tool_path("ffmpeg")
        tool_path("ffprobe")
        return True
    except MediaError:
        return False


@pytest.mark.media
@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not available")
def test_list_and_extract_real_mkv(tmp_path):
    ass = tmp_path / "s.ass"
    ass.write_text(
        "[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,お姉ちゃん\n",
        encoding="utf-8",
    )
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nSis!\n", encoding="utf-8")
    mkv = tmp_path / "v.mkv"
    subprocess.run([
        tool_path("ffmpeg"), "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=c=black:s=320x240:d=5",
        "-i", str(ass), "-i", str(srt),
        "-map", "0", "-map", "1", "-map", "2",
        "-c:v", "libx264", "-c:s", "copy",
        "-metadata:s:s:0", "language=jpn", "-metadata:s:s:1", "language=eng",
        str(mkv),
    ], check=True)
    tracks = list_sub_tracks(mkv)
    assert [(t.codec, t.language) for t in tracks] == [("ass", "jpn"), ("subrip", "eng")]
    out = extract_sub(mkv, tracks[0], tmp_path)
    assert out.suffix == ".ass"
    assert load_cues(out)[0][0].src == "お姉ちゃん"
    png = grab_frame(mkv, 1.0, tmp_path / "f.png", width=160)
    assert png.exists() and png.stat().st_size > 0


def test_extract_cache_path_differs_for_same_stem(tmp_path, monkeypatch):
    from subtitle_translator.core import media

    def fake_run(cmd, timeout=None):
        Path(cmd[-1]).write_text("x")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(media, "_run", fake_run)
    monkeypatch.setattr(media, "tool_path", lambda name: name)
    a = tmp_path / "a" / "EP01.mkv"
    b = tmp_path / "b" / "EP01.mkv"
    t = SubTrack(sub_index=0, codec="subrip", language="jpn", title="")
    pa = extract_sub(a, t, tmp_path / "cache")
    pb = extract_sub(b, t, tmp_path / "cache")
    assert pa != pb and pa.name == pb.name == "EP01.s0.srt"
    assert pa.parent.parent == pb.parent.parent == tmp_path / "cache"
