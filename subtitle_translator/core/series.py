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
    return re.sub(r"\s{2,}", " ", trimmed or name.strip() or stem)


def episode_label(video: str | Path) -> str:
    stem = _TAGS.sub(" ", Path(video).stem).strip()
    m = re.search(r"(\d{1,4})(?:v\d)?(?:話)?\s*$", stem)
    return m.group(1) if m else stem


def memory_path(video: str | Path) -> Path:
    title = re.sub(r'[<>:"/\\|?*]', "_", series_title(video))
    return Path(video).parent / f"{title}{SUFFIX}"


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
                 "role": c.role, "relations": c.relations, "address_terms": c.address_terms}
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
                mem.characters = [Character(**c) for c in d.get("characters", [])]
                mem.glossary = dict(d.get("glossary", {}))
                mem.episodes = [EpisodeNote(**e) for e in d.get("episodes", [])]
            except (ValueError, TypeError, KeyError) as exc:
                log.warning("series memory data unreadable, starting fresh: %s", exc)
        n = _NOTES.search(text)
        if n:
            notes = n.group(1).strip()
            mem.notes = "" if notes == NOTES_HINT else notes
        return mem


def load_memory(video: str | Path) -> SeriesMemory:
    path = memory_path(video)
    if not path.exists():
        return SeriesMemory(title=series_title(video))
    try:
        return SeriesMemory.from_markdown(path.read_text(encoding="utf-8"), series_title(video))
    except (OSError, UnicodeDecodeError) as exc:
        log.warning("cannot read %s: %s", path, exc)
        return SeriesMemory(title=series_title(video))


def save_memory(video: str | Path, mem: SeriesMemory) -> Path | None:
    """Write atomically, re-reading the user's notes first so an edit made meanwhile survives."""
    path = memory_path(video)
    try:
        if path.exists():
            mem.notes = load_memory(video).notes
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(mem.to_markdown(), encoding="utf-8")
        os.replace(tmp, path)
        return path
    except OSError as exc:              # a read-only video folder must not fail the translation
        log.warning("cannot write series memory %s: %s", path, exc)
        return None
