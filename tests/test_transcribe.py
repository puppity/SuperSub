import subprocess
from types import SimpleNamespace

import pytest

from subtitle_translator.core import transcribe as tr
from subtitle_translator.core.media import list_sub_tracks, tool_path
from subtitle_translator.core.settings import Settings
from subtitle_translator.core.transcribe import TRANSCRIBE, Segment, TranscribeError, Word, split_segments


def test_split_keeps_short_segments_and_cuts_long_ones_at_words():
    short = Segment(0.0, 2.0, " こんにちは", [])
    words = [Word(10 + i, 11 + i, "あいうえおかきくけこ") for i in range(8)]   # 8 s, 80 chars
    long = Segment(10.0, 18.0, "".join(w.text for w in words), words)
    cues = split_segments([short, long, Segment(20, 21, "  ", [])], "ja")
    assert cues[0] == (0.0, 2.0, "こんにちは")
    rest = cues[1:]
    assert len(rest) >= 2
    assert all(e - s <= tr.MAX_CUE_S and len(t) <= tr.MAX_CHARS["ja"] for s, e, t in rest)
    assert "".join(t for _, _, t in rest) == long.text


def test_split_english_keeps_word_spacing():
    words = [Word(i * 0.5, i * 0.5 + 0.4, " word%d" % i) for i in range(30)]   # 15 s
    cues = split_segments([Segment(0, 15, "".join(w.text for w in words), words)], "en")
    assert len(cues) >= 3 and cues[0][2].startswith("word0 word1")


class FakeModel:
    def __init__(self, segs, lang="ja"):
        self.segs, self.lang, self.calls = segs, lang, 0

    def transcribe(self, path, **kw):
        self.calls += 1
        assert kw["vad_filter"] and kw["word_timestamps"]
        gen = (SimpleNamespace(start=s, end=e, text=t, words=[]) for s, e, t in self.segs)
        return gen, SimpleNamespace(language=self.lang, duration=10.0)


@pytest.fixture
def fake_audio(monkeypatch):
    def extract(video, wav):
        import wave
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(bytes(320))
        return wav
    monkeypatch.setattr(tr, "extract_audio", extract)


def test_transcribe_writes_srt_and_reports_progress(tmp_path, fake_audio):
    seen = []
    path, lang = tr.transcribe(tmp_path / "Show - 01.mkv", tmp_path / "cache",
                               progress=lambda s, f: seen.append((s, f)),
                               model=FakeModel([(1.0, 3.0, "お姉ちゃん"), (5.0, 10.0, "行くぞ")]))
    assert lang == "ja" and path.name == "Show - 01.whisper.srt"
    text = path.read_text(encoding="utf-8")
    assert "お姉ちゃん" in text and "00:00:05,000 --> 00:00:10,000" in text
    assert ("ถอดเสียง", 1.0) in seen
    assert not (tmp_path / "cache" / "Show - 01.16k.wav").exists()      # temp audio removed


def test_transcribe_with_no_speech_fails_clearly(tmp_path, fake_audio):
    with pytest.raises(TranscribeError, match="ไม่พบคำพูด"):
        tr.transcribe(tmp_path / "v.mkv", tmp_path, model=FakeModel([]))


def test_transcribe_can_be_stopped(tmp_path, fake_audio):
    from subtitle_translator.translate.pipeline import Stopped
    with pytest.raises(Stopped):
        tr.transcribe(tmp_path / "v.mkv", tmp_path, should_stop=lambda: True,
                      model=FakeModel([(1, 2, "a")]))


def test_prepare_project_transcribes_and_flags_episode(tmp_path, monkeypatch):
    from subtitle_translator.translate import pipeline
    video = tmp_path / "Show - 01.mkv"
    video.write_bytes(b"x")
    srt = tmp_path / "Show - 01.whisper.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(pipeline, "transcribe", lambda *a, **k: calls.append(a) or (srt, "en"))
    with pytest.raises(TranscribeError):
        pipeline.prepare_project(video, TRANSCRIBE, Settings(), tmp_path, allow_transcribe=False)
    assert calls == []
    project = pipeline.prepare_project(video, TRANSCRIBE, Settings(), tmp_path)
    ep = project.episode
    assert ep.transcribed and ep.src_lang == "en" and ep.cues[0].src == "Hello"

    from subtitle_translator.translate.analyze import build_analyze_request
    from subtitle_translator.translate.translate import build_translate_request
    assert "speech recognition" in build_analyze_request(ep, "high").context
    assert "speech recognition" in build_translate_request(ep, [ep.cues[0].id], "medium").context


def test_mux_adds_thai_track_without_touching_source(tmp_path):
    from subtitle_translator.export import mux_mkv
    src_ass = tmp_path / "jp.ass"
    src_ass.write_text("[Script Info]\nScriptType: v4.00+\n\n[Events]\n"
                       "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                       "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,やあ\n", encoding="utf-8")
    video = tmp_path / "v.mkv"
    subprocess.run([tool_path("ffmpeg"), "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=160x120:d=3",
                    "-i", str(src_ass), "-map", "0", "-map", "1", "-c:v", "libx264", "-c:s", "copy",
                    "-metadata:s:s:0", "language=jpn", str(video)], check=True)
    before = video.read_bytes()
    th = tmp_path / "v.th.ass"
    th.write_text(src_ass.read_text(encoding="utf-8").replace("やあ", "หวัดดี"), encoding="utf-8")
    out = mux_mkv(video, th, tmp_path / "v.th.mkv")
    assert video.read_bytes() == before
    tracks = list_sub_tracks(out)
    assert [t.language for t in tracks] == ["jpn", "tha"]
    with pytest.raises(ValueError):
        mux_mkv(video, th, video)


def test_split_prefers_pauses_and_avoids_tiny_tails():
    words = ([Word(0 + i * 0.3, 0.3 + i * 0.3, "あいう") for i in range(8)]              # 24 chars
             + [Word(3.0, 3.3, " かきく")]                                               # spaced phrase
             + [Word(3.3 + i * 0.3, 3.6 + i * 0.3, "さしす") for i in range(5)])          # 15 chars
    cues = split_segments([Segment(0, 4.8, "".join(w.text for w in words), words)], "ja")
    assert cues[0][2] == "あいう" * 8                                    # cut at the space, not mid-phrase
    tail = [Word(0, 1, "あ" * 20), Word(1, 2, "い" * 20), Word(2, 2.4, "うえ")]
    assert len(split_segments([Segment(0, 2.4, "x" * 42, tail)], "ja")) == 1   # 2-char tail stays attached
