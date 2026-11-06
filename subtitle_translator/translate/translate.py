from __future__ import annotations

from ..ai.backend import AIBackend, AIError, AIErrorKind, AIRequest
from ..core.models import Cue, Episode
from .prompts import (
    LANG_NAMES, SYSTEM_TRANSLATE, TRANSLATE_MARKER, TRANSLATE_SCHEMA, format_cues, format_sheet,
)

MIN_CHUNK = 10
CONTEXT_TAIL = 20
_LOCAL_FAILURES = (AIErrorKind.REFUSAL, AIErrorKind.INVALID_OUTPUT, AIErrorKind.TRUNCATED)


def make_chunks(cues: list[Cue], size: int) -> list[list[int]]:
    ids = [c.id for c in cues if not c.skip]
    return [ids[i:i + size] for i in range(0, len(ids), size)]


def _names(ep: Episode) -> dict[str, str]:
    return {ch.key: ch.name_th for ch in ep.characters}


def build_translate_request(ep: Episode, ids: list[int], effort: str) -> AIRequest:
    names = _names(ep)
    context = (f"Source language: {LANG_NAMES.get(ep.src_lang, ep.src_lang)}\n"
               + format_sheet(ep)
               + "\n\nFULL SOURCE SUBTITLES (id, time, duration, speaker→listener, text):\n"
               + format_cues(ep.cues, names))
    first = ids[0]
    prev = [c for c in ep.cues if c.id < first and c.thai and not c.skip][-CONTEXT_TAIL:]
    prev_txt = "\n".join(f"{c.id}\t{c.thai}" for c in prev) or "(none)"
    wanted = [ep.cue(i) for i in ids]
    task = ("PREVIOUS THAI LINES (for continuity only, do not output them):\n" + prev_txt
            + "\n\n" + TRANSLATE_MARKER + "\n" + format_cues(wanted, names))
    return AIRequest(system=SYSTEM_TRANSLATE, context=context, task=task,
                     schema=TRANSLATE_SCHEMA, effort=effort)


def check_lines(expected: list[int], lines: list[dict]) -> tuple[dict[int, str], list[int]]:
    wanted = set(expected)
    got: dict[int, str] = {}
    for item in lines:
        cue_id = item.get("id")
        text = (item.get("thai") or "").strip()
        if cue_id in wanted and text and cue_id not in got:
            got[cue_id] = text
    return got, [i for i in expected if i not in got]


def _apply(ep: Episode, got: dict[int, str]) -> None:
    for cue_id, text in got.items():
        cue = ep.cue(cue_id)
        cue.thai = text
        cue.status = "translated"


def _mark_failed(ep: Episode, ids: list[int]) -> None:
    for cue_id in ids:
        cue = ep.cue(cue_id)
        if not cue.thai:
            cue.status = "failed"


def translate_chunk(ep: Episode, ids: list[int], backend: AIBackend, effort: str) -> float:
    try:
        res = backend.run_json(build_translate_request(ep, ids, effort))
    except AIError as exc:
        if exc.kind is AIErrorKind.TRUNCATED and len(ids) > MIN_CHUNK:
            mid = len(ids) // 2
            return (translate_chunk(ep, ids[:mid], backend, effort)
                    + translate_chunk(ep, ids[mid:], backend, effort))
        if exc.kind in _LOCAL_FAILURES:
            _mark_failed(ep, ids)
            return 0.0
        raise
    cost = res.cost_usd or 0.0
    got, missing = check_lines(ids, res.data["lines"])
    _apply(ep, got)
    if missing:
        try:
            res2 = backend.run_json(build_translate_request(ep, missing, effort))
            cost += res2.cost_usd or 0.0
            got2, missing = check_lines(missing, res2.data["lines"])
            _apply(ep, got2)
        except AIError as exc:
            if exc.kind not in _LOCAL_FAILURES:
                raise
        _mark_failed(ep, missing)
    return cost
