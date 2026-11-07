"""Series memory: what earlier episodes taught us, kept as an editable Markdown file.

The file sits next to the videos as ``<series title>.series.md``. The human-readable
sections are regenerated from data on every save, except "โน้ตของผู้ใช้", which is the
user's own text and is preserved verbatim. The machine data lives in an HTML comment
at the end, so the file stays pleasant to read and edit in any Markdown viewer.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .models import Character, Episode

log = logging.getLogger(__name__)

SUFFIX = ".series.md"
NOTES_HEADING = "## โน้ตของผู้ใช้"
NOTES_HINT = "(เขียนอะไรก็ได้ตรงนี้ เช่น ชื่อที่อยากให้ใช้ คำศัพท์ หรือโทนการแปล — โปรแกรมจะส่งให้ AI ทุกตอน)"
_DATA = re.compile(r"<!--\s*series-data\s*(\{.*\})\s*-->", re.S)
_NOTES = re.compile(r"^## โน้ตของผู้ใช้[ \t]*\n(.*?)(?=^## |\Z|<!--\s*series-data)", re.S | re.M)
MAX_SUMMARIES_IN_PROMPT = 4
ALIGN_TH = {"hero": "ฝ่ายดี", "villain": "ฝ่ายร้าย", "neutral": "กลางๆ"}

# Episode markers at the end of a file name: "- 01", "EP01", "E01", "S01E01", "第01話", "#01", " 01v2".
_EPISODE = re.compile(
    r"(?:[\s._-]*(?:S\d{1,2})?(?:EP?|第|#)?\s*\d{1,4}(?:v\d)?(?:話)?)$", re.I)
_TAGS = re.compile(r"\[[^\]]*\]|\([^)]*\)|【[^】]*】")


def series_title(video: str | Path) -> str:
    """Best-effort series name from a video file name: tags and the episode number removed."""
    stem = Path(video).stem
    name = _TAGS.sub(" ", stem)
    name = re.sub(r"[\s._]+$", "", name)
    trimmed = _EPISODE.sub("", name).strip(" ._-")
    if not trimmed and Path(video).parent.name:
        return Path(video).parent.name     # "01.mp4": the folder names the series
    return re.sub(r"\s{2,}", " ", trimmed or name.strip() or stem)


def episode_label(video: str | Path) -> str:
    stem = _TAGS.sub(" ", Path(video).stem).strip()
    m = re.search(r"(\d{1,4})(?:v\d)?(?:話)?\s*$", stem)
    return m.group(1) if m else stem


def _safe_title(video: str | Path) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", series_title(video)).strip(" .") or "series"


def series_dir(video: str | Path, out_dir: str | Path | None = None) -> Path:
    """Folder that collects everything made for one series: subtitles, muxed videos, memory, faces.

    It sits in the output folder (or beside the video). When that folder is already named after
    the series, it is used as is instead of nesting a second folder with the same name.
    """
    base = Path(out_dir) if out_dir else Path(video).parent
    title = _safe_title(video)
    return base if base.name == title else base / title


def memory_path(video: str | Path, out_dir: str | Path | None = None) -> Path:
    return series_dir(video, out_dir) / f"{_safe_title(video)}{SUFFIX}"


def legacy_memory_path(video: str | Path) -> Path:
    """Where older versions kept the memory file: right beside the video."""
    return Path(video).parent / f"{_safe_title(video)}{SUFFIX}"


@dataclass
class EpisodeNote:
    label: str
    file: str
    summary: str


@dataclass
class SeriesMemory:
    title: str
    characters: list[Character] = field(default_factory=list)
    glossary: dict[str, str] = field(default_factory=dict)
    episodes: list[EpisodeNote] = field(default_factory=list)
    notes: str = ""

    # ----- prompt text -----
    def prompt_block(self, exclude_file: str | None = None) -> str:
        """Context for the AI: earlier episodes' cast, terms, story so far and the user's notes."""
        if not (self.characters or self.glossary or self.episodes or self.notes):
            return ""
        eps = [e for e in self.episodes if e.file != exclude_file][-MAX_SUMMARIES_IN_PROMPT:]
        data = {
            "series": self.title,
            "known_characters": [
                {"key": c.key, "name_src": c.name_src, "name_th": c.name_th, "gender": c.gender,
                 "role": c.role, "relations": c.relations, "address_terms": c.address_terms,
                 "alignment": c.alignment, "self_pronoun": c.self_pronoun, "you_pronoun": c.you_pronoun,
                 "speech_style": c.speech_style}
                for c in self.characters
            ],
            "glossary": self.glossary,
            "story_so_far": [{"episode": e.label, "summary": e.summary} for e in eps],
        }
        out = "SERIES MEMORY FROM EARLIER EPISODES (stay consistent with it):\n" + json.dumps(
            data, ensure_ascii=False, indent=1)
        if self.notes:
            out += "\n\nTRANSLATOR NOTES FROM THE USER (follow them):\n" + self.notes
        return out

    # ----- merge -----
    def merge_episode(self, ep: Episode) -> None:
        by_key = {c.key: c for c in self.characters}
        for ch in ep.characters:
            known = by_key.get(ch.key)
            if known is None:
                self.characters.append(Character(**asdict(ch)))
                by_key[ch.key] = self.characters[-1]
                continue
            # Keep the established Thai name and colour; learn new facts.
            known.name_src = known.name_src or ch.name_src
            if known.gender == "unknown":
                known.gender = ch.gender
            known.role = ch.role or known.role
            # Established pronouns stay (the same voice every episode); fill only what is missing.
            for attr in ("self_pronoun", "you_pronoun", "speech_style", "face"):
                if not getattr(known, attr):
                    setattr(known, attr, getattr(ch, attr))
            if ch.alignment != "unknown":
                known.alignment = ch.alignment       # a turn (hero to villain) is a real story event
            for r in ch.relations:
                if r not in known.relations:
                    known.relations.append(r)
            for src, th in ch.address_terms.items():
                known.address_terms.setdefault(src, th)
        for src, th in ep.glossary.items():
            self.glossary.setdefault(src, th)
        file = Path(ep.video_path).name
        note = EpisodeNote(label=episode_label(ep.video_path), file=file, summary=ep.summary)
        self.episodes = [e for e in self.episodes if e.file != file] + [note]
        self.episodes.sort(key=lambda e: (len(e.label), e.label))

    # ----- Markdown -----
    def to_markdown(self) -> str:
        lines = [f"# {self.title} — บันทึกเรื่อง", "",
                 "> โปรแกรมแปลซับสร้างและอัปเดตไฟล์นี้ทุกครั้งที่วิเคราะห์ตอนใหม่ "
                 "แก้ได้เฉพาะส่วน “โน้ตของผู้ใช้” ส่วนอื่นจะถูกเขียนทับ",
                 "", NOTES_HEADING, "", self.notes or NOTES_HINT, "", "## ตัวละคร", ""]
        for c in self.characters:
            bits = [f"**{c.name_th}** ({c.name_src}, `{c.key}`)", c.gender]
            if c.role:
                bits.append(c.role)
            if c.alignment in ALIGN_TH:
                bits.append(ALIGN_TH[c.alignment])
            if c.self_pronoun or c.you_pronoun:
                bits.append(f"แทนตัว {c.self_pronoun or '-'} · เรียกคนอื่น {c.you_pronoun or '-'}")
            if c.speech_style:
                bits.append(c.speech_style)
            if c.relations:
                bits.append("ความสัมพันธ์: " + ", ".join(c.relations))
            if c.address_terms:
                bits.append("คำเรียก: " + ", ".join(f"{k} → {v}" for k, v in c.address_terms.items()))
            lines.append("- " + " · ".join(bits))
        if not self.characters:
            lines.append("(ยังไม่มี)")
        lines += ["", "## คำศัพท์เฉพาะ", ""]
        lines += [f"- {k} → {v}" for k, v in self.glossary.items()] or ["(ยังไม่มี)"]
        lines += ["", "## เรื่องย่อแต่ละตอน", ""]
        for e in self.episodes:
            lines += [f"### ตอน {e.label}", "", e.summary or "-", ""]
        if not self.episodes:
            lines += ["(ยังไม่มี)", ""]
        data = {"version": 1, "title": self.title,
                "characters": [asdict(c) for c in self.characters],
                "glossary": self.glossary,
                "episodes": [asdict(e) for e in self.episodes]}
        lines += ["<!-- series-data", json.dumps(data, ensure_ascii=False, indent=1).replace("--", "\\u002d\\u002d"),
                  "-->", ""]
        return "\n".join(lines)

    @classmethod
    def from_markdown(cls, text: str, title: str) -> SeriesMemory:
        mem = cls(title=title)
        m = _DATA.search(text)
        if m:
            try:
                d = json.loads(m.group(1))
                mem.title = d.get("title") or title
                mem.characters = [Character.from_dict(c) for c in d.get("characters", [])]
                mem.glossary = dict(d.get("glossary", {}))
                mem.episodes = [EpisodeNote(**e) for e in d.get("episodes", [])]
            except (ValueError, TypeError, KeyError) as exc:
                log.warning("series memory data unreadable, starting fresh: %s", exc)
        n = _NOTES.search(text)
        if n:
            notes = n.group(1).strip()
            mem.notes = "" if notes == NOTES_HINT else notes
        return mem


def load_memory(video: str | Path, out_dir: str | Path | None = None) -> SeriesMemory:
    path = memory_path(video, out_dir)
    if not path.exists():
        path = legacy_memory_path(video)          # carried over; the new file is written next save
    if not path.exists():
        return SeriesMemory(title=series_title(video))
    try:
        return SeriesMemory.from_markdown(path.read_text(encoding="utf-8"), series_title(video))
    except (OSError, UnicodeDecodeError) as exc:
        log.warning("cannot read %s: %s", path, exc)
        return SeriesMemory(title=series_title(video))


def save_memory(video: str | Path, mem: SeriesMemory, out_dir: str | Path | None = None) -> Path | None:
    """Write atomically, re-reading the user's notes first so an edit made meanwhile survives."""
    path = memory_path(video, out_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() or legacy_memory_path(video).exists():
            mem.notes = load_memory(video, out_dir).notes
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(mem.to_markdown(), encoding="utf-8")
        os.replace(tmp, path)
        return path
    except OSError as exc:              # a read-only video folder must not fail the translation
        log.warning("cannot write series memory %s: %s", path, exc)
        return None
