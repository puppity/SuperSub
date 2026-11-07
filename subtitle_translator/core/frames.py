"""Frames from the video for the "read the picture" analysis mode.

A few dozen small JPEG frames, one per chosen subtitle line, let the AI see who is on screen,
their apparent age and gender, and whether a scene frames them as a hero or a villain. Face
images saved in the series folder let later episodes recognise the same characters.
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .media import MediaError, _run, tool_path
from .models import Cue
from .series import series_dir

log = logging.getLogger(__name__)

DEFAULT_FRAMES = 36
FRAME_WIDTH = 512
MAX_FACES = 12
MIN_CUE_S = 1.0           # very short lines are often cut-aways; prefer lines that stay on screen


@dataclass
class Frame:
    cue_id: int
    time: float
    path: Path


def pick_cues(cues: list[Cue], n: int = DEFAULT_FRAMES) -> list[Cue]:
    """Up to n lines spread evenly over the episode, preferring lines long enough to show a face."""
    spoken = [c for c in cues if not c.skip]
    if len(spoken) <= n:
        return spoken
    long_enough = [c for c in spoken if c.end - c.start >= MIN_CUE_S]
    pool = long_enough if len(long_enough) >= n else spoken
    step = len(pool) / n
    return [pool[int(i * step + step / 2)] for i in range(n)]


def _decoder_args(video: Path) -> list[str]:
    from .proxy import video_codec
    try:
        return ["-c:v", "libdav1d"] if video_codec(video) == "av1" else []   # libaom is very slow
    except Exception:
        return []


def capture(video: str | Path, cues: list[Cue], out_dir: str | Path,
            progress: Callable[[float], None] = lambda f: None,
            should_stop: Callable[[], bool] = lambda: False) -> list[Frame]:
    """One JPEG per cue, taken from the middle of the line. Lines whose frame fails are skipped."""
    from .proxy import ensure_proxy, needs_proxy
    src = Path(video)
    if needs_proxy(src):
        job = ensure_proxy(src, start=False)
        if job.ready:
            src = job.out                         # same timeline, far faster to seek
    pre = _decoder_args(src)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: list[Frame] = []
    for i, c in enumerate(cues):
        if should_stop():
            break
        t = (c.start + c.end) / 2
        out = out_dir / f"line{c.id:05d}.jpg"
        r = _run([tool_path("ffmpeg"), "-y", "-v", "error", "-ss", f"{t:.3f}", *pre, "-i", str(src),
                  "-frames:v", "1", "-vf", f"scale={FRAME_WIDTH}:-2", "-q:v", "5", str(out)], timeout=120)
        if r.returncode == 0 and out.exists():
            frames.append(Frame(c.id, t, out))
        else:
            log.warning("frame at %.1fs failed: %s", t, r.stderr.decode("utf-8", "replace")[-200:])
        progress((i + 1) / len(cues))
    if cues and not frames:
        raise MediaError("เก็บภาพจากวิดีโอไม่ได้เลย")
    return frames


def faces_dir(video: str | Path, out_dir: str | Path | None = None) -> Path:
    return series_dir(video, out_dir) / "faces"


def save_face(frame: Frame, video: str | Path, out_dir: str | Path | None, key: str) -> str:
    """Keep a frame as the reference face of a character. Returns the file name."""
    folder = faces_dir(video, out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{key}.jpg"
    shutil.copyfile(frame.path, folder / name)
    return name
