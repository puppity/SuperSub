from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .models import Project


class ProjectFileError(ValueError):
    """The .stproj.json file is unreadable. ValueError so existing (ValueError, OSError) handlers still catch it."""


def project_path(video: str | Path) -> Path:
    p = Path(video)
    return p.with_name(p.stem + ".stproj.json")


def save_project(project: Project) -> Path:
    path = project_path(project.episode.video_path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(project.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        for attempt in range(3):         # antivirus / indexer can briefly lock the target on Windows
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 2:
                    raise
                time.sleep(0.1)
    finally:
        try:
            tmp.unlink(missing_ok=True)  # no-op after a successful replace
        except OSError:
            pass
    return path


def load_project(video: str | Path) -> Project | None:
    path = project_path(video)
    if not path.exists():
        return None
    try:
        return Project.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, KeyError, TypeError) as exc:      # JSONDecodeError and UnicodeDecodeError are ValueErrors
        raise ProjectFileError(f"ไฟล์งาน {path.name} เสีย — ลบไฟล์นี้แล้วเริ่มใหม่") from exc
