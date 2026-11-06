import pytest

from fakes import FakeBackend
from helpers import make_episode
from subtitle_translator.ai.backend import AIError, AIErrorKind
from subtitle_translator.translate.prompts import TRANSLATE_MARKER
from subtitle_translator.translate.translate import (
    build_translate_request, check_lines, make_chunks, translate_chunk,
)


def ids_in(req):
    block = req.task.split(TRANSLATE_MARKER, 1)[1].strip()
    return [int(line.split("\t")[0]) for line in block.splitlines() if line.strip()]


def echo(req):
    return {"lines": [{"id": i, "thai": f"ไทย {i}"} for i in ids_in(req)]}


def test_make_chunks_skips_effects():
    ep = make_episode(7)
    ep.cues[2].skip = True
    assert make_chunks(ep.cues, 3) == [[1, 2, 4], [5, 6, 7]]


def test_request_has_stable_context_and_previous_lines():
    ep = make_episode(4)
    ep.cues[0].thai = "ก่อนหน้า"
    req = build_translate_request(ep, [2, 3], "medium")
    assert "line 4" in req.context and "CHARACTER SHEET" in req.context
    assert "(id, time, duration, speaker→listener, text)" in req.context
    assert "4\t00:08.0\t1.5s\t" in req.context
    assert "1\tก่อนหน้า" in req.task
    assert ids_in(req) == [2, 3]
    assert build_translate_request(ep, [4], "medium").context == req.context


def test_check_lines():
    got, missing = check_lines([1, 2, 3], [{"id": 1, "thai": " ก "}, {"id": 1, "thai": "ซ้ำ"},
                                           {"id": 9, "thai": "เกิน"}, {"id": 2, "thai": "  "}])
    assert got == {1: "ก"} and missing == [2, 3]


def test_translate_chunk_happy_path():
    ep = make_episode(3)
    cost = translate_chunk(ep, [1, 2, 3], FakeBackend([echo]), "medium")
    assert [c.thai for c in ep.cues] == ["ไทย 1", "ไทย 2", "ไทย 3"]
    assert {c.status for c in ep.cues} == {"translated"}
    assert cost == pytest.approx(0.01)


def test_translate_chunk_retries_missing_once_then_fails():
    ep = make_episode(3)
    backend = FakeBackend([{"lines": [{"id": 1, "thai": "หนึ่ง"}]},
                           {"lines": [{"id": 2, "thai": "สอง"}]}])
    translate_chunk(ep, [1, 2, 3], backend, "medium")
    assert ids_in(backend.requests[1]) == [2, 3]
    assert [c.status for c in ep.cues] == ["translated", "translated", "failed"]


def test_translate_chunk_splits_when_truncated():
    ep = make_episode(24)
    ids = [c.id for c in ep.cues]
    backend = FakeBackend([AIError(AIErrorKind.TRUNCATED, "cut"), echo, echo])
    translate_chunk(ep, ids, backend, "medium")
    assert [len(ids_in(r)) for r in backend.requests] == [24, 12, 12]
    assert all(c.thai for c in ep.cues)


def test_translate_chunk_refusal_marks_failed():
    ep = make_episode(2)
    translate_chunk(ep, [1, 2], FakeBackend([AIError(AIErrorKind.REFUSAL, "no")]), "medium")
    assert [c.status for c in ep.cues] == ["failed", "failed"]


def test_translate_chunk_reraises_auth():
    ep = make_episode(2)
    with pytest.raises(AIError):
        translate_chunk(ep, [1, 2], FakeBackend([AIError(AIErrorKind.AUTH, "bad key")]), "medium")
