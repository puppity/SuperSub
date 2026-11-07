from __future__ import annotations

from pathlib import Path

from ..ai.backend import AIBackend, AIRequest, AIResult
from ..core.models import Character, Episode
from ..core.series import SeriesMemory
from .prompts import ANALYZE_SCHEMA, LANG_NAMES, SYSTEM_ANALYZE, format_cues

PALETTE = ["#FBBF24", "#F472B6", "#2DD4BF", "#60A5FA", "#A78BFA", "#FB923C", "#4ADE80", "#F87171"]
UNKNOWN_COLOR = "#A8A29E"


def build_analyze_request(ep: Episode, effort: str, memory: str = "") -> AIRequest:
    context = (f"Source language: {LANG_NAMES.get(ep.src_lang, ep.src_lang)}\n"
               + (memory + "\n\n" if memory else "")
               + "Columns: id, start time, duration, Name/Actor/style tag, text\n\n"
               "SUBTITLES:\n" + format_cues(ep.cues))
    return AIRequest(system=SYSTEM_ANALYZE, context=context,
                     task="Analyze the subtitles above and return the JSON.",
                     schema=ANALYZE_SCHEMA, effort=effort)


def apply_analysis(ep: Episode, data: dict, known: list[Character] | None = None) -> None:
    """Fill the episode from the analysis. Characters already known from earlier episodes
    keep their colour and Thai name, so a character looks the same across the series."""
    ep.summary = data["summary"]
    known_by_key = {c.key: c for c in known or []}
    used = {c.color for c in known_by_key.values()}
    free = [c for c in PALETTE if c not in used] or PALETTE
    ep.characters = []
    n_new = 0
    for c in data["characters"]:
        old = known_by_key.get(c["key"])
        if old is not None:
            color, name_th = old.color, old.name_th
        else:
            color, name_th = free[n_new % len(free)], c["name_th"]
            n_new += 1
        ep.characters.append(Character(
            key=c["key"], name_src=c["name_src"], name_th=name_th, gender=c["gender"],
            role=c["role"], relations=list(c["relations"]),
            address_terms={t["source"]: t["thai"] for t in c["address_terms"]},
            color=color,
        ))
    ep.glossary.update({t["source"]: t["thai"] for t in data.get("glossary", [])})
    keys = {c.key for c in ep.characters}
    guesses = {g["id"]: g for g in data["cues"]}
    for cue in ep.cues:
        g = guesses.get(cue.id)
        if cue.skip or g is None:
            continue
        cue.speaker = g["speaker"] if g["speaker"] in keys else None
        cue.addressee = g["addressee"] or None
        cue.speaker_conf = max(0.0, min(1.0, float(g["confidence"])))


def analyze(ep: Episode, backend: AIBackend, effort: str, memory: SeriesMemory | None = None) -> AIResult:
    block = memory.prompt_block(exclude_file=Path(ep.video_path).name) if memory else ""
    res = backend.run_json(build_analyze_request(ep, effort, block))
    apply_analysis(ep, res.data, memory.characters if memory else None)
    return res
