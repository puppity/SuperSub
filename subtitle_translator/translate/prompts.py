from __future__ import annotations

import json

from ..core.models import Cue, Episode

LANG_NAMES = {"ja": "Japanese", "en": "English"}
TRANSLATE_MARKER = "TRANSLATE THESE LINES:"

SYSTEM_ANALYZE = """You analyze subtitles of one episode of a film, drama or anime before it is translated into Thai.
Read every line. Work out:
1. The characters who speak. Give each a short stable ASCII key (e.g. "yuki"), the name as written in the source, a Thai transliteration of the name, gender (male/female/unknown), a short Thai role description (age group, job, family position), and relations to other characters in Thai.
2. For each character, the Thai address terms other characters should use for them, derived from the source (e.g. お姉ちゃん -> พี่, 先生 -> อาจารย์, "sis" -> พี่).
3. For every line id: the most likely speaker key ("unknown" if you cannot tell), who they are talking to (Thai name, comma-separated, or "ทุกคน"/"ตัวเอง"), and your confidence from 0 to 1.
4. A Thai summary of the episode in at most 5 sentences.
5. A glossary of recurring proper nouns and special terms (machines, places, techniques, teams) with one fixed Thai rendering each.
If SERIES MEMORY from earlier episodes is given: reuse the same key, Thai name and address terms for every character already listed, keep glossary renderings, and only add characters or terms that are new.
Use evidence such as names and honorifics, gendered speech (〜わ, 〜ぜ, 僕, 俺, あたし), who answers whom, and the Name/Actor or style tag shown in the third column when present.
Never invent characters who do not speak. Line ids are integers and must be copied exactly."""

SYSTEM_TRANSLATE = """You are a professional Thai subtitle translator for films, dramas and anime.
Translate the requested lines into natural spoken Thai that fits each speaker and listener.
Rules:
- Use the character sheet. Keep names, address terms and pronouns consistent for the whole episode.
- Choose pronouns and sentence-final particles (ครับ/ค่ะ/นะ/จ้ะ/เว้ย...) from the speaker's gender, age and politeness toward the listener.
- Japanese honorifics (-san, -chan, -kun, -senpai, -sensei, お姉ちゃん, お兄ちゃん) become natural Thai equivalents (คุณ, พี่, น้อง, รุ่นพี่, อาจารย์); never transliterate them.
- Transliterate personal names the same way every time, as in the character sheet.
- Keep each line short enough to read: aim for at most 17 Thai characters per second of the line's duration and at most 2 lines; use a newline for a 2-line subtitle.
- Do not add or remove meaning. Translate jokes and wordplay by meaning.
- Return one entry for every requested id, copying ids exactly. Never output lines you were not asked for."""

ANALYZE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "characters", "glossary", "cues"],
    "properties": {
        "summary": {"type": "string"},
        "glossary": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source", "thai"],
                "properties": {"source": {"type": "string"}, "thai": {"type": "string"}},
            },
        },
        "characters": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["key", "name_src", "name_th", "gender", "role", "relations", "address_terms"],
                "properties": {
                    "key": {"type": "string"},
                    "name_src": {"type": "string"},
                    "name_th": {"type": "string"},
                    "gender": {"type": "string", "enum": ["male", "female", "unknown"]},
                    "role": {"type": "string"},
                    "relations": {"type": "array", "items": {"type": "string"}},
                    "address_terms": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["source", "thai"],
                            "properties": {"source": {"type": "string"}, "thai": {"type": "string"}},
                        },
                    },
                },
            },
        },
        "cues": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "speaker", "addressee", "confidence"],
                "properties": {
                    "id": {"type": "integer"},
                    "speaker": {"type": "string"},
                    "addressee": {"type": "string"},
                    "confidence": {"type": "number"},
                },
            },
        },
    },
}

TRANSLATE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["lines"],
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "thai"],
                "properties": {"id": {"type": "integer"}, "thai": {"type": "string"}},
            },
        }
    },
}


def _mmss(t: float) -> str:
    tenths = round(t * 10)
    m, rest = divmod(tenths, 600)
    return f"{m:02d}:{rest / 10:04.1f}"


def format_cues(cues: list[Cue], names: dict[str, str] | None = None) -> str:
    out = []
    for c in cues:
        if c.skip:
            continue
        if names is not None:
            tag = f"{names.get(c.speaker or '') or '?'}→{c.addressee or '?'}"
        else:
            tag = "/".join(x for x in (c.actor, c.style) if x) or "-"
        text = c.src.replace("\n", " / ")
        dur = f"{c.end - c.start:.1f}s"
        out.append(f"{c.id}\t{_mmss(c.start)}\t{dur}\t{tag}\t{text}")
    return "\n".join(out)


def format_sheet(ep: Episode) -> str:
    sheet = {
        "summary": ep.summary,
        "glossary": ep.glossary,
        "characters": [
            {"key": ch.key, "name_src": ch.name_src, "name_th": ch.name_th, "gender": ch.gender,
             "role": ch.role, "relations": ch.relations, "address_terms": ch.address_terms}
            for ch in ep.characters
        ],
    }
    out = "CHARACTER SHEET:\n" + json.dumps(sheet, ensure_ascii=False, indent=1)
    if ep.series_notes:
        out += "\n\nTRANSLATOR NOTES FROM THE USER (follow them):\n" + ep.series_notes
    return out
