from __future__ import annotations

import re
from pathlib import Path

import pysubs2

from .models import Cue


class SubtitleReadError(Exception):
    pass


_EFFECT_TAG = re.compile(r"\\(?:[kK][fo]?\d|p[1-9])")
_EFFECT_STYLE = re.compile(r"(?:^|[\s_\-])(op|ed|opening|ending|karaoke|kara|sign|song|title|insert)(?:[\s_\-\d]|$)", re.I)
_JA_CHARS = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")


def read_text_any(path: str | Path) -> str:
    data = Path(path).read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    for enc in ("utf-8-sig", "cp932", "cp874"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise SubtitleReadError(f"อ่านไฟล์ซับไม่ได้ (encoding ไม่รู้จัก): {path}")


def load_ssa(path: str | Path) -> pysubs2.SSAFile:
    text = read_text_any(path)
    if not text.strip():
        raise SubtitleReadError(f"ไฟล์ซับว่างเปล่า: {path}")
    try:
        return pysubs2.SSAFile.from_string(text)
    except Exception as exc:  # pysubs2 raises several format errors
        raise SubtitleReadError(f"อ่านรูปแบบซับไม่ได้: {path} ({exc})") from exc


def is_effect(event: pysubs2.SSAEvent) -> bool:
    return bool(_EFFECT_TAG.search(event.text) or _EFFECT_STYLE.search(event.style or ""))


def load_cues(path: str | Path) -> tuple[list[Cue], pysubs2.SSAFile]:
    subs = load_ssa(path)
    cues: list[Cue] = []
    for index, ev in enumerate(subs.events):
        if ev.is_comment:
            continue
        plain = ev.plaintext.strip()
        if not plain:
            continue
        cues.append(Cue(
            id=len(cues) + 1,
            start=ev.start / 1000.0,
            end=ev.end / 1000.0,
            src=plain,
            style=ev.style or None,
            actor=ev.name or None,
            src_index=index,
            skip=is_effect(ev),
        ))
    if not cues:
        raise SubtitleReadError(f"ไม่พบบรรทัดซับในไฟล์: {path}")
    return cues, subs


def guess_lang(cues: list[Cue]) -> str:
    text = "".join(c.src for c in cues if not c.skip)
    if not text:
        return "en"
    return "ja" if len(_JA_CHARS.findall(text)) / len(text) > 0.2 else "en"
