from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from ..ai.backend import AIBackend
from ..core.media import SubTrack, extract_sub, lang_of
from ..core.models import Episode, Project
from ..core.project_store import load_project, save_project
from ..core.settings import Settings
from ..core.subio import guess_lang, load_cues
from ..core.series import load_memory, save_memory
from .analyze import analyze, build_analyze_request
from .translate import build_translate_request, make_chunks, translate_chunk

log = logging.getLogger(__name__)
OUTPUT_TOKENS_PER_CUE = 90      # analysis + Thai text + adaptive thinking


class Stopped(Exception):
    pass


def prepare_project(video: str | Path, source: SubTrack | Path, settings: Settings, cache: Path) -> Project:
    existing = load_project(video)
    if existing is not None:
        return existing
    if isinstance(source, SubTrack):
        sub_path = extract_sub(video, source, cache)
        cues, _ = load_cues(sub_path)
        lang = lang_of(source) or guess_lang(cues)
    else:
        sub_path = Path(source)
        cues, _ = load_cues(sub_path)
        lang = guess_lang(cues)
    ep = Episode(video_path=str(video), source=str(sub_path), src_lang=lang, cues=cues)
    project = Project(episode=ep, backend=settings.backend, model=settings.model,
                      chunk_size=settings.chunk_size)
    save_project(project)
    return project


def run_project(project: Project, backend: AIBackend, settings: Settings,
                progress: Callable[[str, float], None], should_stop: Callable[[], bool],
                save: Callable[[Project], object] = save_project) -> Project:
    ep = project.episode
    project.backend, project.model = backend.name, backend.model
    if project.stage == "extracted":
        progress("วิเคราะห์ตัวละคร", 0.0)
        memory = load_memory(ep.video_path)          # what earlier episodes of this series taught us
        ep.glossary = dict(memory.glossary)
        ep.series_notes = memory.notes
        res = analyze(ep, backend, settings.effort_analyze, memory)
        project.cost_usd += res.cost_usd or 0.0
        project.stage = "analyzed"
        save(project)
        memory.merge_episode(ep)
        save_memory(ep.video_path, memory)
    if project.stage in ("analyzed", "translating"):
        project.stage = "translating"
        chunks = make_chunks(ep.cues, project.chunk_size)
        for n, ids in enumerate(chunks):
            if n in project.done_chunks:
                continue
            if should_stop():
                save(project)
                raise Stopped()
            progress("แปล", n / max(len(chunks), 1))
            project.cost_usd += translate_chunk(ep, ids, backend, settings.effort_translate)
            project.done_chunks.append(n)
            save(project)
        project.stage = "done"
        save(project)
        progress("แปล", 1.0)
    log.info("project %s done, cost %.4f", ep.video_path, project.cost_usd)
    return project


def estimate_project_cost(project: Project, backend: AIBackend) -> float | None:
    ep = project.episode
    reqs = [build_analyze_request(ep, "high")]
    reqs += [build_translate_request(ep, ids, "medium") for ids in make_chunks(ep.cues, project.chunk_size)]
    expected_out = OUTPUT_TOKENS_PER_CUE * sum(1 for c in ep.cues if not c.skip)
    return backend.estimate_cost(reqs, expected_out)
