from __future__ import annotations

from pathlib import Path

from ..core.models import Cue


def srt_timestamp(t: float) -> str:
    ms = int(round(t * 1000))
    return "%02d:%02d:%02d,%03d" % (ms // 3_600_000, ms // 60_000 % 60, ms // 1000 % 60, ms % 1000)


def write_srt(cues: list[Cue], path: str | Path, colors: dict[str, str] | None = None) -> Path:
    blocks = []
    for c in cues:
        if c.skip:
            continue
        text = c.thai or c.src
        if colors and c.speaker in colors:
            text = f'<font color="{colors[c.speaker]}">{text}</font>'
        blocks.append(f"{len(blocks) + 1}\n{srt_timestamp(c.start)} --> {srt_timestamp(c.end)}\n{text}\n")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(blocks), encoding="utf-8-sig", newline="\n")
    return path
