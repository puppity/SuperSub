from __future__ import annotations

import os
from pathlib import Path

from ..core.models import Project
from ..core.settings import Settings
from .ass_writer import write_ass
from .srt_writer import write_srt


def output_paths(video: str | Path, out_dir: str | None) -> tuple[Path, Path]:
    v = Path(video)
    folder = Path(out_dir) if out_dir else v.parent
    return folder / f"{v.stem}.th.srt", folder / f"{v.stem}.th.ass"


def _same_file(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))


def export_project(project: Project, settings: Settings, overwrite: bool = False) -> tuple[Path, Path]:
    ep = project.episode
    srt, ass = output_paths(ep.video_path, settings.output_dir)
    for p in (srt, ass):
        if _same_file(p, ep.source):
            raise ValueError(f"ปลายทางตรงกับไฟล์ซับต้นฉบับ จะไม่เขียนทับ: {p}")
    if not overwrite:
        for p in (srt, ass):
            if p.exists():
                raise FileExistsError(str(p))
    colors = {ch.key: ch.color for ch in ep.characters} if settings.srt_colors else None
    write_srt(ep.cues, srt, colors)
    write_ass(ep, ass, settings.ass_font, settings.ass_size)
    return srt, ass
