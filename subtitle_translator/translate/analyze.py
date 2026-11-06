from __future__ import annotations

from ..ai.backend import AIBackend, AIRequest, AIResult
from ..core.models import Character, Episode
from .prompts import ANALYZE_SCHEMA, LANG_NAMES, SYSTEM_ANALYZE, format_cues

PALETTE = ["#FBBF24", "#F472B6", "#2DD4BF", "#60A5FA", "#A78BFA", "#FB923C", "#4ADE80", "#F87171"]
UNKNOWN_COLOR = "#A8A29E"


def build_analyze_request(ep: Episode, effort: str) -> AIRequest:
    context = (f"Source language: {LANG_NAMES.get(ep.src_lang, ep.src_lang)}\n"
               "Columns: id, start time, duration, Name/Actor/style tag, text\n\n"
               "SUBTITLES:\n" + format_cues(ep.cues))
    return AIRequest(system=SYSTEM_ANALYZE, context=context,
                     task="Analyze the subtitles above and return the JSON.",
                     schema=ANALYZE_SCHEMA, effort=effort)


def apply_analysis(ep: Episode, data: dict) -> None:
    ep.summary = data["summary"]
    ep.characters = [
        Character(
            key=c["key"], name_src=c["name_src"], name_th=c["name_th"], gender=c["gender"],
            role=c["role"], relations=list(c["relations"]),
            address_terms={t["source"]: t["thai"] for t in c["address_terms"]},
            color=PALETTE[i % len(PALETTE)],
        )
        for i, c in enumerate(data["characters"])
    ]
    keys = {c.key for c in ep.characters}
    guesses = {g["id"]: g for g in data["cues"]}
    for cue in ep.cues:
        g = guesses.get(cue.id)
        if cue.skip or g is None:
            continue
        cue.speaker = g["speaker"] if g["speaker"] in keys else None
        cue.addressee = g["addressee"] or None
        cue.speaker_conf = max(0.0, min(1.0, float(g["confidence"])))


def analyze(ep: Episode, backend: AIBackend, effort: str) -> AIResult:
    res = backend.run_json(build_analyze_request(ep, effort))
    apply_analysis(ep, res.data)
    return res
