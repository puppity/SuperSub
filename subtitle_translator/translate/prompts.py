from __future__ import annotations

import copy
import json

from ..core.models import Cue, Episode

LANG_NAMES = {"ja": "Japanese", "en": "English"}
ASR_NOTE = ("NOTE: these lines come from automatic speech recognition, not a subtitle file. "
            "There are no speaker names: infer speakers from content and turn-taking. "
            "Some words may be misheard; when a line is clearly a mishearing, use the meaning "
            "the context makes obvious.\n")
TRANSLATE_MARKER = "TRANSLATE THESE LINES:"


def source_header(ep) -> str:
    return (f"Source language: {LANG_NAMES.get(ep.src_lang, ep.src_lang)}\n"
            + (ASR_NOTE if getattr(ep, "transcribed", False) else ""))

SYSTEM_ANALYZE = """You analyze subtitles of one episode of a film, drama or anime before it is translated into Thai.
Read every line. Work out:
1. The characters who speak. Give each a short stable ASCII key (e.g. "yuki"), the name as written in the source, a Thai transliteration of the name, gender (male/female/unknown), a short Thai role description (age group, job, family position), and relations to other characters in Thai.
2. For each character, the Thai address terms other characters should use for them, derived from the source (e.g. お姉ちゃん -> พี่, 先生 -> อาจารย์, "sis" -> พี่).
3. For every line id: the most likely speaker key ("unknown" if you cannot tell), who they are talking to (Thai name, comma-separated, or "ทุกคน"/"ตัวเอง"), and your confidence from 0 to 1.
4. A Thai summary of the episode in at most 5 sentences.
5. A glossary of recurring proper nouns and special terms (machines, places, techniques, teams) with one fixed Thai rendering each.
6. For each character: their side in the story (hero, villain, neutral, or unknown) and the Thai voice that fits them. Give the word they use for "I" (e.g. ผม, ฉัน, หนู, เรา, ข้า, กู, ข้าพเจ้า), their usual word for "you" (e.g. คุณ, นาย, เธอ, แก, เจ้า, มึง, ท่าน), and a short Thai note on their register and how it changes with the listener. Base it on the source: 俺/貴様/てめえ, arrogant or threatening speech and villain roles suit ข้า/แก or กู/มึง; polite heroes suit ผม/ฉัน with คุณ/นาย/เธอ; elders and royalty suit ข้า/เจ้า; children often use หนู. Keep it natural for Thai subtitles.
7. The series: series_title is the title the show is best known by (its official English or romaji title, as streaming sites list it), without episode numbers, season words or release-group tags; take it from the file name and the content. episode_number is the episode number if the file name or the subtitles show it, else 0.
If SERIES MEMORY from earlier episodes is given, copy its "series" value as series_title exactly.
If SERIES MEMORY from earlier episodes is given: reuse the same key, Thai name and address terms for every character already listed, keep glossary renderings, and only add characters or terms that are new.
Use evidence such as names and honorifics, gendered speech (〜わ, 〜ぜ, 僕, 俺, あたし), who answers whom, and the Name/Actor or style tag shown in the third column when present.
Never invent characters who do not speak. Line ids are integers and must be copied exactly."""

SYSTEM_TRANSLATE = """You are a professional Thai subtitle translator for films, dramas and anime.
Translate the requested lines into natural spoken Thai that fits each speaker and listener.
Rules:
- Use the character sheet. Keep names, address terms and pronouns consistent for the whole episode.
- Each character has a Thai voice in the sheet: self_pronoun is their "I", you_pronoun their usual "you", speech_style how it shifts with the listener. Use it every time, so a villain keeps sounding like a villain (e.g. ข้า/แก) and a polite hero keeps ผม/คุณ. Change register only where the scene clearly calls for it (talking to a superior, losing their temper).
- Choose sentence-final particles (ครับ/ค่ะ/นะ/จ้ะ/เว้ย...) from the speaker's gender, age and politeness toward the listener.
- Japanese honorifics (-san, -chan, -kun, -senpai, -sensei, お姉ちゃん, お兄ちゃん) become natural Thai equivalents (คุณ, พี่, น้อง, รุ่นพี่, อาจารย์); never transliterate them.
- Transliterate personal names the same way every time, as in the character sheet.
- Keep each line short enough to read: aim for at most 17 Thai characters per second of the line's duration and at most 2 lines; use a newline for a 2-line subtitle.
- Do not add or remove meaning. Translate jokes and wordplay by meaning.
- Return one entry for every requested id, copying ids exactly. Never output lines you were not asked for."""

ANALYZE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["series_title", "episode_number", "summary", "characters", "glossary", "cues"],
    "properties": {
        "series_title": {"type": "string"},
        "episode_number": {"type": "integer"},
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
                "required": ["key", "name_src", "name_th", "gender", "role", "relations", "address_terms",
                             "alignment", "self_pronoun", "you_pronoun", "speech_style"],
                "properties": {
                    "key": {"type": "string"},
                    "name_src": {"type": "string"},
                    "name_th": {"type": "string"},
                    "gender": {"type": "string", "enum": ["male", "female", "unknown"]},
                    "role": {"type": "string"},
                    "alignment": {"type": "string", "enum": ["hero", "villain", "neutral", "unknown"]},
                    "self_pronoun": {"type": "string"},
                    "you_pronoun": {"type": "string"},
                    "speech_style": {"type": "string"},
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
             "role": ch.role, "relations": ch.relations, "address_terms": ch.address_terms,
             "alignment": ch.alignment, "self_pronoun": ch.self_pronoun, "you_pronoun": ch.you_pronoun,
             "speech_style": ch.speech_style}
            for ch in ep.characters
        ],
    }
    out = "CHARACTER SHEET:\n" + json.dumps(sheet, ensure_ascii=False, indent=1)
    if ep.series_notes:
        out += "\n\nTRANSLATOR NOTES FROM THE USER (follow them):\n" + ep.series_notes
    return out


VISION_NOTE = """PICTURES: frames from the video are attached, each labelled with its time and the line id spoken at that moment.
REFERENCE FACE pictures, when present, show characters already known from earlier episodes; use them to recognise those characters and keep their keys.
Use the pictures to see who is on screen while a line is spoken, their apparent gender and age, clothing and expressions, and whether the scene frames them as a hero or a villain (menacing staging, dark lighting, henchmen, a protagonist's point of view). Let this sharpen speakers, gender, alignment and the Thai voice; the subtitles still decide what is said.
For every character, set face_line to the line id of the frame that shows their face most clearly (alone, facing the camera if possible), or 0 if no frame shows it clearly.
"""


def vision_schema() -> dict:
    """The analysis schema plus face_line per character, for the picture-reading mode."""
    schema = copy.deepcopy(ANALYZE_SCHEMA)
    char = schema["properties"]["characters"]["items"]
    char["properties"]["face_line"] = {"type": "integer"}
    char["required"] = char["required"] + ["face_line"]
    return schema
