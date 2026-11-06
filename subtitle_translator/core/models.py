from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Cue:
    id: int
    start: float
    end: float
    src: str
    style: str | None = None
    actor: str | None = None
    src_index: int | None = None      # index into the original subtitle file's events
    skip: bool = False                # karaoke/sign/effect line: shown, never translated
    speaker: str | None = None        # Character.key
    addressee: str | None = None      # free text, e.g. "ยูกิ" or "ยูกิ, ฮารุ"
    speaker_conf: float = 0.0
    thai: str | None = None
    status: str = "pending"           # pending | translated | edited | failed


@dataclass
class Character:
    key: str
    name_src: str
    name_th: str
    gender: str = "unknown"           # male | female | unknown
    role: str = ""
    relations: list[str] = field(default_factory=list)
    address_terms: dict[str, str] = field(default_factory=dict)
    color: str = "#FFFFFF"


@dataclass
class Episode:
    video_path: str
    source: str                       # path of the extracted / side-car subtitle file
    src_lang: str                     # "ja" | "en"
    cues: list[Cue]
    characters: list[Character] = field(default_factory=list)
    summary: str = ""

    def cue(self, cue_id: int) -> Cue:
        for c in self.cues:
            if c.id == cue_id:
                return c
        raise KeyError(cue_id)

    def character(self, key: str | None) -> Character | None:
        for ch in self.characters:
            if ch.key == key:
                return ch
        return None


STAGES = ("extracted", "analyzed", "translating", "done")


@dataclass
class Project:
    episode: Episode
    backend: str                      # "api" | "claude_code"
    model: str
    stage: str = "extracted"
    chunk_size: int = 80
    done_chunks: list[int] = field(default_factory=list)
    cost_usd: float = 0.0
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Project:
        e = d["episode"]
        episode = Episode(
            video_path=e["video_path"],
            source=e["source"],
            src_lang=e["src_lang"],
            cues=[Cue(**c) for c in e["cues"]],
            characters=[Character(**c) for c in e["characters"]],
            summary=e.get("summary", ""),
        )
        return cls(
            episode=episode,
            backend=d["backend"],
            model=d["model"],
            stage=d["stage"],
            chunk_size=int(d.get("chunk_size", 80)),
            done_chunks=[int(x) for x in d["done_chunks"]],
            cost_usd=float(d["cost_usd"]),
            version=int(d.get("version", 1)),
        )
