from __future__ import annotations

import logging
from pathlib import Path

from ..ai.backend import AIBackend, AIRequest, AIResult
from ..core.frames import MAX_FACES, Frame, faces_dir, save_face
from ..core.models import Character, Episode
from ..core.series import SeriesMemory, remember_series
from .prompts import ANALYZE_SCHEMA, SYSTEM_ANALYZE, VISION_NOTE, _mmss, format_cues, source_header, vision_schema

log = logging.getLogger(__name__)

PALETTE = ["#FBBF24", "#F472B6", "#2DD4BF", "#60A5FA", "#A78BFA", "#FB923C", "#4ADE80", "#F87171"]
UNKNOWN_COLOR = "#A8A29E"


def build_analyze_request(ep: Episode, effort: str, memory: str = "",
                          frames: list[Frame] | None = None,
                          faces: list[tuple[Character, Path]] | None = None) -> AIRequest:
    images: list[tuple[str, bytes]] = []
    for ch, path in faces or []:
        images.append((f'REFERENCE FACE of known character key "{ch.key}" ({ch.name_th}):', path.read_bytes()))
    for f in frames or []:
        images.append((f"FRAME at {_mmss(f.time)}, during line {f.cue_id}:", f.path.read_bytes()))
    context = (f"File name: {Path(ep.video_path).name}\n" + source_header(ep)
               + (VISION_NOTE + "\n" if images else "")
               + (memory + "\n\n" if memory else "")
               + "Columns: id, start time, duration, Name/Actor/style tag, text\n\n"
               "SUBTITLES:\n" + format_cues(ep.cues))
    task = ("Analyze the subtitles and the pictures and return the JSON." if images
            else "Analyze the subtitles above and return the JSON.")
    return AIRequest(system=SYSTEM_ANALYZE, context=context, task=task,
                     schema=vision_schema() if frames else ANALYZE_SCHEMA, effort=effort, images=images)


def known_faces(memory: SeriesMemory | None, video: str | Path,
                out_dir: str | Path | None) -> list[tuple[Character, Path]]:
    """Reference faces saved by earlier episodes that still exist on disk."""
    if memory is None:
        return []
    folder = faces_dir(video, out_dir)
    found = [(c, folder / c.face) for c in memory.characters if c.face]
    return [(c, p) for c, p in found if p.is_file()][:MAX_FACES]


def keep_faces(ep: Episode, data: dict, frames: list[Frame], out_dir: str | Path | None) -> None:
    """Save the frame the AI picked for each character without a face yet."""
    by_line = {f.cue_id: f for f in frames}
    picks = {c["key"]: c.get("face_line", 0) for c in data["characters"]}
    for ch in ep.characters:
        frame = by_line.get(picks.get(ch.key, 0))
        if ch.face or frame is None:
            continue
        try:
            ch.face = save_face(frame, ep.video_path, out_dir, ch.key)
        except OSError as exc:
            log.warning("cannot keep face for %s: %s", ch.key, exc)


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
            # The voice settled in earlier episodes wins, so a character sounds the same all series.
            self_p = old.self_pronoun or c.get("self_pronoun", "")
            you_p = old.you_pronoun or c.get("you_pronoun", "")
            face = old.face
        else:
            color, name_th = free[n_new % len(free)], c["name_th"]
            self_p, you_p, face = c.get("self_pronoun", ""), c.get("you_pronoun", ""), ""
            n_new += 1
        ep.characters.append(Character(
            key=c["key"], name_src=c["name_src"], name_th=name_th, gender=c["gender"],
            role=c["role"], relations=list(c["relations"]),
            address_terms={t["source"]: t["thai"] for t in c["address_terms"]},
            color=color, alignment=c.get("alignment", "unknown"),
            self_pronoun=self_p, you_pronoun=you_p, speech_style=c.get("speech_style", ""), face=face,
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


def analyze(ep: Episode, backend: AIBackend, effort: str, memory: SeriesMemory | None = None,
            frames: list[Frame] | None = None, out_dir: str | Path | None = None) -> AIResult:
    block = memory.prompt_block(exclude_file=Path(ep.video_path).name) if memory else ""
    faces = known_faces(memory, ep.video_path, out_dir) if frames else None
    res = backend.run_json(build_analyze_request(ep, effort, block, frames, faces))
    apply_analysis(ep, res.data, memory.characters if memory else None)
    # Name the series folder before anything is written into it (faces, memory, subtitles).
    title = remember_series(ep.video_path, res.data.get("series_title", ""), res.data.get("episode_number"))
    if memory is not None:
        memory.title = title
    if frames:
        keep_faces(ep, res.data, frames, out_dir)
    return res
