from __future__ import annotations

import json
import os
from pathlib import Path

from .models import Project


def project_path(video: str | Path) -> Path:
    p = Path(video)
    return p.with_name(p.stem + ".stproj.json")


def save_project(project: Project) -> Path:
    path = project_path(project.episode.video_path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(project.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_project(video: str | Path) -> Project | None:
    path = project_path(video)
    if not path.exists():
        return None
    return Project.from_dict(json.loads(path.read_text(encoding="utf-8")))
