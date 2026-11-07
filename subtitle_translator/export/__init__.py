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


def mux_path(video: str | Path, out_dir: str | None) -> Path:
    v = Path(video)
    return (Path(out_dir) if out_dir else v.parent) / f"{v.stem}.th.mkv"


def mux_mkv(video: str | Path, ass: str | Path, out: str | Path) -> Path:
    """Copy every stream of the video and add the Thai .ass as the default subtitle track.

    Nothing is re-encoded. The source video is only read; the result goes to a new file and is
    written to a temporary name first, so a failed run never leaves a half-written output.
    """
    from ..core.media import MediaError, _run, list_sub_tracks, tool_path
    out = Path(out)
    if _same_file(out, video):
        raise ValueError(f"ปลายทางตรงกับไฟล์วิดีโอต้นฉบับ จะไม่เขียนทับ: {out}")
    tmp = out.with_name(out.stem + ".part.mkv")
    th = len(list_sub_tracks(video))          # the Thai track comes after the existing subtitles
    res = _run([tool_path("ffmpeg"), "-y", "-v", "error", "-i", str(video), "-i", str(ass),
                "-map", "0", "-map", "1", "-c", "copy",
                "-disposition:s", "0", f"-disposition:s:{th}", "default",
                f"-metadata:s:s:{th}", "language=tha", f"-metadata:s:s:{th}", "title=ไทย",
                str(tmp)], timeout=3600)
    if res.returncode != 0 or not tmp.exists():
        try:
            tmp.unlink()
        except OSError:
            pass
        raise MediaError("แนบซับเข้าไฟล์วิดีโอไม่สำเร็จ: "
                         + res.stderr.decode("utf-8", "replace").strip()[-300:])
    os.replace(tmp, out)
    return out
