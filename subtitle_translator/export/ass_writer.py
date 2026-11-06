from __future__ import annotations

import re
from pathlib import Path

import pysubs2

from ..core.models import Episode
from ..core.subio import SubtitleReadError, is_effect, load_ssa


def style_name(key: str) -> str:
    return "TH_" + re.sub(r"[^A-Za-z0-9_]", "_", key)


def _color(hex_color: str) -> pysubs2.Color:
    h = hex_color.lstrip("#")
    return pysubs2.Color(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 0)


def write_ass(ep: Episode, path: str | Path, font: str, size: int) -> Path:
    subs = pysubs2.SSAFile()
    res_x, res_y = 1920, 1080
    original = None
    if Path(ep.source).suffix.lower() in (".ass", ".ssa") and Path(ep.source).exists():
        try:
            original = load_ssa(ep.source)
            res_x = int(original.info.get("PlayResX", res_x))
            res_y = int(original.info.get("PlayResY", res_y))
        except (SubtitleReadError, ValueError):
            original = None
    subs.info["PlayResX"], subs.info["PlayResY"] = str(res_x), str(res_y)
    scale = res_y / 1080
    base = pysubs2.SSAStyle(
        fontname=font, fontsize=round(size * scale),
        primarycolor=pysubs2.Color(255, 255, 255, 0), outlinecolor=pysubs2.Color(0, 0, 0, 0),
        backcolor=pysubs2.Color(0, 0, 0, 128), outline=2.5 * scale, shadow=0.5 * scale,
        alignment=pysubs2.Alignment.BOTTOM_CENTER, marginv=round(48 * scale),
    )
    subs.styles["Default"] = base
    for ch in ep.characters:
        st = base.copy()
        st.primarycolor = _color(ch.color)
        subs.styles[style_name(ch.key)] = st
    keys = {ch.key for ch in ep.characters}
    for c in ep.cues:
        if c.skip:
            continue
        subs.events.append(pysubs2.SSAEvent(
            start=round(c.start * 1000), end=round(c.end * 1000),
            text=(c.thai or c.src).replace("\n", r"\N"),
            style=style_name(c.speaker) if c.speaker in keys else "Default",
        ))
    if original is not None:
        for ev in original.events:
            if not ev.is_comment and is_effect(ev):
                if ev.style in original.styles and ev.style not in subs.styles:
                    subs.styles[ev.style] = original.styles[ev.style]
                subs.events.append(ev.copy())
        subs.sort()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    subs.save(str(path), encoding="utf-8-sig")
    return path
