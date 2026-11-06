import pytest

from fakes import FakeBackend
from test_translate import echo
from subtitle_translator.ai.backend import AIError, AIErrorKind
from subtitle_translator.core.project_store import load_project
from subtitle_translator.core.settings import Settings
from subtitle_translator.translate.pipeline import (
    Stopped, estimate_project_cost, prepare_project, run_project,
)

SRT = "".join(f"{i}\n00:00:{i:02d},000 --> 00:00:{i:02d},900\nline {i}\n\n" for i in range(1, 26))
ANALYSIS = {"summary": "s", "characters": [], "cues": []}


@pytest.fixture
def video(tmp_path):
    v = tmp_path / "EP01.mkv"
    v.write_bytes(b"")
    (tmp_path / "EP01.srt").write_text(SRT, encoding="utf-8")
    return v


def test_prepare_from_sidecar_then_reuse(video, tmp_path):
    s = Settings(chunk_size=10)
    p = prepare_project(video, video.with_suffix(".srt"), s, tmp_path / "cache")
    assert len(p.episode.cues) == 25 and p.episode.src_lang == "en"
    assert p.chunk_size == 10 and p.stage == "extracted"
    p.cost_usd = 1.5
    from subtitle_translator.core.project_store import save_project
    save_project(p)
    again = prepare_project(video, video.with_suffix(".srt"), s, tmp_path / "cache")
    assert again.cost_usd == 1.5


def test_run_full_then_done(video, tmp_path):
    s = Settings(chunk_size=10)
    p = prepare_project(video, video.with_suffix(".srt"), s, tmp_path / "cache")
    seen = []
    backend = FakeBackend([ANALYSIS, echo, echo, echo])
    run_project(p, backend, s, lambda stage, f: seen.append((stage, f)), lambda: False)
    assert p.stage == "done" and p.done_chunks == [0, 1, 2]
    assert all(c.thai for c in p.episode.cues)
    assert p.cost_usd == pytest.approx(0.04)
    assert seen[-1] == ("แปล", 1.0)
    assert load_project(video).stage == "done"


def test_resume_after_stop_and_after_crash(video, tmp_path):
    s = Settings(chunk_size=10)
    p = prepare_project(video, video.with_suffix(".srt"), s, tmp_path / "cache")

    def stop_after_first_chunk():
        return len(p.done_chunks) >= 1

    with pytest.raises(Stopped):
        run_project(p, FakeBackend([ANALYSIS, echo]), s, lambda *a: None, stop_after_first_chunk)
    saved = load_project(video)
    assert saved.stage == "translating" and saved.done_chunks == [0]

    with pytest.raises(AIError):
        run_project(saved, FakeBackend([AIError(AIErrorKind.NETWORK, "down")]), s, lambda *a: None, lambda: False)
    saved = load_project(video)
    assert saved.done_chunks == [0]

    backend = FakeBackend([echo, echo])
    run_project(saved, backend, s, lambda *a: None, lambda: False)
    assert len(backend.requests) == 2 and saved.stage == "done"


def test_estimate(video, tmp_path):
    s = Settings(chunk_size=10)
    p = prepare_project(video, video.with_suffix(".srt"), s, tmp_path / "cache")
    assert estimate_project_cost(p, FakeBackend([])) == 0.5
