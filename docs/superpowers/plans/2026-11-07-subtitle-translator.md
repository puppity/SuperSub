# Subtitle Translator (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Windows desktop app that pulls a text subtitle track out of an MKV/MP4 (or takes a side-car .srt/.ass), has Claude work out who is speaking to whom, translates the whole episode into context-aware Thai, lets the user review/edit it, and exports `<video>.th.srt` + `<video>.th.ass`.

**Architecture:** Pure-Python core (`core`, `ai`, `translate`, `export`) with no GUI imports, driven by a PySide6 GUI that runs every slow job in a `QThread`. AI access goes through one small interface (`AIBackend.run_json`) with two implementations: the Anthropic API (API key) and the local Claude Code CLI (Claude Max subscription). Progress is checkpointed into `<video>.stproj.json` after each stage and each translated chunk, so work resumes without paying twice.

**Tech Stack:** Python 3.12, PySide6 (Widgets + Multimedia), anthropic (official SDK), pysubs2, jsonschema, keyring, platformdirs, ffmpeg/ffprobe, pytest + pytest-qt, PyInstaller.

**Spec:** `docs/superpowers/specs/2026-11-07-subtitle-translator-design.md` — read it before starting any task.

## Global Constraints

- Target OS: Windows 10/11 x64. Python `>=3.12`.
- Package name: `subtitle_translator`. Modules in `core/`, `ai/`, `translate/`, `export/` must never import from `gui/`.
- Never modify or delete the source video or source subtitle files. Outputs are only `<stem>.th.srt`, `<stem>.th.ass`, `<stem>.stproj.json` (beside the video) and files in the app cache/config/log dirs.
- `.th.srt` and `.th.ass` are written as UTF-8 with BOM (`utf-8-sig`).
- API key is stored only via `keyring` (service `SubTranslator`, user `anthropic_api_key`). Never write it to settings JSON or logs.
- Model IDs (exact): `claude-opus-5-5` (default, recommended), `claude-sonnet-5-5`, `claude-haiku-5-5`. Prices per 1M tokens input/output: Opus 5.5 $4/$20 (cache read $0.20), Sonnet 5.5 $2/$10 (cache read $0.20), Haiku 5.5 $0.10/$0.50.
- API calls: `thinking={"type": "adaptive"}`, `output_config.effort` (analysis `high`, translation `medium` by default), structured output via `output_config.format` JSON schema, streaming + `get_final_message()`, `max_tokens=64000`. Server-side refusal fallback (`betas=["server-side-fallback-2026-07-01"]`, `fallbacks="default"`) only for Opus 5.5 and Sonnet 5.5, never Haiku 5.5. Never send `temperature`/`top_p`/`top_k`, `budget_tokens`, or assistant prefill.
- Translation chunk size default 80 cues (allowed 40–150); minimum split size 10; continuity tail 20 previous Thai lines.
- "Not sure who is speaking" threshold: `speaker_conf < 0.6` or `speaker is None`.
- Reading-speed target: ≤ 17 characters/second; show a warning above it.
- All user-facing text is Thai.
- Logs: `%LOCALAPPDATA%\SubTranslator\logs\app.log`, rotating 5 × 2 MB.

## File Structure

```
pyproject.toml
SubTranslator.spec                  PyInstaller build recipe
bin/                                ffmpeg.exe, ffprobe.exe (not committed)
subtitle_translator/
  __init__.py
  __main__.py                       entry point: python -m subtitle_translator
  app.py                            QApplication, theme, logging, MainWindow
  core/
    __init__.py
    models.py                       Cue, Character, Episode, Project
    project_store.py                .stproj.json load/save
    subio.py                        read subtitles (encoding fallback), effect detection, language guess
    media.py                        ffprobe/ffmpeg wrappers
    pricing.py                      model table + cost math
    settings.py                     Settings JSON + keyring API key
    logs.py                         logging setup
  ai/
    __init__.py
    backend.py                      AIRequest/AIResult/AIError/AIBackend + validate_json
    json_repair.py                  extract JSON from free text
    api_backend.py                  Anthropic API implementation
    claude_code_backend.py          Claude Code CLI implementation
    factory.py                      make_backend(settings)
  translate/
    __init__.py
    prompts.py                      system prompts, schemas, formatters
    analyze.py                      pass 1: characters + speakers
    translate.py                    pass 2: chunked translation
    pipeline.py                     prepare/run/estimate per file
  export/
    __init__.py                     output_paths, export_project
    srt_writer.py
    ass_writer.py
  gui/
    __init__.py
    theme.py                        colors + QSS
    worker.py                       CallWorker (generic QThread)
    main_window.py                  nav rail + page stack
    settings_page.py
    queue_page.py                   file queue + QueueRunner/EstimateRunner
    review_page.py                  review/edit page + VideoPreview
    timeline_widget.py
tests/
  conftest.py, helpers.py, fakes.py
  test_models.py, test_subio.py, test_media.py, test_pricing_settings.py,
  test_backend_core.py, test_analyze.py, test_translate.py, test_api_backend.py,
  test_claude_code_backend.py, test_pipeline.py, test_export.py, test_gui.py, test_live.py
```

---

### Task 1: Project scaffold, data model, project store

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `subtitle_translator/__init__.py`, `subtitle_translator/core/__init__.py`, `subtitle_translator/ai/__init__.py`, `subtitle_translator/translate/__init__.py`, `subtitle_translator/gui/__init__.py`, `subtitle_translator/core/models.py`, `subtitle_translator/core/project_store.py`
- Test: `tests/conftest.py`, `tests/helpers.py`, `tests/test_models.py`

**Interfaces:**
- Produces: `Cue`, `Character`, `Episode`, `Project` dataclasses (fields below); `Episode.cue(id) -> Cue`; `Episode.character(key) -> Character | None`; `Project.to_dict() -> dict`; `Project.from_dict(d) -> Project`; `project_path(video) -> Path`; `save_project(project) -> Path`; `load_project(video) -> Project | None`; test helper `make_episode(n=5, lang="ja") -> Episode`.

- [ ] **Step 1: Create the repo and scaffold**

```bash
git init
python -m venv .venv
.venv\Scripts\activate
```

`pyproject.toml`:

```toml
[project]
name = "subtitle-translator"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "PySide6>=6.7",
  "anthropic",
  "pysubs2>=1.7",
  "jsonschema>=4.22",
  "keyring>=25",
  "platformdirs>=4.2",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-qt>=4.4", "pyinstaller>=6.6"]

[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["subtitle_translator*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
  "live: calls real AI backends (costs money or Max quota)",
  "media: needs ffmpeg/ffprobe on PATH or in bin/",
]
addopts = "-m 'not live'"
```

`.gitignore`:

```
.venv/
__pycache__/
build/
dist/
bin/*.exe
*.stproj.json
```

Create empty `__init__.py` in `subtitle_translator/`, `core/`, `ai/`, `translate/`, `gui/`.

```bash
pip install -e ".[dev]"
pip show anthropic
```

Write the installed anthropic version into `pyproject.toml` as `"anthropic>=<installed version>"`.

- [ ] **Step 2: Write the failing test**

`tests/helpers.py`:

```python
from subtitle_translator.core.models import Cue, Episode


def make_episode(n: int = 5, lang: str = "ja") -> Episode:
    cues = [
        Cue(id=i, start=float(i * 2), end=float(i * 2 + 1.5), src=f"line {i}")
        for i in range(1, n + 1)
    ]
    return Episode(video_path="C:/v/ep01.mkv", source="C:/v/ep01.ass", src_lang=lang, cues=cues)
```

`tests/conftest.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
```

`tests/test_models.py`:

```python
import json

from helpers import make_episode
from subtitle_translator.core.models import Character, Project
from subtitle_translator.core.project_store import load_project, project_path, save_project


def test_project_roundtrip_through_dict():
    ep = make_episode(3)
    ep.characters.append(Character(key="yuki", name_src="ユキ", name_th="ยูกิ",
                                   address_terms={"お姉ちゃん": "พี่"}, color="#FBBF24"))
    ep.cues[0].speaker = "yuki"
    ep.cues[0].thai = "สวัสดี"
    p = Project(episode=ep, backend="api", model="claude-opus-5-5", done_chunks=[0], cost_usd=0.12)
    again = Project.from_dict(json.loads(json.dumps(p.to_dict())))
    assert again == p
    assert again.episode.cue(1).thai == "สวัสดี"
    assert again.episode.character("yuki").address_terms == {"お姉ちゃん": "พี่"}
    assert again.episode.character("nobody") is None


def test_save_and_load_project(tmp_path):
    video = tmp_path / "EP01.mkv"
    video.write_bytes(b"")
    ep = make_episode(2)
    ep.video_path = str(video)
    p = Project(episode=ep, backend="api", model="claude-opus-5-5")
    path = save_project(p)
    assert path == project_path(video) == tmp_path / "EP01.stproj.json"
    assert load_project(video) == p
    assert load_project(tmp_path / "missing.mkv") is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'subtitle_translator.core.models'`

- [ ] **Step 4: Write the implementation**

`subtitle_translator/core/models.py`:

```python
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
```

`subtitle_translator/core/project_store.py`:

```python
from __future__ import annotations

import json
import os
from pathlib import Path

from .models import Project


def project_path(video: str | Path) -> Path:
    p = Path(video)
    return p.with_name(p.stem + ".stproj.json")


def save_project(project: Project) -> Path:
    path = project_path(project.episode.video_path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(project.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_project(video: str | Path) -> Project | None:
    path = project_path(video)
    if not path.exists():
        return None
    return Project.from_dict(json.loads(path.read_text(encoding="utf-8")))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_models.py -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: scaffold project with data model and project store"
```

---

### Task 2: Subtitle reading (`subio`)

**Files:**
- Create: `subtitle_translator/core/subio.py`
- Test: `tests/test_subio.py`

**Interfaces:**
- Consumes: `Cue` (Task 1)
- Produces: `class SubtitleReadError(Exception)`; `read_text_any(path) -> str`; `is_effect(event: pysubs2.SSAEvent) -> bool`; `load_cues(path) -> tuple[list[Cue], pysubs2.SSAFile]`; `guess_lang(cues) -> str` ("ja" | "en"); `load_ssa(path) -> pysubs2.SSAFile`.

- [ ] **Step 1: Write the failing test**

`tests/test_subio.py`:

```python
import pytest

from subtitle_translator.core.subio import (
    SubtitleReadError, guess_lang, load_cues, read_text_any,
)

ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1
Style: OP,Arial,40,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,8,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.00,Default,Haru,0,0,0,,お姉ちゃん、起きて！
Dialogue: 0,0:00:04.00,0:00:06.00,Default,,{\\i1}うるさいなぁ{\\i0}\\Nあと五分だけ。
Dialogue: 0,0:00:07.00,0:00:09.00,OP,,{\\k20}ra{\\k30}la
Comment: 0,0:00:10.00,0:00:11.00,Default,,note
Dialogue: 0,0:00:12.00,0:00:13.00,Default,,{\\p1}m 0 0 l 10 10{\\p0}
"""

SRT = "1\n00:00:01,000 --> 00:00:02,500\nWake up, sis!\n\n2\n00:00:03,000 --> 00:00:04,000\nFive more minutes.\n"


def test_load_ass_cues_marks_effects_and_keeps_order(tmp_path):
    p = tmp_path / "a.ass"
    p.write_text(ASS, encoding="utf-8")
    cues, subs = load_cues(p)
    assert [c.src for c in cues][:3] == ["お姉ちゃん、起きて！", "うるさいなぁ\nあと五分だけ。", "rala"]
    assert [c.id for c in cues] == [1, 2, 3, 4]          # comment line dropped, drawing kept as skip
    assert cues[0].actor == "Haru" and cues[0].style == "Default"
    assert cues[0].start == 1.0 and cues[0].end == 3.0
    assert [c.skip for c in cues] == [False, False, True, True]
    assert subs.events[cues[2].src_index].style == "OP"


def test_load_srt_cp932_and_utf16(tmp_path):
    jp = "1\n00:00:01,000 --> 00:00:02,000\nこんにちは\n"
    p1 = tmp_path / "jp.srt"
    p1.write_bytes(jp.encode("cp932"))
    assert load_cues(p1)[0][0].src == "こんにちは"
    p2 = tmp_path / "en.srt"
    p2.write_bytes(SRT.encode("utf-16"))
    cues, _ = load_cues(p2)
    assert [c.src for c in cues] == ["Wake up, sis!", "Five more minutes."]


def test_read_text_any_rejects_garbage(tmp_path):
    p = tmp_path / "bad.srt"
    p.write_bytes(b"")
    with pytest.raises(SubtitleReadError):
        load_cues(p)


def test_guess_lang(tmp_path):
    p = tmp_path / "e.srt"
    p.write_text(SRT, encoding="utf-8")
    assert guess_lang(load_cues(p)[0]) == "en"
    p2 = tmp_path / "j.ass"
    p2.write_text(ASS, encoding="utf-8")
    assert guess_lang(load_cues(p2)[0]) == "ja"
    assert read_text_any(p2).startswith("[Script Info]")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_subio.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'subtitle_translator.core.subio'`

- [ ] **Step 3: Write the implementation**

`subtitle_translator/core/subio.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_subio.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add subtitle_translator/core/subio.py tests/test_subio.py
git commit -m "feat: read srt/ass with encoding fallback and effect detection"
```

---

### Task 3: Media wrappers (`media`)

**Files:**
- Create: `subtitle_translator/core/media.py`
- Test: `tests/test_media.py`

**Interfaces:**
- Produces: `class MediaError(Exception)`; `tool_path(name: str) -> str`; `@dataclass SubTrack(sub_index: int, codec: str, language: str, title: str)` with property `is_text: bool`; `lang_of(track) -> str | None`; `list_sub_tracks(video) -> list[SubTrack]`; `pick_track(tracks, prefer=("ja","en")) -> tuple[SubTrack | None, list[SubTrack]]` (chosen, candidates-to-ask); `extract_sub(video, track, out_dir) -> Path`; `grab_frame(video, t, out_png, width=640) -> Path`; `sidecar_subs(video) -> list[Path]`; `no_window() -> dict`.

- [ ] **Step 1: Get ffmpeg for development**

Download the "release essentials" build of ffmpeg for Windows from https://www.gyan.dev/ffmpeg/builds/, and copy `ffmpeg.exe` and `ffprobe.exe` into `bin/`. Check: `bin\ffprobe.exe -version` prints a version.

- [ ] **Step 2: Write the failing test**

`tests/test_media.py`:

```python
import shutil
import subprocess

import pytest

from subtitle_translator.core.media import (
    MediaError, SubTrack, extract_sub, grab_frame, list_sub_tracks, pick_track,
    sidecar_subs, tool_path,
)
from subtitle_translator.core.subio import load_cues


def T(i, codec, lang):
    return SubTrack(sub_index=i, codec=codec, language=lang, title="")


def test_pick_track_prefers_japanese_then_english():
    tracks = [T(0, "subrip", "eng"), T(1, "ass", "jpn"), T(2, "hdmv_pgs_subtitle", "jpn")]
    assert pick_track(tracks) == (tracks[1], [])
    assert pick_track(tracks, prefer=("en", "ja")) == (tracks[0], [])


def test_pick_track_asks_when_ambiguous_or_unknown():
    two = [T(0, "ass", "jpn"), T(1, "subrip", "jpn")]
    assert pick_track(two) == (None, two)
    und = [T(0, "subrip", "und")]
    assert pick_track(und) == (und[0], [])
    many_und = [T(0, "subrip", "und"), T(1, "ass", "fre")]
    assert pick_track(many_und) == (None, many_und)
    assert pick_track([T(0, "hdmv_pgs_subtitle", "jpn")]) == (None, [])


def test_sidecar_subs(tmp_path):
    v = tmp_path / "EP01 [x].mkv"
    v.write_bytes(b"")
    (tmp_path / "EP01 [x].srt").write_text("x")
    (tmp_path / "EP01 [x].ja.ass").write_text("x")
    (tmp_path / "EP01 [x].th.srt").write_text("x")
    (tmp_path / "EP02.srt").write_text("x")
    names = sorted(p.name for p in sidecar_subs(v))
    assert names == ["EP01 [x].ja.ass", "EP01 [x].srt"]


def _have_ffmpeg():
    try:
        tool_path("ffmpeg")
        tool_path("ffprobe")
        return True
    except MediaError:
        return False


@pytest.mark.media
@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not available")
def test_list_and_extract_real_mkv(tmp_path):
    ass = tmp_path / "s.ass"
    ass.write_text(
        "[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,お姉ちゃん\n",
        encoding="utf-8",
    )
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nSis!\n", encoding="utf-8")
    mkv = tmp_path / "v.mkv"
    subprocess.run([
        tool_path("ffmpeg"), "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=c=black:s=320x240:d=5",
        "-i", str(ass), "-i", str(srt),
        "-map", "0", "-map", "1", "-map", "2",
        "-c:v", "libx264", "-c:s", "copy",
        "-metadata:s:s:0", "language=jpn", "-metadata:s:s:1", "language=eng",
        str(mkv),
    ], check=True)
    tracks = list_sub_tracks(mkv)
    assert [(t.codec, t.language) for t in tracks] == [("ass", "jpn"), ("subrip", "eng")]
    out = extract_sub(mkv, tracks[0], tmp_path)
    assert out.suffix == ".ass"
    assert load_cues(out)[0][0].src == "お姉ちゃん"
    png = grab_frame(mkv, 1.0, tmp_path / "f.png", width=160)
    assert png.exists() and png.stat().st_size > 0
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_media.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'subtitle_translator.core.media'`

- [ ] **Step 4: Write the implementation**

`subtitle_translator/core/media.py`:

```python
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

TEXT_CODECS = {"subrip", "ass", "ssa", "webvtt", "mov_text", "text"}
LANG_ALIASES = {"ja": ("jpn", "ja", "jp"), "en": ("eng", "en")}
SUB_SUFFIXES = {".srt", ".ass", ".ssa"}


class MediaError(Exception):
    pass


def no_window() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def tool_path(name: str) -> str:
    exe = name + (".exe" if os.name == "nt" else "")
    bases = [Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])),
             Path(sys.executable).parent]
    for base in bases:
        cand = base / "bin" / exe
        if cand.exists():
            return str(cand)
    found = shutil.which(name)
    if found:
        return found
    raise MediaError(f"ไม่พบ {exe} — วางไฟล์ไว้ในโฟลเดอร์ bin ของโปรแกรม")


def _run(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout, **no_window())
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"ffmpeg ทำงานนานเกินไป: {' '.join(args[:3])}") from exc


@dataclass
class SubTrack:
    sub_index: int          # index among subtitle streams (ffmpeg "0:s:N")
    codec: str
    language: str
    title: str

    @property
    def is_text(self) -> bool:
        return self.codec in TEXT_CODECS


def lang_of(track: SubTrack) -> str | None:
    lang = track.language.lower()
    for key, aliases in LANG_ALIASES.items():
        if lang in aliases:
            return key
    return None


def list_sub_tracks(video: str | Path) -> list[SubTrack]:
    r = _run([tool_path("ffprobe"), "-v", "error", "-print_format", "json",
              "-show_streams", "-select_streams", "s", str(video)])
    if r.returncode != 0:
        raise MediaError(r.stderr.decode("utf-8", "replace").strip() or "ffprobe อ่านไฟล์ไม่ได้")
    streams = json.loads(r.stdout or b"{}").get("streams", [])
    tracks = []
    for i, s in enumerate(streams):
        tags = s.get("tags") or {}
        tracks.append(SubTrack(sub_index=i, codec=s.get("codec_name", ""),
                               language=tags.get("language", "und"), title=tags.get("title", "")))
    return tracks


def pick_track(tracks: list[SubTrack], prefer: tuple[str, ...] = ("ja", "en")
               ) -> tuple[SubTrack | None, list[SubTrack]]:
    text = [t for t in tracks if t.is_text]
    for lang in prefer:
        cands = [t for t in text if lang_of(t) == lang]
        if len(cands) == 1:
            return cands[0], []
        if len(cands) > 1:
            return None, cands
    if len(text) == 1:
        return text[0], []
    return None, text


def extract_sub(video: str | Path, track: SubTrack, out_dir: str | Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    keep_ass = track.codec in ("ass", "ssa")
    ext = "ass" if keep_ass else "srt"
    codec = "copy" if track.codec in ("ass", "ssa", "subrip") else "srt"
    out = out_dir / f"{Path(video).stem}.s{track.sub_index}.{ext}"
    r = _run([tool_path("ffmpeg"), "-y", "-v", "error", "-i", str(video),
              "-map", f"0:s:{track.sub_index}", "-c:s", codec, str(out)])
    if r.returncode != 0 or not out.exists():
        raise MediaError(r.stderr.decode("utf-8", "replace").strip() or "ดึงซับไม่สำเร็จ")
    return out


def grab_frame(video: str | Path, t: float, out_png: str | Path, width: int = 640) -> Path:
    out = Path(out_png)
    r = _run([tool_path("ffmpeg"), "-y", "-v", "error", "-ss", f"{max(t, 0):.3f}",
              "-i", str(video), "-frames:v", "1", "-vf", f"scale={width}:-2", str(out)], timeout=60)
    if r.returncode != 0 or not out.exists():
        raise MediaError(r.stderr.decode("utf-8", "replace").strip() or "จับภาพไม่สำเร็จ")
    return out


def sidecar_subs(video: str | Path) -> list[Path]:
    v = Path(video)
    found = []
    for name in glob.glob(glob.escape(str(v.parent / v.stem)) + "*"):
        p = Path(name)
        if p.suffix.lower() in SUB_SUFFIXES and ".th." not in p.name.lower():
            found.append(p)
    return sorted(found)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_media.py -v`
Expected: 4 passed (the media test is skipped only if `bin/` has no ffmpeg)

- [ ] **Step 6: Commit**

```bash
git add subtitle_translator/core/media.py tests/test_media.py
git commit -m "feat: ffprobe/ffmpeg wrappers for subtitle tracks and frames"
```

---

### Task 4: Pricing and settings

**Files:**
- Create: `subtitle_translator/core/pricing.py`, `subtitle_translator/core/settings.py`
- Test: `tests/test_pricing_settings.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) ModelInfo(id, label, input, output, cache_read, recommended, note)`; `MODELS: dict[str, ModelInfo]`; `DEFAULT_MODEL = "claude-opus-5-5"`; `cost_usd(model, input_tokens, output_tokens, cache_read=0, cache_write=0) -> float`; `@dataclass Settings(backend="api", model="claude-opus-5-5", effort_analyze="high", effort_translate="medium", chunk_size=80, prefer_langs=["ja","en"], output_dir=None, ass_font="Leelawadee UI", ass_size=52, srt_colors=False)`; `default_settings_path() -> Path`; `load_settings(path=None) -> Settings`; `save_settings(s, path=None) -> None`; `get_api_key() -> str | None`; `set_api_key(key) -> None`; `clear_api_key() -> None`; `cache_dir() -> Path`; `log_dir() -> Path`.

- [ ] **Step 1: Write the failing test**

`tests/test_pricing_settings.py`:

```python
import keyring
import pytest
from keyring.backend import KeyringBackend

from subtitle_translator.core import settings as st
from subtitle_translator.core.pricing import DEFAULT_MODEL, MODELS, cost_usd


def test_cost_math():
    assert DEFAULT_MODEL == "claude-opus-5-5" and MODELS[DEFAULT_MODEL].recommended
    assert cost_usd("claude-opus-5-5", 1_000_000, 0) == pytest.approx(4.0)
    assert cost_usd("claude-opus-5-5", 0, 1_000_000) == pytest.approx(20.0)
    assert cost_usd("claude-sonnet-5-5", 1_000_000, 1_000_000) == pytest.approx(12.0)
    assert cost_usd("claude-opus-5-5", 0, 0, cache_read=1_000_000) == pytest.approx(0.20)
    assert cost_usd("claude-opus-5-5", 0, 0, cache_write=1_000_000) == pytest.approx(5.0)


def test_settings_roundtrip_ignores_unknown_keys(tmp_path):
    path = tmp_path / "settings.json"
    s = st.Settings(backend="claude_code", chunk_size=60)
    st.save_settings(s, path)
    assert st.load_settings(path) == s
    path.write_text('{"backend": "api", "bogus": 1}', encoding="utf-8")
    assert st.load_settings(path).backend == "api"
    assert st.load_settings(tmp_path / "none.json") == st.Settings()
    assert "key" not in path.read_text(encoding="utf-8").lower()


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.store = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        self.store.pop((service, username), None)


def test_api_key_uses_keyring():
    keyring.set_keyring(MemoryKeyring())
    assert st.get_api_key() is None
    st.set_api_key("  sk-ant-test  ")
    assert st.get_api_key() == "sk-ant-test"
    st.clear_api_key()
    assert st.get_api_key() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pricing_settings.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

`subtitle_translator/core/pricing.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelInfo:
    id: str
    label: str
    input: float        # USD per 1M input tokens
    output: float       # USD per 1M output tokens
    cache_read: float   # USD per 1M cached input tokens read
    recommended: bool = False
    note: str = ""


# Haiku 5.5: prices are for prompts up to 100K tokens; its cache-read price is
# assumed at 10% of input. Re-check both against the live pricing page before release.
MODELS: dict[str, ModelInfo] = {
    "claude-opus-5-5": ModelInfo("claude-opus-5-5", "Claude Opus 5.5", 4.00, 20.00, 0.20, True,
                                 "แปลดีที่สุด จับน้ำเสียงและความสัมพันธ์ตัวละครได้แม่น"),
    "claude-sonnet-5-5": ModelInfo("claude-sonnet-5-5", "Claude Sonnet 5.5", 2.00, 10.00, 0.20, False,
                                   "คุณภาพรองลงมา ราคาครึ่งหนึ่ง เหมาะแปลทั้งซีซัน"),
    "claude-haiku-5-5": ModelInfo("claude-haiku-5-5", "Claude Haiku 5.5", 0.10, 0.50, 0.01, False,
                                  "ถูกมาก ใช้ร่างงาน ไม่แนะนำสำหรับแปลจริง"),
}
DEFAULT_MODEL = "claude-opus-5-5"
CACHE_WRITE_MULT = 1.25


def cost_usd(model: str, input_tokens: int, output_tokens: int,
             cache_read: int = 0, cache_write: int = 0) -> float:
    m = MODELS[model]
    total = (input_tokens * m.input + output_tokens * m.output
             + cache_read * m.cache_read + cache_write * m.input * CACHE_WRITE_MULT)
    return total / 1_000_000
```

`subtitle_translator/core/settings.py`:

```python
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import keyring
import platformdirs
from keyring.errors import PasswordDeleteError

from .pricing import DEFAULT_MODEL

APP = "SubTranslator"
KEYRING_SERVICE = "SubTranslator"
KEYRING_USER = "anthropic_api_key"


@dataclass
class Settings:
    backend: str = "api"                    # "api" | "claude_code"
    model: str = DEFAULT_MODEL
    effort_analyze: str = "high"
    effort_translate: str = "medium"
    chunk_size: int = 80
    prefer_langs: list[str] = field(default_factory=lambda: ["ja", "en"])
    output_dir: str | None = None           # None = beside the video
    ass_font: str = "Leelawadee UI"
    ass_size: int = 52
    srt_colors: bool = False


def default_settings_path() -> Path:
    return Path(platformdirs.user_config_dir(APP, appauthor=False)) / "settings.json"


def cache_dir() -> Path:
    p = Path(platformdirs.user_cache_dir(APP, appauthor=False))
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_dir() -> Path:
    p = Path(platformdirs.user_log_dir(APP, appauthor=False))
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_settings(path: Path | None = None) -> Settings:
    path = path or default_settings_path()
    if not path.exists():
        return Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Settings()
    known = {f.name for f in fields(Settings)}
    return Settings(**{k: v for k, v in raw.items() if k in known})


def save_settings(s: Settings, path: Path | None = None) -> None:
    path = path or default_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(s), ensure_ascii=False, indent=1), encoding="utf-8")


def get_api_key() -> str | None:
    return keyring.get_password(KEYRING_SERVICE, KEYRING_USER)


def set_api_key(key: str) -> None:
    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key.strip())


def clear_api_key() -> None:
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
    except PasswordDeleteError:
        pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pricing_settings.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add subtitle_translator/core/pricing.py subtitle_translator/core/settings.py tests/test_pricing_settings.py
git commit -m "feat: model pricing table and settings with keyring API key"
```

---

### Task 5: AI backend contract and JSON repair

**Files:**
- Create: `subtitle_translator/ai/backend.py`, `subtitle_translator/ai/json_repair.py`
- Test: `tests/fakes.py`, `tests/test_backend_core.py`

**Interfaces:**
- Produces:
  - `class AIErrorKind(str, Enum)`: `AUTH, RATE_LIMIT, QUOTA, BAD_REQUEST, REFUSAL, TRUNCATED, INVALID_OUTPUT, NETWORK, NOT_INSTALLED`
  - `class AIError(Exception)` with `.kind: AIErrorKind`, `.retry_after: str | None`, constructor `AIError(kind, message, retry_after=None)`
  - `@dataclass Usage(input_tokens=0, output_tokens=0, cache_read=0, cache_write=0)`
  - `@dataclass AIResult(data: Any, usage: Usage, cost_usd: float | None)`
  - `@dataclass AIRequest(system: str, context: str, task: str, schema: dict, effort: str = "medium")` — `system` and `context` are stable across calls for one episode (cacheable); `task` varies.
  - `class AIBackend(Protocol)`: attributes `name: str`, `model: str`; methods `run_json(req) -> AIResult`, `estimate_cost(reqs: list[AIRequest], expected_output_tokens: int) -> float | None`, `check() -> str`
  - `validate_json(data, schema) -> None` (raises `AIError(INVALID_OUTPUT)`)
  - `extract_json(text: str) -> Any` (raises `ValueError`)
  - test fake: `FakeBackend(responses)` where each response is a dict, an `Exception` to raise, or a callable `req -> dict`; it records `.requests`.

- [ ] **Step 1: Write the failing test**

`tests/fakes.py`:

```python
from subtitle_translator.ai.backend import AIResult, Usage


class FakeBackend:
    name = "fake"
    model = "claude-opus-5-5"

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def run_json(self, req):
        self.requests.append(req)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        if callable(r):
            r = r(req)
        return AIResult(r, Usage(input_tokens=10, output_tokens=5), 0.01)

    def estimate_cost(self, reqs, expected_output_tokens):
        return 0.5

    def check(self):
        return "ok"
```

`tests/test_backend_core.py`:

```python
import pytest

from subtitle_translator.ai.backend import AIError, AIErrorKind, validate_json
from subtitle_translator.ai.json_repair import extract_json

SCHEMA = {"type": "object", "required": ["lines"], "additionalProperties": False,
          "properties": {"lines": {"type": "array", "items": {"type": "integer"}}}}


def test_validate_json():
    validate_json({"lines": [1, 2]}, SCHEMA)
    with pytest.raises(AIError) as e:
        validate_json({"lines": ["x"]}, SCHEMA)
    assert e.value.kind is AIErrorKind.INVALID_OUTPUT


@pytest.mark.parametrize("text, expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('Here you go:\n{"a": [1, {"b": 2}]}\nThanks', {"a": [1, {"b": 2}]}),
    ('result: [{"id": 1}, {"id": 2}] done', [{"id": 1}, {"id": 2}]),
])
def test_extract_json(text, expected):
    assert extract_json(text) == expected


def test_extract_json_fails_cleanly():
    with pytest.raises(ValueError):
        extract_json("no json here")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backend_core.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

`subtitle_translator/ai/backend.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

import jsonschema


class AIErrorKind(str, Enum):
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    QUOTA = "quota"
    BAD_REQUEST = "bad_request"
    REFUSAL = "refusal"
    TRUNCATED = "truncated"
    INVALID_OUTPUT = "invalid_output"
    NETWORK = "network"
    NOT_INSTALLED = "not_installed"


class AIError(Exception):
    def __init__(self, kind: AIErrorKind, message: str, retry_after: str | None = None):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0


@dataclass
class AIResult:
    data: Any
    usage: Usage = field(default_factory=Usage)
    cost_usd: float | None = None


@dataclass
class AIRequest:
    system: str
    context: str
    task: str
    schema: dict
    effort: str = "medium"


class AIBackend(Protocol):
    name: str
    model: str

    def run_json(self, req: AIRequest) -> AIResult: ...

    def estimate_cost(self, reqs: list[AIRequest], expected_output_tokens: int) -> float | None: ...

    def check(self) -> str: ...


def validate_json(data: Any, schema: dict) -> None:
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as exc:
        raise AIError(AIErrorKind.INVALID_OUTPUT, f"ผลลัพธ์ไม่ตรงรูปแบบ: {exc.message}") from exc
```

`subtitle_translator/ai/json_repair.py`:

```python
from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Any:
    t = text.strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    m = _FENCE.search(t)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = t.find(opener), t.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(t[i:j + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("ไม่พบ JSON ในคำตอบ")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_backend_core.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add subtitle_translator/ai tests/fakes.py tests/test_backend_core.py
git commit -m "feat: AI backend contract, schema validation, JSON repair"
```

---

### Task 6: Prompts and pass 1 (character analysis)

**Files:**
- Create: `subtitle_translator/translate/prompts.py`, `subtitle_translator/translate/analyze.py`
- Test: `tests/test_analyze.py`

**Interfaces:**
- Consumes: `Episode`, `Cue`, `Character` (Task 1); `AIRequest`, `AIResult`, `AIBackend` (Task 5)
- Produces: `SYSTEM_ANALYZE`, `SYSTEM_TRANSLATE`, `ANALYZE_SCHEMA`, `TRANSLATE_SCHEMA`, `LANG_NAMES = {"ja": "Japanese", "en": "English"}`, `format_cues(cues, names: dict[str, str] | None = None) -> str` (one line per non-skip cue: `id<TAB>mm:ss.s<TAB>tag<TAB>text`, newlines as ` / `), `format_sheet(ep) -> str`, `TRANSLATE_MARKER = "TRANSLATE THESE LINES:"`; `PALETTE: list[str]`, `UNKNOWN_COLOR = "#A8A29E"`, `build_analyze_request(ep, effort) -> AIRequest`, `apply_analysis(ep, data) -> None`, `analyze(ep, backend, effort) -> AIResult`.

- [ ] **Step 1: Write the failing test**

`tests/test_analyze.py`:

```python
from fakes import FakeBackend
from helpers import make_episode
from subtitle_translator.ai.backend import validate_json
from subtitle_translator.translate.analyze import PALETTE, analyze, build_analyze_request
from subtitle_translator.translate.prompts import ANALYZE_SCHEMA, format_cues

ANALYSIS = {
    "summary": "พี่น้องสาวตื่นสายไปโรงเรียน",
    "characters": [
        {"key": "yuki", "name_src": "ユキ", "name_th": "ยูกิ", "gender": "female",
         "role": "พี่สาว ม.ปลาย", "relations": ["พี่สาวของฮารุ"],
         "address_terms": [{"source": "お姉ちゃん", "thai": "พี่"}]},
        {"key": "haru", "name_src": "ハル", "name_th": "ฮารุ", "gender": "female",
         "role": "น้องสาว ม.ต้น", "relations": ["น้องสาวของยูกิ"], "address_terms": []},
    ],
    "cues": [
        {"id": 1, "speaker": "haru", "addressee": "ยูกิ", "confidence": 0.9},
        {"id": 2, "speaker": "yuki", "addressee": "ฮารุ", "confidence": 1.4},
        {"id": 3, "speaker": "ghost", "addressee": "", "confidence": 0.3},
    ],
}


def test_schema_accepts_sample():
    validate_json(ANALYSIS, ANALYZE_SCHEMA)


def test_format_cues_skips_effects_and_flattens_newlines():
    ep = make_episode(3)
    ep.cues[0].src = "a\nb"
    ep.cues[1].skip = True
    ep.cues[2].speaker = "yuki"
    ep.cues[2].addressee = "ฮารุ"
    out = format_cues(ep.cues, names={"yuki": "ยูกิ"})
    lines = out.splitlines()
    assert lines[0] == "1\t00:02.0\t?→?\ta / b"
    assert lines[1] == "3\t00:06.0\tยูกิ→ฮารุ\tline 3"
    assert len(lines) == 2


def test_analyze_applies_characters_and_speakers():
    ep = make_episode(3)
    backend = FakeBackend([ANALYSIS])
    res = analyze(ep, backend, "high")
    req = backend.requests[0]
    assert req.effort == "high" and req.schema is ANALYZE_SCHEMA
    assert "Japanese" in req.context and "line 3" in req.context
    assert [c.key for c in ep.characters] == ["yuki", "haru"]
    assert ep.characters[0].address_terms == {"お姉ちゃん": "พี่"}
    assert [c.color for c in ep.characters] == PALETTE[:2]
    assert ep.summary.startswith("พี่น้อง")
    assert (ep.cues[0].speaker, ep.cues[0].speaker_conf) == ("haru", 0.9)
    assert ep.cues[1].speaker_conf == 1.0
    assert ep.cues[2].speaker is None
    assert res.cost_usd == 0.01


def test_build_request_is_stable_for_caching():
    ep = make_episode(2)
    a = build_analyze_request(ep, "high")
    b = build_analyze_request(ep, "high")
    assert (a.system, a.context) == (b.system, b.context)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_analyze.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `prompts.py`**

`subtitle_translator/translate/prompts.py`:

```python
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
    "required": ["summary", "characters", "cues"],
    "properties": {
        "summary": {"type": "string"},
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
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:04.1f}"


def format_cues(cues: list[Cue], names: dict[str, str] | None = None) -> str:
    out = []
    for c in cues:
        if c.skip:
            continue
        if names is not None:
            tag = f"{names.get(c.speaker or '', '?')}→{c.addressee or '?'}"
        else:
            tag = "/".join(x for x in (c.actor, c.style) if x) or "-"
        text = c.src.replace("\n", " / ")
        out.append(f"{c.id}\t{_mmss(c.start)}\t{tag}\t{text}")
    return "\n".join(out)


def format_sheet(ep: Episode) -> str:
    sheet = {
        "summary": ep.summary,
        "characters": [
            {"key": ch.key, "name_src": ch.name_src, "name_th": ch.name_th, "gender": ch.gender,
             "role": ch.role, "relations": ch.relations, "address_terms": ch.address_terms}
            for ch in ep.characters
        ],
    }
    return "CHARACTER SHEET:\n" + json.dumps(sheet, ensure_ascii=False, indent=1)
```

- [ ] **Step 4: Write `analyze.py`**

`subtitle_translator/translate/analyze.py`:

```python
from __future__ import annotations

from ..ai.backend import AIBackend, AIRequest, AIResult
from ..core.models import Character, Episode
from .prompts import ANALYZE_SCHEMA, LANG_NAMES, SYSTEM_ANALYZE, format_cues

PALETTE = ["#FBBF24", "#F472B6", "#2DD4BF", "#60A5FA", "#A78BFA", "#FB923C", "#4ADE80", "#F87171"]
UNKNOWN_COLOR = "#A8A29E"


def build_analyze_request(ep: Episode, effort: str) -> AIRequest:
    context = (f"Source language: {LANG_NAMES.get(ep.src_lang, ep.src_lang)}\n"
               "Columns: id, start time, Name/Actor/style tag, text\n\n"
               "SUBTITLES:\n" + format_cues(ep.cues))
    return AIRequest(system=SYSTEM_ANALYZE, context=context,
                     task="Analyze the subtitles above and return the JSON.",
                     schema=ANALYZE_SCHEMA, effort=effort)


def apply_analysis(ep: Episode, data: dict) -> None:
    ep.summary = data["summary"]
    ep.characters = [
        Character(
            key=c["key"], name_src=c["name_src"], name_th=c["name_th"], gender=c["gender"],
            role=c["role"], relations=list(c["relations"]),
            address_terms={t["source"]: t["thai"] for t in c["address_terms"]},
            color=PALETTE[i % len(PALETTE)],
        )
        for i, c in enumerate(data["characters"])
    ]
    keys = {c.key for c in ep.characters}
    guesses = {g["id"]: g for g in data["cues"]}
    for cue in ep.cues:
        g = guesses.get(cue.id)
        if cue.skip or g is None:
            continue
        cue.speaker = g["speaker"] if g["speaker"] in keys else None
        cue.addressee = g["addressee"] or None
        cue.speaker_conf = max(0.0, min(1.0, float(g["confidence"])))


def analyze(ep: Episode, backend: AIBackend, effort: str) -> AIResult:
    res = backend.run_json(build_analyze_request(ep, effort))
    apply_analysis(ep, res.data)
    return res
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_analyze.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add subtitle_translator/translate tests/test_analyze.py
git commit -m "feat: prompts, schemas and pass-1 character analysis"
```

---

### Task 7: Pass 2 (chunked translation)

**Files:**
- Create: `subtitle_translator/translate/translate.py`
- Test: `tests/test_translate.py`

**Interfaces:**
- Consumes: Task 5 (`AIError`, `AIErrorKind`, `AIRequest`, `AIBackend`), Task 6 (`SYSTEM_TRANSLATE`, `TRANSLATE_SCHEMA`, `TRANSLATE_MARKER`, `format_cues`, `format_sheet`)
- Produces: `MIN_CHUNK = 10`, `CONTEXT_TAIL = 20`; `make_chunks(cues, size) -> list[list[int]]`; `build_translate_request(ep, ids, effort) -> AIRequest`; `check_lines(expected: list[int], lines: list[dict]) -> tuple[dict[int, str], list[int]]`; `translate_chunk(ep, ids, backend, effort) -> float` (returns cost in USD, 0.0 when unknown; marks unrecoverable lines `status="failed"`; re-raises AUTH/RATE_LIMIT/QUOTA/NETWORK/NOT_INSTALLED/BAD_REQUEST).

- [ ] **Step 1: Write the failing test**

`tests/test_translate.py`:

```python
import pytest

from fakes import FakeBackend
from helpers import make_episode
from subtitle_translator.ai.backend import AIError, AIErrorKind
from subtitle_translator.translate.prompts import TRANSLATE_MARKER
from subtitle_translator.translate.translate import (
    build_translate_request, check_lines, make_chunks, translate_chunk,
)


def ids_in(req):
    block = req.task.split(TRANSLATE_MARKER, 1)[1].strip()
    return [int(line.split("\t")[0]) for line in block.splitlines() if line.strip()]


def echo(req):
    return {"lines": [{"id": i, "thai": f"ไทย {i}"} for i in ids_in(req)]}


def test_make_chunks_skips_effects():
    ep = make_episode(7)
    ep.cues[2].skip = True
    assert make_chunks(ep.cues, 3) == [[1, 2, 4], [5, 6, 7]]


def test_request_has_stable_context_and_previous_lines():
    ep = make_episode(4)
    ep.cues[0].thai = "ก่อนหน้า"
    req = build_translate_request(ep, [2, 3], "medium")
    assert "line 4" in req.context and "CHARACTER SHEET" in req.context
    assert "1\tก่อนหน้า" in req.task
    assert ids_in(req) == [2, 3]
    assert build_translate_request(ep, [4], "medium").context == req.context


def test_check_lines():
    got, missing = check_lines([1, 2, 3], [{"id": 1, "thai": " ก "}, {"id": 1, "thai": "ซ้ำ"},
                                           {"id": 9, "thai": "เกิน"}, {"id": 2, "thai": "  "}])
    assert got == {1: "ก"} and missing == [2, 3]


def test_translate_chunk_happy_path():
    ep = make_episode(3)
    cost = translate_chunk(ep, [1, 2, 3], FakeBackend([echo]), "medium")
    assert [c.thai for c in ep.cues] == ["ไทย 1", "ไทย 2", "ไทย 3"]
    assert {c.status for c in ep.cues} == {"translated"}
    assert cost == pytest.approx(0.01)


def test_translate_chunk_retries_missing_once_then_fails():
    ep = make_episode(3)
    backend = FakeBackend([{"lines": [{"id": 1, "thai": "หนึ่ง"}]},
                           {"lines": [{"id": 2, "thai": "สอง"}]}])
    translate_chunk(ep, [1, 2, 3], backend, "medium")
    assert ids_in(backend.requests[1]) == [2, 3]
    assert [c.status for c in ep.cues] == ["translated", "translated", "failed"]


def test_translate_chunk_splits_when_truncated():
    ep = make_episode(24)
    ids = [c.id for c in ep.cues]
    backend = FakeBackend([AIError(AIErrorKind.TRUNCATED, "cut"), echo, echo])
    translate_chunk(ep, ids, backend, "medium")
    assert [len(ids_in(r)) for r in backend.requests] == [24, 12, 12]
    assert all(c.thai for c in ep.cues)


def test_translate_chunk_refusal_marks_failed():
    ep = make_episode(2)
    translate_chunk(ep, [1, 2], FakeBackend([AIError(AIErrorKind.REFUSAL, "no")]), "medium")
    assert [c.status for c in ep.cues] == ["failed", "failed"]


def test_translate_chunk_reraises_auth():
    ep = make_episode(2)
    with pytest.raises(AIError):
        translate_chunk(ep, [1, 2], FakeBackend([AIError(AIErrorKind.AUTH, "bad key")]), "medium")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_translate.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

`subtitle_translator/translate/translate.py`:

```python
from __future__ import annotations

from ..ai.backend import AIBackend, AIError, AIErrorKind, AIRequest
from ..core.models import Cue, Episode
from .prompts import (
    LANG_NAMES, SYSTEM_TRANSLATE, TRANSLATE_MARKER, TRANSLATE_SCHEMA, format_cues, format_sheet,
)

MIN_CHUNK = 10
CONTEXT_TAIL = 20
_LOCAL_FAILURES = (AIErrorKind.REFUSAL, AIErrorKind.INVALID_OUTPUT, AIErrorKind.TRUNCATED)


def make_chunks(cues: list[Cue], size: int) -> list[list[int]]:
    ids = [c.id for c in cues if not c.skip]
    return [ids[i:i + size] for i in range(0, len(ids), size)]


def _names(ep: Episode) -> dict[str, str]:
    return {ch.key: ch.name_th for ch in ep.characters}


def build_translate_request(ep: Episode, ids: list[int], effort: str) -> AIRequest:
    names = _names(ep)
    context = (f"Source language: {LANG_NAMES.get(ep.src_lang, ep.src_lang)}\n"
               + format_sheet(ep)
               + "\n\nFULL SOURCE SUBTITLES (id, time, speaker→listener, text):\n"
               + format_cues(ep.cues, names))
    first = ids[0]
    prev = [c for c in ep.cues if c.id < first and c.thai and not c.skip][-CONTEXT_TAIL:]
    prev_txt = "\n".join(f"{c.id}\t{c.thai}" for c in prev) or "(none)"
    wanted = [ep.cue(i) for i in ids]
    task = ("PREVIOUS THAI LINES (for continuity only, do not output them):\n" + prev_txt
            + "\n\n" + TRANSLATE_MARKER + "\n" + format_cues(wanted, names))
    return AIRequest(system=SYSTEM_TRANSLATE, context=context, task=task,
                     schema=TRANSLATE_SCHEMA, effort=effort)


def check_lines(expected: list[int], lines: list[dict]) -> tuple[dict[int, str], list[int]]:
    wanted = set(expected)
    got: dict[int, str] = {}
    for item in lines:
        cue_id = item.get("id")
        text = (item.get("thai") or "").strip()
        if cue_id in wanted and text and cue_id not in got:
            got[cue_id] = text
    return got, [i for i in expected if i not in got]


def _apply(ep: Episode, got: dict[int, str]) -> None:
    for cue_id, text in got.items():
        cue = ep.cue(cue_id)
        cue.thai = text
        cue.status = "translated"


def _mark_failed(ep: Episode, ids: list[int]) -> None:
    for cue_id in ids:
        cue = ep.cue(cue_id)
        if not cue.thai:
            cue.status = "failed"


def translate_chunk(ep: Episode, ids: list[int], backend: AIBackend, effort: str) -> float:
    try:
        res = backend.run_json(build_translate_request(ep, ids, effort))
    except AIError as exc:
        if exc.kind is AIErrorKind.TRUNCATED and len(ids) > MIN_CHUNK:
            mid = len(ids) // 2
            return (translate_chunk(ep, ids[:mid], backend, effort)
                    + translate_chunk(ep, ids[mid:], backend, effort))
        if exc.kind in _LOCAL_FAILURES:
            _mark_failed(ep, ids)
            return 0.0
        raise
    cost = res.cost_usd or 0.0
    got, missing = check_lines(ids, res.data["lines"])
    _apply(ep, got)
    if missing:
        try:
            res2 = backend.run_json(build_translate_request(ep, missing, effort))
            cost += res2.cost_usd or 0.0
            got2, missing = check_lines(missing, res2.data["lines"])
            _apply(ep, got2)
        except AIError as exc:
            if exc.kind not in _LOCAL_FAILURES:
                raise
        _mark_failed(ep, missing)
    return cost
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_translate.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add subtitle_translator/translate/translate.py tests/test_translate.py
git commit -m "feat: chunked translation with id checks, retry, split and failure marking"
```

---

### Task 8: Anthropic API backend

**Files:**
- Create: `subtitle_translator/ai/api_backend.py`
- Test: `tests/test_api_backend.py`

**Interfaces:**
- Consumes: Task 4 (`MODELS`, `cost_usd`), Task 5 (contract)
- Produces: `FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}`; `class ApiBackend` with `__init__(api_key: str, model: str, client=None)`, `name = "api"`, `run_json`, `estimate_cost`, `check`; helper `build_params(model, req) -> dict`.

Before writing code, read `python/claude-api/README.md` (Prompt Caching, Thinking & Effort, Stop Reasons, Refusal Fallbacks, Token Counting) and `python/claude-api/tool-use.md` (Structured outputs) in the claude-api skill. If any parameter below differs from those docs or from the installed SDK, follow the docs and update the test to match.

- [ ] **Step 1: Write the failing test**

`tests/test_api_backend.py`:

```python
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from subtitle_translator.ai.api_backend import ApiBackend, build_params
from subtitle_translator.ai.backend import AIErrorKind, AIRequest

REQ = AIRequest(system="SYS", context="CTX", task="TASK",
                schema={"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}},
                effort="high")


def message(text='{"ok": true}', stop="end_turn", model="claude-opus-5-5"):
    usage = SimpleNamespace(input_tokens=1000, output_tokens=500,
                            cache_read_input_tokens=2000, cache_creation_input_tokens=0)
    return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking=""),
                                    SimpleNamespace(type="text", text=text)],
                           stop_reason=stop, usage=usage, model=model)


class Stream:
    def __init__(self, result):
        self.result = result

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeMessages:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def stream(self, **kw):
        self.calls.append(kw)
        return Stream(self.result)

    def count_tokens(self, **kw):
        return SimpleNamespace(input_tokens=250_000)


def fake_client(result):
    beta_msgs = FakeMessages(result)
    msgs = FakeMessages(result)
    return SimpleNamespace(messages=msgs, beta=SimpleNamespace(messages=beta_msgs),
                           models=SimpleNamespace(retrieve=lambda m: SimpleNamespace(id=m)))


def status_error(cls, code, msg="x"):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls(message=msg, response=httpx.Response(code, request=req), body=None)


def test_build_params():
    p = build_params("claude-opus-5-5", REQ)
    assert p["model"] == "claude-opus-5-5" and p["max_tokens"] == 64000
    assert p["thinking"] == {"type": "adaptive"}
    assert p["output_config"]["effort"] == "high"
    assert p["output_config"]["format"] == {"type": "json_schema", "schema": REQ.schema}
    assert p["system"][0]["text"] == "SYS"
    assert p["system"][1] == {"type": "text", "text": "CTX", "cache_control": {"type": "ephemeral"}}
    assert p["messages"] == [{"role": "user", "content": "TASK"}]
    assert "temperature" not in p


def test_opus_uses_beta_fallback_and_prices_usage():
    client = fake_client(message())
    res = ApiBackend("k", "claude-opus-5-5", client=client).run_json(REQ)
    call = client.beta.messages.calls[0]
    assert call["betas"] == ["server-side-fallback-2026-07-01"] and call["fallbacks"] == "default"
    assert client.messages.calls == []
    assert res.data == {"ok": True}
    assert res.usage.cache_read == 2000
    assert res.cost_usd == pytest.approx((1000 * 4 + 500 * 20 + 2000 * 0.2) / 1e6)


def test_haiku_has_no_fallback():
    client = fake_client(message(model="claude-haiku-5-5"))
    ApiBackend("k", "claude-haiku-5-5", client=client).run_json(REQ)
    assert client.beta.messages.calls == []
    assert "fallbacks" not in client.messages.calls[0]


@pytest.mark.parametrize("msg, kind", [
    (message(stop="refusal"), AIErrorKind.REFUSAL),
    (message(stop="max_tokens"), AIErrorKind.TRUNCATED),
    (message(text="not json"), AIErrorKind.INVALID_OUTPUT),
    (message(text='{"nope": 1}'), AIErrorKind.INVALID_OUTPUT),
])
def test_bad_responses(msg, kind):
    with pytest.raises(Exception) as e:
        ApiBackend("k", "claude-opus-5-5", client=fake_client(msg)).run_json(REQ)
    assert e.value.kind is kind


@pytest.mark.parametrize("exc, kind", [
    (status_error(anthropic.AuthenticationError, 401), AIErrorKind.AUTH),
    (status_error(anthropic.RateLimitError, 429), AIErrorKind.RATE_LIMIT),
    (status_error(anthropic.BadRequestError, 400, "Your credit balance is too low"), AIErrorKind.QUOTA),
    (status_error(anthropic.BadRequestError, 400, "bad"), AIErrorKind.BAD_REQUEST),
    (status_error(anthropic.InternalServerError, 500), AIErrorKind.NETWORK),
])
def test_sdk_errors_are_mapped(exc, kind):
    with pytest.raises(Exception) as e:
        ApiBackend("k", "claude-opus-5-5", client=fake_client(exc)).run_json(REQ)
    assert e.value.kind is kind


def test_estimate_and_check():
    b = ApiBackend("k", "claude-opus-5-5", client=fake_client(message()))
    assert b.estimate_cost([REQ, REQ], 100_000) == pytest.approx((500_000 * 4 + 100_000 * 20) / 1e6)
    assert b.check() == "เชื่อมต่อ API ได้"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api_backend.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

`subtitle_translator/ai/api_backend.py`:

```python
from __future__ import annotations

import json
import logging

import anthropic

from ..core.pricing import MODELS, cost_usd
from .backend import AIError, AIErrorKind, AIRequest, AIResult, Usage, validate_json

log = logging.getLogger(__name__)
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def build_params(model: str, req: AIRequest) -> dict:
    return {
        "model": model,
        "max_tokens": 64000,
        "system": [
            {"type": "text", "text": req.system},
            {"type": "text", "text": req.context, "cache_control": {"type": "ephemeral"}},
        ],
        "messages": [{"role": "user", "content": req.task}],
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": req.effort,
                          "format": {"type": "json_schema", "schema": req.schema}},
    }


class ApiBackend:
    name = "api"

    def __init__(self, api_key: str, model: str, client=None):
        self.model = model
        self.client = client or anthropic.Anthropic(api_key=api_key, max_retries=4)

    def _send(self, params: dict):
        if self.model in FALLBACK_MODELS:
            with self.client.beta.messages.stream(**params, betas=[FALLBACK_BETA], fallbacks="default") as s:
                return s.get_final_message()
        with self.client.messages.stream(**params) as s:
            return s.get_final_message()

    def run_json(self, req: AIRequest) -> AIResult:
        try:
            msg = self._send(build_params(self.model, req))
        except anthropic.AuthenticationError as exc:
            raise AIError(AIErrorKind.AUTH, "API key ไม่ถูกต้อง") from exc
        except anthropic.PermissionDeniedError as exc:
            raise AIError(AIErrorKind.AUTH, "API key นี้ไม่มีสิทธิ์ใช้โมเดลนี้") from exc
        except anthropic.RateLimitError as exc:
            raise AIError(AIErrorKind.RATE_LIMIT, "เรียก API ถี่เกินไป ลองใหม่อีกครู่") from exc
        except anthropic.BadRequestError as exc:
            if "credit balance" in str(exc).lower():
                raise AIError(AIErrorKind.QUOTA, "เครดิต API หมด — เติมเงินที่ Claude Console") from exc
            raise AIError(AIErrorKind.BAD_REQUEST, f"คำขอไม่ถูกต้อง: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise AIError(AIErrorKind.NETWORK, f"เซิร์ฟเวอร์ตอบ {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise AIError(AIErrorKind.NETWORK, "เชื่อมต่ออินเทอร์เน็ตไม่ได้") from exc

        if msg.stop_reason == "refusal":
            raise AIError(AIErrorKind.REFUSAL, "Claude ปฏิเสธการแปลช่วงนี้")
        if msg.stop_reason == "max_tokens":
            raise AIError(AIErrorKind.TRUNCATED, "คำตอบยาวเกินและถูกตัด")
        text = "".join(b.text for b in msg.content if b.type == "text")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AIError(AIErrorKind.INVALID_OUTPUT, "คำตอบไม่ใช่ JSON") from exc
        validate_json(data, req.schema)

        u = msg.usage
        usage = Usage(input_tokens=u.input_tokens, output_tokens=u.output_tokens,
                      cache_read=u.cache_read_input_tokens or 0,
                      cache_write=u.cache_creation_input_tokens or 0)
        priced = msg.model if msg.model in MODELS else self.model
        cost = cost_usd(priced, usage.input_tokens, usage.output_tokens, usage.cache_read, usage.cache_write)
        log.info("api call model=%s in=%d out=%d cache_read=%d cost=%.4f",
                 priced, usage.input_tokens, usage.output_tokens, usage.cache_read, cost)
        return AIResult(data, usage, cost)

    def estimate_cost(self, reqs: list[AIRequest], expected_output_tokens: int) -> float | None:
        total_in = 0
        for req in reqs:
            p = build_params(self.model, req)
            counted = self.client.messages.count_tokens(model=p["model"], system=p["system"],
                                                        messages=p["messages"])
            total_in += counted.input_tokens
        return cost_usd(self.model, total_in, expected_output_tokens)

    def check(self) -> str:
        try:
            self.client.models.retrieve(self.model)
        except anthropic.AuthenticationError as exc:
            raise AIError(AIErrorKind.AUTH, "API key ไม่ถูกต้อง") from exc
        except anthropic.APIConnectionError as exc:
            raise AIError(AIErrorKind.NETWORK, "เชื่อมต่ออินเทอร์เน็ตไม่ได้") from exc
        return "เชื่อมต่อ API ได้"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_api_backend.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add subtitle_translator/ai/api_backend.py tests/test_api_backend.py
git commit -m "feat: Anthropic API backend with structured output, caching, fallback"
```

---

### Task 9: Claude Code backend and backend factory

**Files:**
- Create: `subtitle_translator/ai/claude_code_backend.py`, `subtitle_translator/ai/factory.py`
- Test: `tests/test_claude_code_backend.py`

**Interfaces:**
- Consumes: Task 5 (contract, `extract_json`, `validate_json`), Task 4 (`Settings`, `get_api_key`), Task 8 (`ApiBackend`), Task 3 (`no_window`)
- Produces: `MODEL_ALIAS = {"claude-opus-5-5": "opus", "claude-sonnet-5-5": "sonnet", "claude-haiku-5-5": "haiku"}`; `find_claude() -> str | None`; `class ClaudeCodeBackend(model, exe=None, runner=subprocess.run)` with `name = "claude_code"`, `run_json`, `estimate_cost` (always `None`), `check`; `make_backend(settings) -> AIBackend`.

- [ ] **Step 1: Confirm the CLI contract on this machine**

Run: `claude --help`
Confirm that `-p/--print`, `--output-format` (with `json`) and `--model` exist. Then run:

```bash
echo Reply with exactly OK | claude -p --output-format json --model sonnet
```

Expected: one JSON object containing at least `"result"` and `"is_error"`. If the field names differ, change `_call` below and the test fixtures to the real names before continuing.

- [ ] **Step 2: Write the failing test**

`tests/test_claude_code_backend.py`:

```python
import json
import subprocess

import pytest

from subtitle_translator.ai.backend import AIErrorKind, AIRequest
from subtitle_translator.ai.claude_code_backend import ClaudeCodeBackend

SCHEMA = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}
REQ = AIRequest(system="SYS", context="CTX", task="TASK", schema=SCHEMA)


def proc(result, is_error=False, code=0, stderr=""):
    out = json.dumps({"type": "result", "is_error": is_error, "result": result})
    return subprocess.CompletedProcess(args=[], returncode=code,
                                       stdout=out.encode("utf-8"), stderr=stderr.encode("utf-8"))


class Runner:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, args, **kw):
        self.calls.append((args, kw))
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_success_with_code_fence_and_prompt_shape():
    runner = Runner(proc('```json\n{"ok": true}\n```'))
    b = ClaudeCodeBackend("claude-opus-5-5", exe="claude", runner=runner)
    res = b.run_json(REQ)
    assert res.data == {"ok": True} and res.cost_usd is None
    args, kw = runner.calls[0]
    assert args[:6] == ["claude", "-p", "--output-format", "json", "--model", "opus"]
    prompt = kw["input"].decode("utf-8")
    assert prompt.index("SYS") < prompt.index("CTX") < prompt.index("TASK")
    assert '"required": ["ok"]' in prompt
    assert kw["timeout"] == 600


def test_invalid_json_is_retried_once():
    runner = Runner(proc("sorry"), proc('{"ok": false}'))
    res = ClaudeCodeBackend("claude-sonnet-5-5", exe="claude", runner=runner).run_json(REQ)
    assert res.data == {"ok": False}
    assert "previous reply was invalid" in runner.calls[1][1]["input"].decode("utf-8")


def test_invalid_twice_raises():
    runner = Runner(proc("x"), proc("y"))
    with pytest.raises(Exception) as e:
        ClaudeCodeBackend("claude-opus-5-5", exe="claude", runner=runner).run_json(REQ)
    assert e.value.kind is AIErrorKind.INVALID_OUTPUT


def test_usage_limit_maps_to_quota_with_reset_time():
    runner = Runner(proc("Claude usage limit reached. Your limit resets at 7pm (Asia/Bangkok).", is_error=True, code=1))
    with pytest.raises(Exception) as e:
        ClaudeCodeBackend("claude-opus-5-5", exe="claude", runner=runner).run_json(REQ)
    assert e.value.kind is AIErrorKind.QUOTA
    assert e.value.retry_after == "7pm (Asia/Bangkok)"


def test_not_logged_in_and_not_installed():
    runner = Runner(proc("Invalid API key · Please run /login", is_error=True, code=1))
    with pytest.raises(Exception) as e:
        ClaudeCodeBackend("claude-opus-5-5", exe="claude", runner=runner).run_json(REQ)
    assert e.value.kind is AIErrorKind.AUTH
    runner = Runner(FileNotFoundError())
    with pytest.raises(Exception) as e:
        ClaudeCodeBackend("claude-opus-5-5", exe="claude", runner=runner).run_json(REQ)
    assert e.value.kind is AIErrorKind.NOT_INSTALLED


def test_estimate_is_none_and_check():
    b = ClaudeCodeBackend("claude-opus-5-5", exe="claude", runner=Runner(proc("OK")))
    assert b.estimate_cost([REQ], 1000) is None
    assert b.check() == "เชื่อมต่อ Claude Code ได้"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_claude_code_backend.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Write the implementation**

`subtitle_translator/ai/claude_code_backend.py`:

```python
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..core.media import no_window
from .backend import AIError, AIErrorKind, AIRequest, AIResult, Usage, validate_json
from .json_repair import extract_json

MODEL_ALIAS = {"claude-opus-5-5": "opus", "claude-sonnet-5-5": "sonnet", "claude-haiku-5-5": "haiku"}
_LIMIT = re.compile(r"usage limit|rate limit|limit reached|out of extra usage", re.I)
_RESET = re.compile(r"resets?\s+(?:at\s+)?(.+?)(?:\.\s|\.$|$)", re.I | re.M)
_LOGIN = re.compile(r"/login|log ?in|not authenticated|invalid api key", re.I)


def find_claude() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    for cand in (Path.home() / ".local" / "bin" / "claude.exe",
                 Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd"):
        if cand.exists():
            return str(cand)
    return None


class ClaudeCodeBackend:
    name = "claude_code"

    def __init__(self, model: str, exe: str | None = None, runner=subprocess.run):
        self.model = model
        self.exe = exe
        self.runner = runner
        self.workdir = tempfile.mkdtemp(prefix="subtrans-cc-")   # keeps project CLAUDE.md files out

    def _prompt(self, req: AIRequest, error_note: str | None = None) -> str:
        parts = [req.system, req.context, req.task,
                 "Reply with ONLY one JSON value that matches this JSON Schema. "
                 "No prose, no code fences, do not use any tools.\n" + json.dumps(req.schema, ensure_ascii=False)]
        if error_note:
            parts.append(error_note)
        return "\n\n".join(parts)

    def _call(self, prompt: str) -> str:
        exe = self.exe or find_claude()
        if not exe:
            raise AIError(AIErrorKind.NOT_INSTALLED, "ไม่พบ Claude Code — ติดตั้งแล้วล็อกอินด้วยบัญชี Max ก่อน")
        args = [exe, "-p", "--output-format", "json", "--model", MODEL_ALIAS.get(self.model, "opus")]
        try:
            r = self.runner(args, input=prompt.encode("utf-8"), capture_output=True,
                            timeout=600, cwd=self.workdir, **no_window())
        except FileNotFoundError as exc:
            raise AIError(AIErrorKind.NOT_INSTALLED, "ไม่พบ Claude Code — ติดตั้งแล้วล็อกอินด้วยบัญชี Max ก่อน") from exc
        except subprocess.TimeoutExpired as exc:
            raise AIError(AIErrorKind.NETWORK, "Claude Code ตอบช้าเกิน 10 นาที") from exc
        out = r.stdout.decode("utf-8", "replace")
        err = r.stderr.decode("utf-8", "replace")
        try:
            outer = json.loads(out)
        except json.JSONDecodeError:
            outer = None
        result = outer.get("result", "") if isinstance(outer, dict) else out
        is_error = r.returncode != 0 or (isinstance(outer, dict) and bool(outer.get("is_error")))
        if is_error:
            blob = f"{result}\n{err}"
            if _LIMIT.search(blob):
                m = _RESET.search(blob)
                raise AIError(AIErrorKind.QUOTA, "โควตา Claude Max เต็ม",
                              retry_after=m.group(1).strip() if m else None)
            if _LOGIN.search(blob):
                raise AIError(AIErrorKind.AUTH,
                              "Claude Code ยังไม่ได้ล็อกอิน — เปิด Terminal แล้วพิมพ์ claude เพื่อล็อกอิน")
            raise AIError(AIErrorKind.BAD_REQUEST, blob.strip()[:500] or f"claude ออกด้วยรหัส {r.returncode}")
        return result

    def _parse(self, text: str, schema: dict):
        data = extract_json(text)          # ValueError if none
        validate_json(data, schema)        # AIError(INVALID_OUTPUT) if wrong shape
        return data

    def run_json(self, req: AIRequest) -> AIResult:
        text = self._call(self._prompt(req))
        try:
            data = self._parse(text, req.schema)
        except (ValueError, AIError) as first:
            note = f"Your previous reply was invalid ({first}). Return only valid JSON for the schema."
            text = self._call(self._prompt(req, note))
            try:
                data = self._parse(text, req.schema)
            except ValueError as exc:
                raise AIError(AIErrorKind.INVALID_OUTPUT, "Claude Code ตอบไม่เป็น JSON") from exc
        return AIResult(data, Usage(), None)

    def estimate_cost(self, reqs: list[AIRequest], expected_output_tokens: int) -> float | None:
        return None

    def check(self) -> str:
        self._call("Reply with exactly: OK")
        return "เชื่อมต่อ Claude Code ได้"
```

`subtitle_translator/ai/factory.py`:

```python
from __future__ import annotations

from ..core.settings import Settings, get_api_key
from .api_backend import ApiBackend
from .backend import AIBackend, AIError, AIErrorKind
from .claude_code_backend import ClaudeCodeBackend


def make_backend(settings: Settings) -> AIBackend:
    if settings.backend == "claude_code":
        return ClaudeCodeBackend(settings.model)
    key = get_api_key()
    if not key:
        raise AIError(AIErrorKind.AUTH, "ยังไม่ได้ใส่ API key — ไปที่หน้าตั้งค่า")
    return ApiBackend(key, settings.model)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_claude_code_backend.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add subtitle_translator/ai tests/test_claude_code_backend.py
git commit -m "feat: Claude Code (Max) backend and backend factory"
```

---

### Task 10: Pipeline (prepare, run with resume, estimate)

**Files:**
- Create: `subtitle_translator/translate/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: Tasks 1–7, 9
- Produces: `class Stopped(Exception)`; `OUTPUT_TOKENS_PER_CUE = 90` (analysis + Thai text + adaptive thinking); `prepare_project(video, source: SubTrack | Path, settings, cache: Path) -> Project` (reuses an existing `.stproj.json`); `run_project(project, backend, settings, progress: Callable[[str, float], None], should_stop: Callable[[], bool], save=save_project) -> Project`; `estimate_project_cost(project, backend) -> float | None`.

- [ ] **Step 1: Write the failing test**

`tests/test_pipeline.py`:

```python
import pytest

from fakes import FakeBackend
from test_translate import echo
from subtitle_translator.ai.backend import AIError, AIErrorKind
from subtitle_translator.core.project_store import load_project
from subtitle_translator.core.settings import Settings
from subtitle_translator.translate.pipeline import (
    Stopped, estimate_project_cost, prepare_project, run_project,
)

SRT = "".join(f"{i}\n00:00:{i:02d},000 --> 00:00:{i:02d},900\nline {i}\n\n" for i in range(1, 26))
ANALYSIS = {"summary": "s", "characters": [], "cues": []}


@pytest.fixture
def video(tmp_path):
    v = tmp_path / "EP01.mkv"
    v.write_bytes(b"")
    (tmp_path / "EP01.srt").write_text(SRT, encoding="utf-8")
    return v


def test_prepare_from_sidecar_then_reuse(video, tmp_path):
    s = Settings(chunk_size=10)
    p = prepare_project(video, video.with_suffix(".srt"), s, tmp_path / "cache")
    assert len(p.episode.cues) == 25 and p.episode.src_lang == "en"
    assert p.chunk_size == 10 and p.stage == "extracted"
    p.cost_usd = 1.5
    from subtitle_translator.core.project_store import save_project
    save_project(p)
    again = prepare_project(video, video.with_suffix(".srt"), s, tmp_path / "cache")
    assert again.cost_usd == 1.5


def test_run_full_then_done(video, tmp_path):
    s = Settings(chunk_size=10)
    p = prepare_project(video, video.with_suffix(".srt"), s, tmp_path / "cache")
    seen = []
    backend = FakeBackend([ANALYSIS, echo, echo, echo])
    run_project(p, backend, s, lambda stage, f: seen.append((stage, f)), lambda: False)
    assert p.stage == "done" and p.done_chunks == [0, 1, 2]
    assert all(c.thai for c in p.episode.cues)
    assert p.cost_usd == pytest.approx(0.04)
    assert seen[-1] == ("แปล", 1.0)
    assert load_project(video).stage == "done"


def test_resume_after_stop_and_after_crash(video, tmp_path):
    s = Settings(chunk_size=10)
    p = prepare_project(video, video.with_suffix(".srt"), s, tmp_path / "cache")

    def stop_after_first_chunk():
        return len(p.done_chunks) >= 1

    with pytest.raises(Stopped):
        run_project(p, FakeBackend([ANALYSIS, echo]), s, lambda *a: None, stop_after_first_chunk)
    saved = load_project(video)
    assert saved.stage == "translating" and saved.done_chunks == [0]

    with pytest.raises(AIError):
        run_project(saved, FakeBackend([AIError(AIErrorKind.NETWORK, "down")]), s, lambda *a: None, lambda: False)
    saved = load_project(video)
    assert saved.done_chunks == [0]

    backend = FakeBackend([echo, echo])
    run_project(saved, backend, s, lambda *a: None, lambda: False)
    assert len(backend.requests) == 2 and saved.stage == "done"


def test_estimate(video, tmp_path):
    s = Settings(chunk_size=10)
    p = prepare_project(video, video.with_suffix(".srt"), s, tmp_path / "cache")
    assert estimate_project_cost(p, FakeBackend([])) == 0.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

`subtitle_translator/translate/pipeline.py`:

```python
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from ..ai.backend import AIBackend
from ..core.media import SubTrack, extract_sub, lang_of
from ..core.models import Episode, Project
from ..core.project_store import load_project, save_project
from ..core.settings import Settings
from ..core.subio import guess_lang, load_cues
from .analyze import analyze, build_analyze_request
from .translate import build_translate_request, make_chunks, translate_chunk

log = logging.getLogger(__name__)
OUTPUT_TOKENS_PER_CUE = 90


class Stopped(Exception):
    pass


def prepare_project(video: str | Path, source: SubTrack | Path, settings: Settings, cache: Path) -> Project:
    existing = load_project(video)
    if existing is not None:
        return existing
    if isinstance(source, SubTrack):
        sub_path = extract_sub(video, source, cache)
        cues, _ = load_cues(sub_path)
        lang = lang_of(source) or guess_lang(cues)
    else:
        sub_path = Path(source)
        cues, _ = load_cues(sub_path)
        lang = guess_lang(cues)
    ep = Episode(video_path=str(video), source=str(sub_path), src_lang=lang, cues=cues)
    project = Project(episode=ep, backend=settings.backend, model=settings.model,
                      chunk_size=settings.chunk_size)
    save_project(project)
    return project


def run_project(project: Project, backend: AIBackend, settings: Settings,
                progress: Callable[[str, float], None], should_stop: Callable[[], bool],
                save: Callable[[Project], object] = save_project) -> Project:
    ep = project.episode
    project.backend, project.model = backend.name, backend.model
    if project.stage == "extracted":
        progress("วิเคราะห์ตัวละคร", 0.0)
        res = analyze(ep, backend, settings.effort_analyze)
        project.cost_usd += res.cost_usd or 0.0
        project.stage = "analyzed"
        save(project)
    if project.stage in ("analyzed", "translating"):
        project.stage = "translating"
        chunks = make_chunks(ep.cues, project.chunk_size)
        for n, ids in enumerate(chunks):
            if n in project.done_chunks:
                continue
            if should_stop():
                save(project)
                raise Stopped()
            progress("แปล", n / max(len(chunks), 1))
            project.cost_usd += translate_chunk(ep, ids, backend, settings.effort_translate)
            project.done_chunks.append(n)
            save(project)
        project.stage = "done"
        save(project)
        progress("แปล", 1.0)
    log.info("project %s done, cost %.4f", ep.video_path, project.cost_usd)
    return project


def estimate_project_cost(project: Project, backend: AIBackend) -> float | None:
    ep = project.episode
    reqs = [build_analyze_request(ep, "high")]
    reqs += [build_translate_request(ep, ids, "medium") for ids in make_chunks(ep.cues, project.chunk_size)]
    expected_out = OUTPUT_TOKENS_PER_CUE * sum(1 for c in ep.cues if not c.skip)
    return backend.estimate_cost(reqs, expected_out)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pipeline.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add subtitle_translator/translate/pipeline.py tests/test_pipeline.py
git commit -m "feat: per-file pipeline with checkpoint resume and cost estimate"
```

---

### Task 11: Export (.th.srt and .th.ass)

**Files:**
- Create: `subtitle_translator/export/__init__.py`, `subtitle_translator/export/srt_writer.py`, `subtitle_translator/export/ass_writer.py`
- Test: `tests/test_export.py`

**Interfaces:**
- Consumes: Task 1 models, Task 2 (`load_ssa`, `is_effect`), Task 4 (`Settings`)
- Produces: `srt_timestamp(t) -> str`; `write_srt(cues, path, colors: dict[str, str] | None = None) -> Path`; `write_ass(ep, path, font, size) -> Path`; `style_name(key) -> str`; `output_paths(video, out_dir: str | None) -> tuple[Path, Path]`; `export_project(project, settings, overwrite=False) -> tuple[Path, Path]` (raises `FileExistsError` when a target exists and `overwrite` is False).

- [ ] **Step 1: Write the failing test**

`tests/test_export.py`:

```python
import pysubs2
import pytest

from helpers import make_episode
from test_subio import ASS
from subtitle_translator.core.models import Character, Project
from subtitle_translator.core.settings import Settings
from subtitle_translator.core.subio import load_cues
from subtitle_translator.export import export_project, output_paths
from subtitle_translator.export.ass_writer import style_name, write_ass
from subtitle_translator.export.srt_writer import srt_timestamp, write_srt


def test_srt_timestamp():
    assert srt_timestamp(3725.4567) == "01:02:05,457"


def test_write_srt_bom_colors_and_fallback(tmp_path):
    ep = make_episode(3)
    ep.cues[0].thai, ep.cues[0].speaker = "พี่ ตื่นได้แล้ว!\nเดี๋ยวสาย", "haru"
    ep.cues[1].skip = True
    path = write_srt(ep.cues, tmp_path / "a.th.srt", colors={"haru": "#F472B6"})
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    assert text.startswith("1\n00:00:02,000 --> 00:00:03,500\n<font color=\"#F472B6\">พี่ ตื่นได้แล้ว!\nเดี๋ยวสาย</font>\n")
    assert "2\n00:00:06,000 --> 00:00:07,500\nline 3\n" in text
    assert "line 2" not in text


def test_write_ass_styles_per_character_and_keeps_effects(tmp_path):
    src = tmp_path / "orig.ass"
    src.write_text(ASS, encoding="utf-8")
    cues, _ = load_cues(src)
    ep = make_episode(1)
    ep.source, ep.cues = str(src), cues
    ep.characters = [Character(key="haru", name_src="ハル", name_th="ฮารุ", color="#F472B6")]
    cues[0].speaker, cues[0].thai = "haru", "พี่ ตื่นได้แล้ว!"
    cues[1].thai = "เสียงดัง\nขออีกห้านาที"
    out = write_ass(ep, tmp_path / "x.th.ass", "Leelawadee UI", 52)
    assert out.read_bytes().startswith(b"\xef\xbb\xbf")
    subs = pysubs2.load(str(out), encoding="utf-8-sig")
    st = subs.styles[style_name("haru")]
    assert (st.primarycolor.r, st.primarycolor.g, st.primarycolor.b) == (0xF4, 0x72, 0xB6)
    assert st.fontname == "Leelawadee UI"
    texts = [e.text for e in subs.events]
    assert "พี่ ตื่นได้แล้ว!" in texts
    assert "เสียงดัง\\Nขออีกห้านาที" in texts
    assert any("\\k20" in t for t in texts)              # karaoke line copied untouched
    assert subs.events[[e.text for e in subs.events].index("พี่ ตื่นได้แล้ว!")].style == style_name("haru")


def test_export_project_paths_and_overwrite(tmp_path):
    ep = make_episode(2)
    ep.video_path = str(tmp_path / "EP01.mkv")
    ep.source = str(tmp_path / "missing.srt")
    for c in ep.cues:
        c.thai = "ไทย"
    p = Project(episode=ep, backend="api", model="claude-opus-5-5")
    srt, ass = export_project(p, Settings())
    assert (srt.name, ass.name) == ("EP01.th.srt", "EP01.th.ass")
    with pytest.raises(FileExistsError):
        export_project(p, Settings())
    export_project(p, Settings(), overwrite=True)
    assert output_paths(ep.video_path, str(tmp_path / "out"))[0] == tmp_path / "out" / "EP01.th.srt"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_export.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

`subtitle_translator/export/srt_writer.py`:

```python
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
    path.write_text("\n".join(blocks), encoding="utf-8-sig")
    return path
```

`subtitle_translator/export/ass_writer.py`:

```python
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
```

`subtitle_translator/export/__init__.py`:

```python
from __future__ import annotations

from pathlib import Path

from ..core.models import Project
from ..core.settings import Settings
from .ass_writer import write_ass
from .srt_writer import write_srt


def output_paths(video: str | Path, out_dir: str | None) -> tuple[Path, Path]:
    v = Path(video)
    folder = Path(out_dir) if out_dir else v.parent
    return folder / f"{v.stem}.th.srt", folder / f"{v.stem}.th.ass"


def export_project(project: Project, settings: Settings, overwrite: bool = False) -> tuple[Path, Path]:
    ep = project.episode
    srt, ass = output_paths(ep.video_path, settings.output_dir)
    if not overwrite:
        for p in (srt, ass):
            if p.exists():
                raise FileExistsError(str(p))
    colors = {ch.key: ch.color for ch in ep.characters} if settings.srt_colors else None
    write_srt(ep.cues, srt, colors)
    write_ass(ep, ass, settings.ass_font, settings.ass_size)
    return srt, ass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_export.py -v`
Expected: 4 passed. If `pysubs2.Alignment` or `SSAStyle.copy()` is missing in the installed pysubs2, use `alignment=2` and `pysubs2.SSAStyle(**base.as_dict())` instead, then re-run.

- [ ] **Step 5: Commit**

```bash
git add subtitle_translator/export tests/test_export.py
git commit -m "feat: export Thai srt (BOM) and per-character colored ass"
```

---

### Task 12: GUI shell (theme, logging, worker, main window, entry point)

**Files:**
- Create: `subtitle_translator/core/logs.py`, `subtitle_translator/gui/theme.py`, `subtitle_translator/gui/worker.py`, `subtitle_translator/gui/main_window.py`, `subtitle_translator/app.py`, `subtitle_translator/__main__.py`
- Create (temporary stubs replaced in Tasks 13–15): `subtitle_translator/gui/settings_page.py`, `subtitle_translator/gui/queue_page.py`, `subtitle_translator/gui/review_page.py`
- Test: `tests/test_gui.py`

**Interfaces:**
- Consumes: Task 4 (`Settings`, `save_settings`, `log_dir`)
- Produces: `setup_logging() -> Path`; `COLORS: dict[str, str]`, `apply_theme(app)`; `class CallWorker(QThread)` with `__init__(fn: Callable[[], object])`, signals `done(object)`, `failed(object)` (the exception); `class MainWindow(QMainWindow)` with `__init__(settings, settings_path=None)`, attributes `queue_page`, `review_page`, `settings_page`, `stack`, methods `go(index)`, `show_review(project)`, `persist()`; page constructors used by MainWindow: `QueuePage(settings, persist)`, `ReviewPage(settings, persist)`, `SettingsPage(settings, persist)`; `QueuePage.open_review` signal `Signal(object)`; `ReviewPage.load(project)`; `main() -> int`.

- [ ] **Step 1: Write the failing test**

`tests/test_gui.py`:

```python
from subtitle_translator.core.settings import Settings
from subtitle_translator.gui.main_window import MainWindow
from subtitle_translator.gui.worker import CallWorker


def test_main_window_switches_pages(qtbot, tmp_path):
    w = MainWindow(Settings(), settings_path=tmp_path / "s.json")
    qtbot.addWidget(w)
    for i in range(3):
        w.go(i)
        assert w.stack.currentIndex() == i
    w.persist()
    assert (tmp_path / "s.json").exists()


def test_call_worker_reports_result_and_error(qtbot):
    ok = CallWorker(lambda: 42)
    with qtbot.waitSignal(ok.done, timeout=3000) as blocker:
        ok.start()
    assert blocker.args == [42]
    bad = CallWorker(lambda: 1 / 0)
    with qtbot.waitSignal(bad.failed, timeout=3000) as blocker:
        bad.start()
    assert isinstance(blocker.args[0], ZeroDivisionError)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gui.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write logging, theme and worker**

`subtitle_translator/core/logs.py`:

```python
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .settings import log_dir


def setup_logging() -> Path:
    path = log_dir() / "app.log"
    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)   # never log request headers
    return path
```

`subtitle_translator/gui/theme.py`:

```python
from __future__ import annotations

from PySide6.QtWidgets import QApplication

COLORS = {
    "bg": "#1C1C1C", "rail": "#171717", "card": "#2A2A2A", "card2": "#262626",
    "field": "#1E1E1E", "stroke": "#353535", "stroke2": "#3A3A3A",
    "text": "#F3F3F3", "text2": "#C8C8C8", "text3": "#A8A8A8",
    "accent": "#60CDFF", "on_accent": "#0B1A22", "selected": "#1F3442",
    "good": "#8FE3A8", "warn": "#F5C46B", "bad": "#F28B82",
}

QSS = """
QWidget { background: %(bg)s; color: %(text)s; font-family: "Leelawadee UI", "Segoe UI", "Yu Gothic UI"; font-size: 10pt; }
QFrame#rail { background: %(rail)s; border-right: 1px solid #2E2E2E; }
QToolButton[nav="true"] { background: transparent; color: %(text2)s; border: 0; border-radius: 8px; padding: 8px 4px; font-size: 8pt; }
QToolButton[nav="true"]:checked { background: #2D2D2D; color: %(text)s; }
QToolButton[nav="true"]:hover { background: #262626; }
QFrame[card="true"] { background: %(card)s; border: 1px solid %(stroke)s; border-radius: 10px; }
QFrame[card="true"] QLabel, QFrame[card="true"] QCheckBox, QFrame[card="true"] QRadioButton { background: transparent; }
QLabel[role="h1"] { font-size: 20pt; font-weight: 600; }
QLabel[role="h2"] { font-size: 12pt; font-weight: 600; }
QLabel[role="muted"] { color: %(text3)s; }
QPushButton { background: #323232; border: 1px solid %(stroke2)s; border-radius: 6px; padding: 7px 14px; min-height: 22px; }
QPushButton:hover { background: #3A3A3A; }
QPushButton:disabled { color: #6F6F6F; }
QPushButton[accent="true"] { background: %(accent)s; color: %(on_accent)s; border: 0; font-weight: 600; }
QPushButton[accent="true"]:hover { background: #7AD6FF; }
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox { background: %(field)s; border: 1px solid %(stroke2)s; border-bottom: 2px solid #4A4A4A; border-radius: 6px; padding: 6px 8px; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus { border-bottom: 2px solid %(accent)s; }
QComboBox QAbstractItemView { background: %(card)s; selection-background-color: %(selected)s; }
QTableWidget { background: %(card)s; border: 1px solid %(stroke)s; border-radius: 10px; gridline-color: #323232; selection-background-color: %(selected)s; selection-color: %(text)s; }
QHeaderView::section { background: %(card)s; color: %(text3)s; border: 0; border-bottom: 1px solid %(stroke)s; padding: 6px; }
QListWidget { background: transparent; border: 0; }
QProgressBar { background: #3A3A3A; border: 0; border-radius: 2px; max-height: 4px; }
QProgressBar::chunk { background: %(accent)s; border-radius: 2px; }
QScrollBar:vertical { background: transparent; width: 10px; }
QScrollBar::handle:vertical { background: #4A4A4A; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
""" % COLORS


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)
```

`subtitle_translator/gui/worker.py`:

```python
from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import QThread, Signal

log = logging.getLogger(__name__)


class CallWorker(QThread):
    done = Signal(object)
    failed = Signal(object)

    def __init__(self, fn: Callable[[], object], parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            self.done.emit(self._fn())
        except Exception as exc:  # reported to the GUI, never swallowed silently
            log.exception("background call failed")
            self.failed.emit(exc)
```

- [ ] **Step 4: Write temporary page stubs, main window and entry point**

Temporary stubs (each file replaced in Tasks 13, 14, 15):

`subtitle_translator/gui/settings_page.py`:

```python
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class SettingsPage(QWidget):
    def __init__(self, settings, persist, parent=None):
        super().__init__(parent)
        QVBoxLayout(self).addWidget(QLabel("ตั้งค่า"))
```

`subtitle_translator/gui/queue_page.py`:

```python
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class QueuePage(QWidget):
    open_review = Signal(object)

    def __init__(self, settings, persist, parent=None):
        super().__init__(parent)
        QVBoxLayout(self).addWidget(QLabel("คิวงาน"))
```

`subtitle_translator/gui/review_page.py`:

```python
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class ReviewPage(QWidget):
    def __init__(self, settings, persist, parent=None):
        super().__init__(parent)
        QVBoxLayout(self).addWidget(QLabel("ตรวจ/แก้"))

    def load(self, project):
        self.project = project
```

`subtitle_translator/gui/main_window.py`:

```python
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QMainWindow, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from ..core.settings import Settings, save_settings
from .queue_page import QueuePage
from .review_page import ReviewPage
from .settings_page import SettingsPage

NAV = [("คิวงาน", "☰"), ("ตรวจ/แก้", "✎"), ("ตั้งค่า", "⚙")]


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, settings_path: Path | None = None):
        super().__init__()
        self.settings = settings
        self.settings_path = settings_path
        self.setWindowTitle("แปลซับไทย")
        self.resize(1440, 900)

        rail = QFrame(objectName="rail")
        rail.setFixedWidth(76)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(6, 16, 6, 16)
        rail_layout.setSpacing(6)
        self.nav_group = QButtonGroup(self)
        self.nav_buttons: list[QToolButton] = []
        for i, (label, glyph) in enumerate(NAV):
            b = QToolButton(text=f"{glyph}\n{label}", checkable=True)
            b.setProperty("nav", True)
            b.setFixedSize(64, 56)
            b.setToolButtonStyle(Qt.ToolButtonTextOnly)
            b.setAccessibleName(label)
            self.nav_group.addButton(b, i)
            self.nav_buttons.append(b)
            if i == 2:
                rail_layout.addStretch(1)
            rail_layout.addWidget(b)
        self.nav_group.idClicked.connect(self.go)

        self.queue_page = QueuePage(settings, self.persist)
        self.review_page = ReviewPage(settings, self.persist)
        self.settings_page = SettingsPage(settings, self.persist)
        self.stack = QStackedWidget()
        for page in (self.queue_page, self.review_page, self.settings_page):
            self.stack.addWidget(page)
        self.queue_page.open_review.connect(self.show_review)

        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(rail)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        self.go(0)

    def go(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.nav_buttons[index].setChecked(True)

    def show_review(self, project) -> None:
        self.review_page.load(project)
        self.go(1)

    def persist(self) -> None:
        save_settings(self.settings, self.settings_path)
```

`subtitle_translator/app.py`:

```python
from __future__ import annotations

import logging
import sys
import traceback

from PySide6.QtWidgets import QApplication, QMessageBox

from .core.logs import setup_logging
from .core.media import MediaError, tool_path
from .core.settings import load_settings
from .gui.main_window import MainWindow
from .gui.theme import apply_theme

log = logging.getLogger(__name__)


def _excepthook(exc_type, exc, tb):
    log.error("unhandled error", exc_info=(exc_type, exc, tb))
    box = QMessageBox(QMessageBox.Critical, "เกิดข้อผิดพลาด", f"{exc_type.__name__}: {exc}")
    box.setDetailedText("".join(traceback.format_exception(exc_type, exc, tb)))
    box.exec()


def main() -> int:
    log_path = setup_logging()
    app = QApplication(sys.argv)
    apply_theme(app)
    sys.excepthook = _excepthook
    try:
        tool_path("ffmpeg")
        tool_path("ffprobe")
    except MediaError as exc:
        QMessageBox.warning(None, "ไม่พบ ffmpeg", f"{exc}\n\nlog: {log_path}")
    window = MainWindow(load_settings())
    window.show()
    return app.exec()
```

`subtitle_translator/__main__.py`:

```python
import sys

from subtitle_translator.app import main

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run tests to verify they pass, then launch once**

Run: `pytest tests/test_gui.py -v`
Expected: 2 passed

Run: `python -m subtitle_translator`
Expected: a dark window with the left rail (คิวงาน / ตรวจ/แก้ / ตั้งค่า); clicking each switches the page.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: GUI shell with dark theme, nav rail, background worker, logging"
```

---

### Task 13: Settings page

**Files:**
- Modify (replace stub): `subtitle_translator/gui/settings_page.py`
- Test: `tests/test_gui.py` (append)

**Interfaces:**
- Consumes: Task 4 (`Settings`, `MODELS`, `get_api_key`, `set_api_key`), Task 9 (`ApiBackend`, `ClaudeCodeBackend`, `find_claude`), Task 12 (`CallWorker`, `COLORS`)
- Produces: `SettingsPage(settings, persist)` with attributes `backend_api: QRadioButton`, `backend_cc: QRadioButton`, `api_box: QFrame`, `cc_box: QFrame`, `key_edit: QLineEdit`, `model_buttons: dict[str, QRadioButton]`, `status_label: QLabel`, `chunk_spin: QSpinBox`, `srt_colors: QCheckBox`; signal `changed = Signal()` emitted after every persisted change.

- [ ] **Step 1: Write the failing test** (append to `tests/test_gui.py`)

```python
from subtitle_translator.gui.settings_page import SettingsPage


def test_settings_page_switches_backend_and_persists(qtbot):
    saved = []
    s = Settings()
    page = SettingsPage(s, lambda: saved.append(True))
    qtbot.addWidget(page)
    page.show()
    assert page.api_box.isVisible() and not page.cc_box.isVisible()
    page.backend_cc.click()
    assert s.backend == "claude_code" and saved
    assert page.cc_box.isVisible() and not page.api_box.isVisible()
    page.model_buttons["claude-sonnet-5-5"].click()
    assert s.model == "claude-sonnet-5-5"
    page.chunk_spin.setValue(100)
    assert s.chunk_size == 100
    page.srt_colors.click()
    assert s.srt_colors is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gui.py::test_settings_page_switches_backend_and_persists -v`
Expected: FAIL with `AttributeError: 'SettingsPage' object has no attribute 'api_box'`

- [ ] **Step 3: Write the implementation**

Replace `subtitle_translator/gui/settings_page.py`:

```python
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QRadioButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from ..ai.api_backend import ApiBackend
from ..ai.claude_code_backend import ClaudeCodeBackend, find_claude
from ..core.pricing import MODELS
from ..core.settings import Settings, get_api_key, set_api_key
from .theme import COLORS
from .worker import CallWorker

EFFORTS = ["low", "medium", "high", "xhigh", "max"]


def card() -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setProperty("card", True)
    lay = QVBoxLayout(f)
    lay.setContentsMargins(18, 16, 18, 16)
    lay.setSpacing(10)
    return f, lay


def label(text: str, role: str | None = None) -> QLabel:
    lb = QLabel(text)
    lb.setWordWrap(True)
    if role:
        lb.setProperty("role", role)
    return lb


class SettingsPage(QWidget):
    changed = Signal()

    def __init__(self, settings: Settings, persist, parent=None):
        super().__init__(parent)
        self.s = settings
        self._persist = persist
        self._worker: CallWorker | None = None

        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(32, 28, 32, 28)
        col.setSpacing(8)
        col.addWidget(label("ตั้งค่า", "h1"))

        # --- backend choice ---
        col.addWidget(label("วิธีเชื่อมต่อ Claude", "h2"))
        choice, choice_lay = card()
        self.backend_api = QRadioButton("Anthropic API key  (แนะนำ) — จ่ายตามที่ใช้จริง เห็นราคาก่อนแปล ไม่ติดโควตา")
        self.backend_cc = QRadioButton("Claude Code (บัญชี Claude Max) — ไม่มีค่า API เพิ่ม แต่ติดโควตารอบ 5 ชม./รายสัปดาห์ และช้ากว่า")
        group = QButtonGroup(self)
        group.addButton(self.backend_api)
        group.addButton(self.backend_cc)
        choice_lay.addWidget(self.backend_api)
        choice_lay.addWidget(self.backend_cc)
        col.addWidget(choice)

        # --- API key box ---
        self.api_box, api_lay = card()
        api_lay.addWidget(label("Anthropic API key", "h2"))
        api_lay.addWidget(label("เก็บใน Windows Credential Manager ไม่เขียนลงไฟล์", "muted"))
        row = QHBoxLayout()
        self.key_edit = QLineEdit(echoMode=QLineEdit.Password, placeholderText="sk-ant-...")
        self.key_edit.setAccessibleName("API key")
        if get_api_key():
            self.key_edit.setPlaceholderText("บันทึก key ไว้แล้ว — วางใหม่เพื่อเปลี่ยน")
        show = QPushButton("แสดง", checkable=True)
        show.toggled.connect(lambda on: self.key_edit.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        save_test = QPushButton("บันทึกและทดสอบ")
        save_test.setProperty("accent", True)
        save_test.clicked.connect(self._save_and_test_key)
        row.addWidget(self.key_edit, 1)
        row.addWidget(show)
        row.addWidget(save_test)
        api_lay.addLayout(row)
        col.addWidget(self.api_box)

        # --- Claude Code box ---
        self.cc_box, cc_lay = card()
        cc_lay.addWidget(label("Claude Code บนเครื่องนี้", "h2"))
        cc_lay.addWidget(label("โปรแกรมสั่งงานผ่านคำสั่ง claude -p และใช้โควตาของบัญชี Max", "muted"))
        self.cc_path = label("")
        cc_lay.addWidget(self.cc_path)
        cc_check = QPushButton("ตรวจการเชื่อมต่อ")
        cc_check.clicked.connect(self._test_cc)
        cc_lay.addWidget(cc_check)
        warn = label("ใช้กับบัญชีของคุณบนเครื่องของคุณเท่านั้น ห้ามแจกหรือขายโปรแกรมที่ตั้งค่าแบบนี้ "
                     "และควรอ่านเงื่อนไขของ Claude Max ประกอบ")
        warn.setStyleSheet(f"color: {COLORS['warn']}; background: #3D2F14; border-radius: 6px; padding: 8px;")
        cc_lay.addWidget(warn)
        col.addWidget(self.cc_box)

        self.status_label = label("")
        col.addWidget(self.status_label)

        # --- model + translation options ---
        col.addWidget(label("การแปล", "h2"))
        models, models_lay = card()
        self.model_buttons: dict[str, QRadioButton] = {}
        mgroup = QButtonGroup(self)
        for mid, info in MODELS.items():
            tag = "  · แนะนำ" if info.recommended else ""
            b = QRadioButton(f"{info.label}{tag}   ${info.input:g} / ${info.output:g} ต่อ 1 ล้าน token — {info.note}")
            b.setChecked(mid == self.s.model)
            b.toggled.connect(lambda on, m=mid: on and self._set("model", m))
            mgroup.addButton(b)
            models_lay.addWidget(b)
            self.model_buttons[mid] = b
        form = QFormLayout()
        self.effort_a = QComboBox()
        self.effort_t = QComboBox()
        for box, key in ((self.effort_a, "effort_analyze"), (self.effort_t, "effort_translate")):
            box.addItems(EFFORTS)
            box.setCurrentText(getattr(self.s, key))
            box.currentTextChanged.connect(lambda v, k=key: self._set(k, v))
        self.chunk_spin = QSpinBox(minimum=40, maximum=150, singleStep=10, value=self.s.chunk_size)
        self.chunk_spin.valueChanged.connect(lambda v: self._set("chunk_size", v))
        self.lang_box = QComboBox()
        self.lang_box.addItems(["ญี่ปุ่นก่อน แล้วอังกฤษ", "อังกฤษก่อน แล้วญี่ปุ่น"])
        self.lang_box.setCurrentIndex(0 if self.s.prefer_langs[:1] == ["ja"] else 1)
        self.lang_box.currentIndexChanged.connect(
            lambda i: self._set("prefer_langs", ["ja", "en"] if i == 0 else ["en", "ja"]))
        form.addRow("ความละเอียดตอนวิเคราะห์ตัวละคร", self.effort_a)
        form.addRow("ความละเอียดตอนแปล", self.effort_t)
        form.addRow("จำนวนบรรทัดต่อการแปล 1 ครั้ง", self.chunk_spin)
        form.addRow("ภาษาต้นฉบับที่เลือกก่อน", self.lang_box)
        models_lay.addLayout(form)
        col.addWidget(models)

        # --- output ---
        col.addWidget(label("ไฟล์ที่ส่งออก", "h2"))
        out, out_lay = card()
        out_row = QHBoxLayout()
        self.out_label = label(self.s.output_dir or "ข้างไฟล์วิดีโอ")
        pick = QPushButton("เลือกโฟลเดอร์…")
        pick.clicked.connect(self._pick_dir)
        reset = QPushButton("ข้างไฟล์วิดีโอ")
        reset.clicked.connect(lambda: self._set_out(None))
        out_row.addWidget(self.out_label, 1)
        out_row.addWidget(pick)
        out_row.addWidget(reset)
        out_lay.addLayout(out_row)
        oform = QFormLayout()
        self.font_box = QComboBox(editable=True)
        self.font_box.addItems(["Leelawadee UI", "Tahoma", "Noto Sans Thai"])
        self.font_box.setCurrentText(self.s.ass_font)
        self.font_box.currentTextChanged.connect(lambda v: self._set("ass_font", v))
        self.size_spin = QSpinBox(minimum=24, maximum=120, value=self.s.ass_size)
        self.size_spin.valueChanged.connect(lambda v: self._set("ass_size", v))
        oform.addRow("ฟอนต์ใน .ass", self.font_box)
        oform.addRow("ขนาดตัวอักษร (ที่ความสูง 1080)", self.size_spin)
        out_lay.addLayout(oform)
        self.srt_colors = QCheckBox("ใส่แท็กสีใน .srt ด้วย (บางโปรแกรมไม่แสดงสี)", checked=self.s.srt_colors)
        self.srt_colors.toggled.connect(lambda on: self._set("srt_colors", on))
        out_lay.addWidget(self.srt_colors)
        col.addWidget(out)
        col.addStretch(1)

        scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        scroll.setWidget(body)
        QVBoxLayout(self).addWidget(scroll)
        self.layout().setContentsMargins(0, 0, 0, 0)

        (self.backend_cc if self.s.backend == "claude_code" else self.backend_api).setChecked(True)
        self.backend_api.toggled.connect(lambda on: on and self._set("backend", "api"))
        self.backend_cc.toggled.connect(lambda on: on and self._set("backend", "claude_code"))
        self._sync_backend()

    def _set(self, key: str, value) -> None:
        setattr(self.s, key, value)
        self._persist()
        if key == "backend":
            self._sync_backend()
        self.changed.emit()

    def _sync_backend(self) -> None:
        is_cc = self.s.backend == "claude_code"
        self.api_box.setVisible(not is_cc)
        self.cc_box.setVisible(is_cc)
        path = find_claude()
        self.cc_path.setText(f"พบโปรแกรม: {path}" if path else "ยังไม่พบ Claude Code ในเครื่องนี้")
        self.status_label.setText("")

    def _set_out(self, folder: str | None) -> None:
        self.out_label.setText(folder or "ข้างไฟล์วิดีโอ")
        self._set("output_dir", folder)

    def _pick_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "เลือกโฟลเดอร์สำหรับไฟล์ซับไทย")
        if folder:
            self._set_out(folder)

    def _run_check(self, fn) -> None:
        self.status_label.setText("กำลังตรวจ…")
        self._worker = CallWorker(fn)
        self._worker.done.connect(lambda msg: self._show_status(str(msg), COLORS["good"]))
        self._worker.failed.connect(lambda exc: self._show_status(str(exc), COLORS["bad"]))
        self._worker.start()

    def _show_status(self, text: str, color: str) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color};")

    def _save_and_test_key(self) -> None:
        key = self.key_edit.text().strip()
        if key:
            set_api_key(key)
            self.key_edit.clear()
            self.key_edit.setPlaceholderText("บันทึก key ไว้แล้ว — วางใหม่เพื่อเปลี่ยน")
        stored = get_api_key()
        if not stored:
            self._show_status("ยังไม่ได้ใส่ API key", COLORS["bad"])
            return
        model = self.s.model
        self._run_check(lambda: ApiBackend(stored, model).check())

    def _test_cc(self) -> None:
        model = self.s.model
        self._run_check(lambda: ClaudeCodeBackend(model).check())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_gui.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add subtitle_translator/gui/settings_page.py tests/test_gui.py
git commit -m "feat: settings page with backend switch, key test, model and output options"
```

---

### Task 14: Queue page (file queue, estimate, run)

**Files:**
- Modify (replace stub): `subtitle_translator/gui/queue_page.py`
- Test: `tests/test_gui.py` (append)

**Interfaces:**
- Consumes: Task 3 (`list_sub_tracks`, `pick_track`, `sidecar_subs`, `SubTrack`, `MediaError`), Task 2 (`SubtitleReadError`), Task 4 (`Settings`, `cache_dir`, `MODELS`), Task 5 (`AIError`, `AIErrorKind`), Task 9 (`make_backend`), Task 10 (`prepare_project`, `run_project`, `estimate_project_cost`, `Stopped`), Task 11 (`export_project`), Task 12 (`COLORS`)
- Produces: `@dataclass QueueItem(video: Path, source: SubTrack | Path | None = None, meta: str = "", status: str = "รอคิว", project: Project | None = None, estimate: float | None = None, error: str | None = None)`; `describe_source(video, tracks, sidecars, prefer) -> tuple[SubTrack | Path | None, list[SubTrack], str]`; `class QueueRunner(QThread)` signals `item_progress(int, str, float)`, `item_done(int, object)`, `item_failed(int, str)`, `paused(str)`; `class EstimateRunner(QThread)` signal `estimated(int, object)`; `QueuePage` methods `add_paths(paths: list[Path])`, `start()`, `stop()`, attribute `items: list[QueueItem]`, signal `open_review`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_gui.py`)

```python
from pathlib import Path

from subtitle_translator.core.media import SubTrack
from subtitle_translator.gui.queue_page import QueuePage, describe_source


def T(i, codec, lang):
    return SubTrack(sub_index=i, codec=codec, language=lang, title="")


def test_describe_source_rules(tmp_path):
    v = tmp_path / "EP01.mkv"
    side = tmp_path / "EP01.srt"
    assert describe_source(v, [T(0, "ass", "jpn")], [], ["ja", "en"])[0].sub_index == 0
    src, cands, meta = describe_source(v, [T(0, "hdmv_pgs_subtitle", "jpn")], [side], ["ja", "en"])
    assert src == side and "ไฟล์ซับข้างวิดีโอ" in meta
    src, cands, meta = describe_source(v, [], [], ["ja", "en"])
    assert src is None and "ต้องถอดเสียง" in meta
    src, cands, meta = describe_source(v, [T(0, "hdmv_pgs_subtitle", "jpn")], [], ["ja", "en"])
    assert src is None and "ซับภาพ" in meta
    src, cands, meta = describe_source(v, [T(0, "ass", "jpn"), T(1, "subrip", "jpn")], [], ["ja", "en"])
    assert src is None and len(cands) == 2


def test_queue_page_adds_sidecar_file_without_ffmpeg(qtbot, tmp_path, monkeypatch):
    v = tmp_path / "EP01.mkv"
    v.write_bytes(b"")
    (tmp_path / "EP01.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n", encoding="utf-8")
    monkeypatch.setattr("subtitle_translator.gui.queue_page.list_sub_tracks", lambda p: [])
    page = QueuePage(Settings(backend="claude_code"), lambda: None)   # no API estimate thread in tests
    qtbot.addWidget(page)
    page.add_paths([tmp_path])
    assert len(page.items) == 1
    assert page.items[0].source == tmp_path / "EP01.srt"
    page.add_paths([v])
    assert len(page.items) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gui.py -k "describe or queue_page" -v`
Expected: FAIL with `ImportError: cannot import name 'describe_source'`

- [ ] **Step 3: Write the implementation**

Replace `subtitle_translator/gui/queue_page.py`:

```python
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ..ai.backend import AIError, AIErrorKind
from ..ai.factory import make_backend
from ..core.media import MediaError, SubTrack, lang_of, list_sub_tracks, pick_track, sidecar_subs
from ..core.models import Project
from ..core.pricing import MODELS
from ..core.settings import Settings, cache_dir
from ..core.subio import SubtitleReadError
from ..export import export_project
from ..translate.pipeline import Stopped, estimate_project_cost, prepare_project, run_project
from .theme import COLORS

log = logging.getLogger(__name__)
VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".webm", ".avi"}
STOP_QUEUE = {AIErrorKind.AUTH, AIErrorKind.QUOTA, AIErrorKind.RATE_LIMIT,
              AIErrorKind.NOT_INSTALLED, AIErrorKind.NETWORK}


@dataclass
class QueueItem:
    video: Path
    source: SubTrack | Path | None = None
    meta: str = ""
    status: str = "รอคิว"
    project: Project | None = None
    estimate: float | None = None
    error: str | None = None


def describe_source(video: Path, tracks: list[SubTrack], sidecars: list[Path], prefer: list[str]
                    ) -> tuple[SubTrack | Path | None, list[SubTrack], str]:
    chosen, cands = pick_track(tracks, tuple(prefer))
    if chosen is not None:
        name = {"ja": "ญี่ปุ่น", "en": "อังกฤษ"}.get(lang_of(chosen) or "", chosen.language)
        return chosen, [], f"แทร็กซับ {name} · {chosen.codec}"
    if sidecars:
        return sidecars[0], [], f"ไฟล์ซับข้างวิดีโอ · {sidecars[0].name}"
    if cands:
        return None, cands, f"มีแทร็กซับ {len(cands)} แทร็ก · เลือกแทร็ก"
    if tracks:
        return None, [], "ซับภาพ (PGS/VobSub) · รองรับในเฟส 3"
    return None, [], "ไม่พบซับ · ต้องถอดเสียง (เฟส 2)"


def friendly(exc: Exception) -> str:
    if isinstance(exc, AIError) and exc.retry_after:
        return f"{exc} — ใช้ต่อได้หลัง {exc.retry_after}"
    return str(exc)


class QueueRunner(QThread):
    item_progress = Signal(int, str, float)
    item_done = Signal(int, object)
    item_failed = Signal(int, str)
    paused = Signal(str)

    def __init__(self, jobs: list[tuple[int, QueueItem]], settings: Settings, parent=None):
        super().__init__(parent)
        self.jobs = jobs
        self.settings = settings
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        for row, item in self.jobs:
            if self._stop:
                self.paused.emit("หยุดแล้ว — กดเริ่มเพื่อทำต่อ")
                return
            try:
                backend = make_backend(self.settings)
                project = prepare_project(item.video, item.source, self.settings, cache_dir())
                project = run_project(project, backend, self.settings,
                                      lambda stage, f, r=row: self.item_progress.emit(r, stage, f),
                                      lambda: self._stop)
                self.item_done.emit(row, project)
            except Stopped:
                self.paused.emit("หยุดแล้ว — กดเริ่มเพื่อทำต่อ")
                return
            except AIError as exc:
                self.item_failed.emit(row, friendly(exc))
                if exc.kind in STOP_QUEUE:
                    self.paused.emit(friendly(exc))
                    return
            except (MediaError, SubtitleReadError) as exc:
                self.item_failed.emit(row, str(exc))
            except Exception as exc:
                log.exception("unexpected failure on %s", item.video)
                self.item_failed.emit(row, f"ผิดพลาดที่ไม่คาดคิด: {exc}")


class EstimateRunner(QThread):
    estimated = Signal(int, object)

    def __init__(self, jobs: list[tuple[int, QueueItem]], settings: Settings, parent=None):
        super().__init__(parent)
        self.jobs = jobs
        self.settings = settings

    def run(self) -> None:
        try:
            backend = make_backend(self.settings)
        except AIError:
            return
        for row, item in self.jobs:
            try:
                project = prepare_project(item.video, item.source, self.settings, cache_dir())
                self.estimated.emit(row, estimate_project_cost(project, backend))
            except Exception:
                log.exception("estimate failed for %s", item.video)
                self.estimated.emit(row, None)


class FileCard(QFrame):
    def __init__(self, on_action, parent=None):
        super().__init__(parent)
        self.setProperty("card", True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        top = QHBoxLayout()
        text = QVBoxLayout()
        self.name = QLabel()
        self.name.setProperty("role", "h2")
        self.meta = QLabel()
        self.meta.setProperty("role", "muted")
        text.addWidget(self.name)
        text.addWidget(self.meta)
        top.addLayout(text, 1)
        self.status = QLabel()
        self.cost = QLabel()
        self.cost.setMinimumWidth(70)
        self.cost.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.action = QPushButton()
        self.action.clicked.connect(on_action)
        top.addWidget(self.status)
        top.addWidget(self.cost)
        top.addWidget(self.action)
        lay.addLayout(top)
        self.bar = QProgressBar(textVisible=False, maximum=1000)
        lay.addWidget(self.bar)

    def update_from(self, item: QueueItem) -> None:
        self.name.setText(item.video.name)
        self.meta.setText(item.error or item.meta)
        self.status.setText(item.status)
        color = COLORS["bad"] if item.error else COLORS["text2"]
        self.status.setStyleSheet(f"color: {color};")
        if item.project and item.project.cost_usd:
            self.cost.setText(f"${item.project.cost_usd:.2f}")
        elif item.estimate is not None:
            self.cost.setText(f"~${item.estimate:.2f}")
        else:
            self.cost.setText("—")
        done = item.project is not None and item.project.stage == "done"
        self.action.setText("ตรวจ/แก้" if done else ("เลือกแทร็ก" if item.source is None and "เลือก" in item.meta else "ลบ"))


class QueuePage(QWidget):
    open_review = Signal(object)

    def __init__(self, settings: Settings, persist, parent=None):
        super().__init__(parent)
        self.s = settings
        self._persist = persist
        self.items: list[QueueItem] = []
        self.cards: list[FileCard] = []
        self.runner: QueueRunner | None = None
        self.estimator: EstimateRunner | None = None
        self.setAcceptDrops(True)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(32, 28, 32, 28)
        outer.setSpacing(24)

        left = QVBoxLayout()
        head = QHBoxLayout()
        title = QLabel("คิวงาน")
        title.setProperty("role", "h1")
        head.addWidget(title, 1)
        add_files = QPushButton("+ เพิ่มไฟล์")
        add_files.clicked.connect(self._pick_files)
        add_dir = QPushButton("+ เพิ่มโฟลเดอร์")
        add_dir.clicked.connect(self._pick_dir)
        head.addWidget(add_files)
        head.addWidget(add_dir)
        left.addLayout(head)
        drop = QLabel("ลากไฟล์ .mkv หรือทั้งโฟลเดอร์มาวางที่นี่ · ระบบหาแทร็กซับญี่ปุ่น/อังกฤษ และไฟล์ .srt/.ass ข้างวิดีโอให้เอง")
        drop.setWordWrap(True)
        drop.setStyleSheet("border: 1.5px dashed #444; border-radius: 10px; padding: 16px; color: #C8C8C8;")
        left.addWidget(drop)
        self.list_box = QVBoxLayout()
        self.list_box.addStretch(1)
        holder = QWidget()
        holder.setLayout(self.list_box)
        scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        scroll.setWidget(holder)
        left.addWidget(scroll, 1)
        outer.addLayout(left, 3)

        side = QFrame()
        side.setProperty("card", True)
        side.setFixedWidth(340)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(20, 20, 20, 20)
        h = QLabel("สรุปก่อนเริ่ม")
        h.setProperty("role", "h2")
        sl.addWidget(h)
        sl.addWidget(QLabel("เชื่อมต่อผ่าน"))
        self.backend_box = QComboBox()
        self.backend_box.addItems(["Anthropic API key", "Claude Code (บัญชี Max)"])
        self.backend_box.setCurrentIndex(1 if self.s.backend == "claude_code" else 0)
        self.backend_box.currentIndexChanged.connect(self._backend_changed)
        sl.addWidget(self.backend_box)
        sl.addWidget(QLabel("โมเดลแปล"))
        self.model_box = QComboBox()
        for mid, info in MODELS.items():
            self.model_box.addItem(info.label + (" · แนะนำ" if info.recommended else ""), mid)
        self.model_box.setCurrentIndex(list(MODELS).index(self.s.model))
        self.model_box.currentIndexChanged.connect(self._model_changed)
        sl.addWidget(self.model_box)
        self.total = QLabel()
        self.total.setProperty("role", "h1")
        sl.addWidget(self.total)
        est = QPushButton("ประมาณราคา")
        est.clicked.connect(self.estimate)
        sl.addWidget(est)
        self.start_btn = QPushButton("เริ่มแปลทั้งคิว")
        self.start_btn.setProperty("accent", True)
        self.start_btn.clicked.connect(self.start)
        self.stop_btn = QPushButton("หยุด", enabled=False)
        self.stop_btn.clicked.connect(self.stop)
        export_all = QPushButton("ส่งออกไฟล์ที่เสร็จทั้งหมด")
        export_all.clicked.connect(self.export_all)
        for b in (self.start_btn, self.stop_btn, export_all):
            sl.addWidget(b)
        self.message = QLabel("หยุดได้ทุกเมื่อ ผลที่เสร็จแล้วบันทึกไว้ กลับมาทำต่อไม่เสียเงินซ้ำ")
        self.message.setWordWrap(True)
        self.message.setProperty("role", "muted")
        sl.addWidget(self.message)
        sl.addStretch(1)
        outer.addWidget(side)
        self._refresh_total()

    # ----- adding files -----
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add_paths([Path(u.toLocalFile()) for u in e.mimeData().urls()])

    def _pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "เลือกวิดีโอ", "", "Video (*.mkv *.mp4 *.m4v *.webm *.avi)")
        self.add_paths([Path(f) for f in files])

    def _pick_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "เลือกโฟลเดอร์")
        if folder:
            self.add_paths([Path(folder)])

    def add_paths(self, paths: list[Path]) -> None:
        videos: list[Path] = []
        for p in paths:
            if p.is_dir():
                videos += sorted(x for x in p.rglob("*") if x.suffix.lower() in VIDEO_EXT)
            elif p.suffix.lower() in VIDEO_EXT:
                videos.append(p)
        known = {i.video for i in self.items}
        new_rows = []
        for v in videos:
            if v in known:
                continue
            try:
                tracks = list_sub_tracks(v)
            except MediaError as exc:
                self._append(QueueItem(video=v, status="ผิดพลาด", error=str(exc)))
                continue
            source, cands, meta = describe_source(v, tracks, sidecar_subs(v), self.s.prefer_langs)
            item = QueueItem(video=v, source=source, meta=meta,
                             status="รอคิว" if source is not None else "ข้าม")
            self._append(item, cands)
            if item.source is not None:
                new_rows.append(len(self.items) - 1)
        if new_rows and self.s.backend == "api":
            self._estimate_rows(new_rows)
        self._refresh_total()

    def _append(self, item: QueueItem, cands: list[SubTrack] | None = None) -> None:
        self.items.append(item)
        row = len(self.items) - 1
        card = FileCard(lambda _=False, r=row: self._action(r))
        card._cands = cands or []
        self.cards.append(card)
        self.list_box.insertWidget(self.list_box.count() - 1, card)
        card.update_from(item)

    def _action(self, row: int) -> None:
        item, card = self.items[row], self.cards[row]
        if item.project is not None and item.project.stage == "done":
            self.open_review.emit(item.project)
        elif item.source is None and card._cands:
            labels = [f"#{t.sub_index} {t.language} {t.codec} {t.title}".strip() for t in card._cands]
            choice, ok = QInputDialog.getItem(self, "เลือกแทร็กซับ", item.video.name, labels, 0, False)
            if ok:
                item.source = card._cands[labels.index(choice)]
                item.meta, item.status = f"แทร็กซับ {choice}", "รอคิว"
                card.update_from(item)
        elif self.runner is None:
            card.setParent(None)
            del self.items[row], self.cards[row]
            for r, c in enumerate(self.cards):
                c.action.clicked.disconnect()
                c.action.clicked.connect(lambda _=False, rr=r: self._action(rr))
            self._refresh_total()

    # ----- settings shortcuts -----
    def _backend_changed(self, index: int) -> None:
        self.s.backend = "claude_code" if index == 1 else "api"
        self._persist()
        self._refresh_total()

    def _model_changed(self, index: int) -> None:
        self.s.model = self.model_box.itemData(index)
        self._persist()
        for it in self.items:
            it.estimate = None
        self._refresh_total()

    # ----- estimate -----
    def estimate(self) -> None:
        self._estimate_rows([r for r, it in enumerate(self.items) if it.source is not None])

    def _estimate_rows(self, rows: list[int]) -> None:
        if self.s.backend != "api" or not rows:
            self._refresh_total()
            return
        self.estimator = EstimateRunner([(r, self.items[r]) for r in rows], self.s)
        self.estimator.estimated.connect(self._on_estimated)
        self.estimator.start()

    def _on_estimated(self, row: int, value) -> None:
        self.items[row].estimate = value
        self.cards[row].update_from(self.items[row])
        self._refresh_total()

    def _refresh_total(self) -> None:
        if self.s.backend == "claude_code":
            self.total.setText("ใช้โควตา Max")
            return
        total = sum(it.estimate or 0 for it in self.items if it.source is not None)
        self.total.setText(f"~${total:.2f}")

    # ----- run -----
    def start(self) -> None:
        jobs = [(r, it) for r, it in enumerate(self.items)
                if it.source is not None and not (it.project and it.project.stage == "done")]
        if not jobs:
            return
        self.runner = QueueRunner(jobs, self.s)
        self.runner.item_progress.connect(self._on_progress)
        self.runner.item_done.connect(self._on_done)
        self.runner.item_failed.connect(self._on_failed)
        self.runner.paused.connect(self.message.setText)
        self.runner.finished.connect(self._on_finished)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.message.setText("กำลังแปล…")
        self.runner.start()

    def stop(self) -> None:
        if self.runner:
            self.runner.stop()
            self.message.setText("กำลังหยุดหลังจบช่วงปัจจุบัน…")

    def _on_progress(self, row: int, stage: str, frac: float) -> None:
        item = self.items[row]
        item.status, item.error = f"{stage} {int(frac * 100)}%", None
        self.cards[row].bar.setValue(int(frac * 1000))
        self.cards[row].update_from(item)

    def _on_done(self, row: int, project) -> None:
        item = self.items[row]
        item.project, item.status = project, "พร้อมตรวจ"
        failed = sum(1 for c in project.episode.cues if c.status == "failed")
        if failed:
            item.status = f"พร้อมตรวจ · ต้องแปลเอง {failed} บรรทัด"
        self.cards[row].bar.setValue(1000)
        self.cards[row].update_from(item)

    def _on_failed(self, row: int, msg: str) -> None:
        item = self.items[row]
        item.status, item.error = "ผิดพลาด", msg
        self.cards[row].update_from(item)

    def _on_finished(self) -> None:
        self.runner = None
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        if not self.message.text().startswith(("หยุด", "โควตา", "API", "เครดิต", "ยังไม่", "ไม่พบ", "เชื่อม", "เรียก")):
            self.message.setText("เสร็จแล้ว — กด ตรวจ/แก้ ที่แต่ละไฟล์")

    def export_all(self) -> None:
        done = [it for it in self.items if it.project and it.project.stage == "done"]
        written = 0
        for it in done:
            try:
                export_project(it.project, self.s)
            except FileExistsError as exc:
                ans = QMessageBox.question(self, "มีไฟล์อยู่แล้ว", f"เขียนทับ {exc}?")
                if ans != QMessageBox.Yes:
                    continue
                export_project(it.project, self.s, overwrite=True)
            written += 1
        self.message.setText(f"ส่งออกแล้ว {written} ไฟล์")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_gui.py -v`
Expected: 5 passed

- [ ] **Step 5: Manual check**

Run `python -m subtitle_translator`, drag a folder holding one MKV with a Japanese ASS track. Expected: one card "แทร็กซับ ญี่ปุ่น · ass", and with an API key saved, a `~$` estimate appears within a few seconds.

- [ ] **Step 6: Commit**

```bash
git add subtitle_translator/gui/queue_page.py tests/test_gui.py
git commit -m "feat: queue page with track detection, estimate, background run, stop/resume"
```

---

### Task 15: Review page (video preview, cue table, editor, timeline, export)

**Files:**
- Create: `subtitle_translator/gui/timeline_widget.py`
- Modify (replace stub): `subtitle_translator/gui/review_page.py`
- Test: `tests/test_gui.py` (append)

**Interfaces:**
- Consumes: Task 1 models, Task 3 (`grab_frame`, `MediaError`), Task 4 (`Settings`, `cache_dir`), Task 6 (`UNKNOWN_COLOR`), Task 7 (`translate_chunk`), Task 9 (`make_backend`), Task 11 (`export_project`), Task 12 (`CallWorker`, `COLORS`), `save_project`
- Produces: `TimelineWidget` with `set_data(cues, colors: dict[str | None, str], names: dict[str | None, str])`, `set_selected(cue_id)`, signal `cue_clicked(int)`; `UNSURE_THRESHOLD = 0.6`; `cps(cue, text) -> float`; `filter_ids(cues, mode: str, query: str) -> list[int]` (`mode` in `"all" | "unsure" | "edited" | "failed"`); `ReviewPage.load(project)`, attributes `table: QTableWidget`, `editor: QPlainTextEdit`, `speaker_box: QComboBox`, `timeline`, methods `select(cue_id)`, `save_edit()`, `set_filter(mode)`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_gui.py`)

```python
from helpers import make_episode
from subtitle_translator.core.models import Character, Project
from subtitle_translator.gui.review_page import ReviewPage, cps, filter_ids


def review_project(tmp_path):
    ep = make_episode(4)
    ep.video_path = str(tmp_path / "EP01.mkv")
    ep.characters = [Character(key="yuki", name_src="ユキ", name_th="ยูกิ", color="#FBBF24")]
    for c in ep.cues:
        c.thai, c.status, c.speaker, c.speaker_conf = f"ไทย {c.id}", "translated", "yuki", 0.9
    ep.cues[1].speaker_conf = 0.3
    ep.cues[2].status = "failed"
    return Project(episode=ep, backend="api", model="claude-opus-5-5", stage="done")


def test_filters_and_cps(tmp_path):
    ep = review_project(tmp_path).episode
    assert filter_ids(ep.cues, "all", "") == [1, 2, 3, 4]
    assert filter_ids(ep.cues, "unsure", "") == [2]
    assert filter_ids(ep.cues, "failed", "") == [3]
    assert filter_ids(ep.cues, "all", "ไทย 4") == [4]
    assert cps(ep.cues[0], "x" * 15) == 10.0


def test_review_page_edit_saves_project(qtbot, tmp_path):
    p = review_project(tmp_path)
    page = ReviewPage(Settings(), lambda: None)
    qtbot.addWidget(page)
    page.load(p)
    assert page.table.rowCount() == 4
    page.select(2)
    assert page.editor.toPlainText() == "ไทย 2"
    page.editor.setPlainText("แก้แล้ว")
    page.save_edit()
    assert p.episode.cue(2).thai == "แก้แล้ว" and p.episode.cue(2).status == "edited"
    assert (tmp_path / "EP01.stproj.json").exists()
    page.set_filter("failed")
    assert page.table.rowCount() == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gui.py -k "filters or review_page" -v`
Expected: FAIL with `ImportError: cannot import name 'cps'`

- [ ] **Step 3: Write the timeline widget**

`subtitle_translator/gui/timeline_widget.py`:

```python
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..core.models import Cue

WINDOW = 60.0      # seconds shown
LABEL_W = 96
LANE_H = 20


class TimelineWidget(QWidget):
    cue_clicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cues: list[Cue] = []
        self.colors: dict[str | None, str] = {}
        self.names: dict[str | None, str] = {}
        self.selected: int | None = None
        self._hits: list[tuple[QRectF, int]] = []
        self.setMinimumHeight(40)

    def set_data(self, cues: list[Cue], colors: dict[str | None, str], names: dict[str | None, str]) -> None:
        self.cues = [c for c in cues if not c.skip]
        self.colors, self.names = colors, names
        lanes = len(names)
        self.setMinimumHeight(24 + lanes * (LANE_H + 4))
        self.update()

    def set_selected(self, cue_id: int | None) -> None:
        self.selected = cue_id
        self.update()

    def _window(self) -> tuple[float, float]:
        center = 0.0
        for c in self.cues:
            if c.id == self.selected:
                center = (c.start + c.end) / 2
        start = max(0.0, center - WINDOW / 2)
        return start, start + WINDOW

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#222222"))
        t0, t1 = self._window()
        width = max(self.width() - LABEL_W - 12, 10)
        p.setPen(QColor("#8F8F8F"))
        for k in range(5):
            t = t0 + (t1 - t0) * k / 4
            x = LABEL_W + width * k / 4
            p.drawText(int(x), 14, f"{int(t // 60):02d}:{int(t % 60):02d}")
        self._hits = []
        for lane, key in enumerate(self.names):
            y = 22 + lane * (LANE_H + 4)
            p.setPen(QColor(self.colors.get(key, "#A8A29E")))
            p.drawText(8, y + 15, self.names[key])
            p.fillRect(QRectF(LABEL_W, y, width, LANE_H), QColor("#1A1A1A"))
            for c in self.cues:
                if c.speaker != key or c.end < t0 or c.start > t1:
                    continue
                x0 = LABEL_W + (c.start - t0) / (t1 - t0) * width
                x1 = LABEL_W + (c.end - t0) / (t1 - t0) * width
                rect = QRectF(x0, y + 3, max(x1 - x0, 3), LANE_H - 6)
                p.fillRect(rect, QColor(self.colors.get(key, "#A8A29E")))
                if c.id == self.selected:
                    p.setPen(QPen(QColor("#FFFFFF"), 2))
                    p.drawRect(rect)
                self._hits.append((rect, c.id))
        p.end()

    def mousePressEvent(self, event) -> None:
        pos = event.position()
        for rect, cue_id in self._hits:
            if rect.adjusted(-2, -4, 2, 4).contains(pos):
                self.cue_clicked.emit(cue_id)
                return
```

- [ ] **Step 4: Write the review page**

Replace `subtitle_translator/gui/review_page.py`:

```python
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSizeF, Qt, QUrl
from PySide6.QtGui import QBrush, QColor, QFont, QKeySequence, QPen, QPixmap, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QComboBox, QFrame, QGraphicsPixmapItem, QGraphicsScene,
    QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..ai.factory import make_backend
from ..core.media import MediaError, grab_frame
from ..core.models import Cue, Project
from ..core.project_store import save_project
from ..core.settings import Settings, cache_dir
from ..export import export_project
from ..translate.analyze import UNKNOWN_COLOR
from ..translate.translate import translate_chunk
from .theme import COLORS
from .timeline_widget import TimelineWidget
from .worker import CallWorker

log = logging.getLogger(__name__)
UNSURE_THRESHOLD = 0.6
CPS_LIMIT = 17


def cps(cue: Cue, text: str) -> float:
    return len(text.replace("\n", "")) / max(cue.end - cue.start, 0.1)


def filter_ids(cues: list[Cue], mode: str, query: str) -> list[int]:
    q = query.strip().lower()
    out = []
    for c in cues:
        if c.skip:
            continue
        if mode == "unsure" and not (c.speaker is None or c.speaker_conf < UNSURE_THRESHOLD):
            continue
        if mode == "edited" and c.status != "edited":
            continue
        if mode == "failed" and c.status != "failed":
            continue
        if q and q not in c.src.lower() and q not in (c.thai or "").lower():
            continue
        out.append(c.id)
    return out


class VideoPreview(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setStyleSheet("background: #0A0A0A; border: 1px solid #2E2E2E; border-radius: 10px;")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumSize(420, 236)
        self.video = QGraphicsVideoItem()
        self.still = QGraphicsPixmapItem()
        self.text = QGraphicsSimpleTextItem()
        font = QFont("Leelawadee UI", 18, QFont.DemiBold)
        self.text.setFont(font)
        self.text.setPen(QPen(QColor("#000000"), 1.2))
        for item in (self.video, self.still, self.text):
            self.scene().addItem(item)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.errorOccurred.connect(self._on_error)
        self.fallback = False
        self.path: str | None = None

    def set_source(self, path: str) -> None:
        self.path, self.fallback = path, False
        self.still.setPixmap(QPixmap())
        self.player.setSource(QUrl.fromLocalFile(path))

    def _on_error(self, *_):
        log.warning("video playback failed, using still frames: %s", self.player.errorString())
        self.fallback = True

    def show_cue(self, cue: Cue, text: str, color: str) -> None:
        self.text.setText(text)
        self.text.setBrush(QBrush(QColor(color)))
        if self.fallback and self.path:
            try:
                png = grab_frame(self.path, (cue.start + cue.end) / 2, cache_dir() / "preview.png")
                self.still.setPixmap(QPixmap(str(png)).scaled(self.viewport().size(), Qt.KeepAspectRatio))
            except MediaError:
                pass
        else:
            self.player.pause()
            self.player.setPosition(int(cue.start * 1000))
        self._layout()

    def toggle(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout()

    def _layout(self) -> None:
        size = self.viewport().size()
        self.scene().setSceneRect(0, 0, size.width(), size.height())
        self.video.setSize(QSizeF(size.width(), size.height()))
        r = self.text.boundingRect()
        self.text.setPos((size.width() - r.width()) / 2, size.height() - r.height() - 16)


class ReviewPage(QWidget):
    COLS = ["#", "เวลา", "คนพูด → พูดกับ", "ต้นฉบับ", "ไทย"]

    def __init__(self, settings: Settings, persist, parent=None):
        super().__init__(parent)
        self.s = settings
        self.project: Project | None = None
        self.current: int | None = None
        self.mode = "all"
        self._worker: CallWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        self.title = QLabel("ยังไม่ได้เลือกไฟล์")
        self.title.setProperty("role", "h1")
        self.subtitle = QLabel("")
        self.subtitle.setProperty("role", "muted")
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        head.addLayout(titles, 1)
        self.filter_group = QButtonGroup(self)
        self.filter_buttons: dict[str, QPushButton] = {}
        for mode, text in (("all", "ทั้งหมด"), ("unsure", "ไม่มั่นใจ"), ("edited", "แก้แล้ว"), ("failed", "ล้มเหลว")):
            b = QPushButton(text, checkable=True)
            b.clicked.connect(lambda _=False, m=mode: self.set_filter(m))
            self.filter_group.addButton(b)
            self.filter_buttons[mode] = b
            head.addWidget(b)
        self.search = QLineEdit(placeholderText="ค้นหาคำ…")
        self.search.textChanged.connect(lambda _: self._fill_table())
        head.addWidget(self.search)
        export = QPushButton("ส่งออก .srt + .ass")
        export.setProperty("accent", True)
        export.clicked.connect(self.export)
        head.addWidget(export)
        root.addLayout(head)

        body = QHBoxLayout()
        left = QVBoxLayout()
        self.preview = VideoPreview()
        left.addWidget(self.preview, 3)
        play = QPushButton("เล่น / หยุด  (F5)")
        play.clicked.connect(self.preview.toggle)
        left.addWidget(play)
        chars_card = QFrame()
        chars_card.setProperty("card", True)
        cl = QVBoxLayout(chars_card)
        h = QLabel("ตัวละคร")
        h.setProperty("role", "h2")
        cl.addWidget(h)
        self.chars = QListWidget()
        cl.addWidget(self.chars)
        left.addWidget(chars_card, 2)
        body.addLayout(left, 2)

        right = QVBoxLayout()
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(True)
        hh = self.table.horizontalHeader()
        for i, mode in enumerate([QHeaderView.ResizeToContents] * 3 + [QHeaderView.Stretch] * 2):
            hh.setSectionResizeMode(i, mode)
        self.table.itemSelectionChanged.connect(self._on_row)
        right.addWidget(self.table, 1)

        edit_card = QFrame()
        edit_card.setProperty("card", True)
        el = QVBoxLayout(edit_card)
        er = QHBoxLayout()
        self.edit_label = QLabel("แก้บรรทัด")
        self.edit_label.setProperty("role", "muted")
        er.addWidget(self.edit_label)
        er.addWidget(QLabel("คนพูด"))
        self.speaker_box = QComboBox()
        er.addWidget(self.speaker_box)
        er.addStretch(1)
        self.cps_label = QLabel("")
        er.addWidget(self.cps_label)
        self.retranslate_btn = QPushButton("แปลบรรทัดนี้ใหม่")
        self.retranslate_btn.clicked.connect(self.retranslate)
        er.addWidget(self.retranslate_btn)
        save = QPushButton("บันทึก (Ctrl+Enter)")
        save.clicked.connect(self.save_edit)
        er.addWidget(save)
        el.addLayout(er)
        self.editor = QPlainTextEdit()
        self.editor.setFixedHeight(70)
        self.editor.textChanged.connect(self._update_cps)
        el.addWidget(self.editor)
        right.addWidget(edit_card)
        body.addLayout(right, 3)
        root.addLayout(body, 1)

        self.timeline = TimelineWidget()
        self.timeline.cue_clicked.connect(self.select)
        root.addWidget(self.timeline)

        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.save_edit)
        QShortcut(QKeySequence("F5"), self, activated=self.preview.toggle)

    # ----- data helpers -----
    def _colors(self) -> dict[str | None, str]:
        colors: dict[str | None, str] = {ch.key: ch.color for ch in self.project.episode.characters}
        colors[None] = UNKNOWN_COLOR
        return colors

    def _names(self) -> dict[str | None, str]:
        names: dict[str | None, str] = {ch.key: ch.name_th for ch in self.project.episode.characters}
        names[None] = "ไม่ทราบ"
        return names

    def load(self, project: Project) -> None:
        self.project = project
        ep = project.episode
        self.title.setText(Path(ep.video_path).name)
        lang = {"ja": "ญี่ปุ่น", "en": "อังกฤษ"}.get(ep.src_lang, ep.src_lang)
        self.subtitle.setText(f"{lang} → ไทย · {len(ep.cues)} บรรทัด · {project.model} · ใช้ไป ${project.cost_usd:.2f}")
        names, colors = self._names(), self._colors()
        self.chars.clear()
        for key, name in names.items():
            count = sum(1 for c in ep.cues if c.speaker == key and not c.skip)
            ch = ep.character(key)
            info = f"{ch.gender} · {ch.role}" if ch else "ยังระบุคนพูดไม่ได้"
            self.chars.addItem(f"● {name} — {info} · {count} บรรทัด")
            self.chars.item(self.chars.count() - 1).setForeground(QColor(colors[key]))
        self.speaker_box.clear()
        for key, name in names.items():
            self.speaker_box.addItem(name, key)
        self.preview.set_source(ep.video_path)
        self.timeline.set_data(ep.cues, colors, names)
        self.filter_buttons["all"].setChecked(True)
        self.mode = "all"
        self._update_counts()
        self._fill_table()

    def _update_counts(self) -> None:
        cues = self.project.episode.cues
        for mode, text in (("all", "ทั้งหมด"), ("unsure", "ไม่มั่นใจ"), ("edited", "แก้แล้ว"), ("failed", "ล้มเหลว")):
            self.filter_buttons[mode].setText(f"{text} {len(filter_ids(cues, mode, ''))}")

    def set_filter(self, mode: str) -> None:
        self.mode = mode
        self.filter_buttons[mode].setChecked(True)
        self._fill_table()

    def _fill_table(self) -> None:
        if not self.project:
            return
        ep = self.project.episode
        ids = filter_ids(ep.cues, self.mode, self.search.text())
        names, colors = self._names(), self._colors()
        self.table.blockSignals(True)
        self.table.setRowCount(len(ids))
        for row, cue_id in enumerate(ids):
            c = ep.cue(cue_id)
            who = f"{names.get(c.speaker, 'ไม่ทราบ')} → {c.addressee or '?'}"
            if c.speaker is None or c.speaker_conf < UNSURE_THRESHOLD:
                who += "  ⚠"
            values = [str(c.id), f"{int(c.start // 60):02d}:{c.start % 60:04.1f}", who, c.src, c.thai or "—"]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, c.id)
                if col == 2:
                    item.setForeground(QColor(colors.get(c.speaker, UNKNOWN_COLOR)))
                if col == 4 and c.status == "failed":
                    item.setForeground(QColor(COLORS["bad"]))
                self.table.setItem(row, col, item)
        self.table.blockSignals(False)
        self.table.resizeRowsToContents()

    # ----- selection & editing -----
    def _on_row(self) -> None:
        items = self.table.selectedItems()
        if items:
            self.select(items[0].data(Qt.UserRole), from_table=True)

    def select(self, cue_id: int, from_table: bool = False) -> None:
        if not self.project:
            return
        c = self.project.episode.cue(cue_id)
        self.current = cue_id
        self.edit_label.setText(f"แก้บรรทัด {cue_id}")
        self.editor.setPlainText(c.thai or "")
        idx = self.speaker_box.findData(c.speaker)
        self.speaker_box.setCurrentIndex(idx if idx >= 0 else self.speaker_box.count() - 1)
        self.preview.show_cue(c, c.thai or c.src, self._colors().get(c.speaker, UNKNOWN_COLOR))
        self.timeline.set_selected(cue_id)
        if not from_table:
            for row in range(self.table.rowCount()):
                if self.table.item(row, 0).data(Qt.UserRole) == cue_id:
                    self.table.blockSignals(True)
                    self.table.selectRow(row)
                    self.table.blockSignals(False)
                    break

    def _update_cps(self) -> None:
        if self.current is None or not self.project:
            return
        value = cps(self.project.episode.cue(self.current), self.editor.toPlainText())
        warn = value > CPS_LIMIT
        self.cps_label.setText(f"ความเร็วอ่าน {value:.0f} ตัวอักษร/วินาที" + (" · ยาวไป" if warn else ""))
        self.cps_label.setStyleSheet(f"color: {COLORS['warn'] if warn else COLORS['good']};")

    def save_edit(self) -> None:
        if self.current is None or not self.project:
            return
        c = self.project.episode.cue(self.current)
        c.thai = self.editor.toPlainText().strip() or None
        c.speaker = self.speaker_box.currentData()
        if c.speaker is not None:
            c.speaker_conf = 1.0
        c.status = "edited" if c.thai else "failed"
        save_project(self.project)
        self.timeline.update()
        self._update_counts()
        current = self.current
        self._fill_table()
        self.select(current)

    def retranslate(self) -> None:
        if self.current is None or not self.project:
            return
        cue_id, ep = self.current, self.project.episode
        self.retranslate_btn.setEnabled(False)
        self.retranslate_btn.setText("กำลังแปล…")

        def job():
            backend = make_backend(self.s)
            return translate_chunk(ep, [cue_id], backend, self.s.effort_translate)

        def finish(result=None, error=None):
            self.retranslate_btn.setEnabled(True)
            self.retranslate_btn.setText("แปลบรรทัดนี้ใหม่")
            if error is not None:
                QMessageBox.warning(self, "แปลไม่สำเร็จ", str(error))
                return
            self.project.cost_usd += result or 0.0
            save_project(self.project)
            self._fill_table()
            self.select(cue_id)

        self._worker = CallWorker(job)
        self._worker.done.connect(lambda r: finish(result=r))
        self._worker.failed.connect(lambda e: finish(error=e))
        self._worker.start()

    def export(self) -> None:
        if not self.project:
            return
        try:
            srt, ass = export_project(self.project, self.s)
        except FileExistsError as exc:
            if QMessageBox.question(self, "มีไฟล์อยู่แล้ว", f"เขียนทับ {exc}?") != QMessageBox.Yes:
                return
            srt, ass = export_project(self.project, self.s, overwrite=True)
        QMessageBox.information(self, "ส่งออกแล้ว", f"{srt}\n{ass}")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_gui.py -v`
Expected: 7 passed

- [ ] **Step 6: Manual check**

Run the app, translate one short MKV, open ตรวจ/แก้. Expected: the table fills, clicking a row jumps the video and shows the Thai line in the speaker's colour, the timeline highlights the cue, editing + Ctrl+Enter marks the row edited, and Export writes both files beside the video.

- [ ] **Step 7: Commit**

```bash
git add subtitle_translator/gui/review_page.py subtitle_translator/gui/timeline_widget.py tests/test_gui.py
git commit -m "feat: review page with video preview, filters, editor, timeline, export"
```

---

### Task 16: Live tests, packaging and release check

**Files:**
- Create: `tests/test_live.py`, `SubTranslator.spec`, `README.md`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the live test**

`tests/test_live.py`:

```python
import pytest

from helpers import make_episode
from subtitle_translator.ai.api_backend import ApiBackend
from subtitle_translator.ai.claude_code_backend import ClaudeCodeBackend, find_claude
from subtitle_translator.core.settings import get_api_key
from subtitle_translator.translate.analyze import analyze
from subtitle_translator.translate.translate import translate_chunk

LINES = [
    ("お姉ちゃん、起きて！遅刻するよ！", "Haru"), ("うるさいなぁ…あと五分だけ。", "Yuki"),
    ("もう！お母さんに言いつけるからね！", "Haru"), ("わかった、わかったよ、ハル。", "Yuki"),
    ("お姉ちゃん、朝ごはんできてるよ。", "Haru"), ("ありがとう。ハルは本当にしっかり者だね。", "Yuki"),
] * 3 + [("おはよう。二人とも元気だね。", "Tanaka"), ("おはようございます、先生！", "Yuki")]


def live_episode():
    ep = make_episode(len(LINES))
    for c, (text, actor) in zip(ep.cues, LINES):
        c.src, c.actor = text, actor
    return ep


def backends():
    out = []
    if get_api_key():
        out.append(ApiBackend(get_api_key(), "claude-opus-5-5"))
    if find_claude():
        out.append(ClaudeCodeBackend("claude-sonnet-5-5"))
    return out


@pytest.mark.live
@pytest.mark.parametrize("backend", backends(), ids=lambda b: b.name)
def test_live_translation(backend):
    ep = live_episode()
    analyze(ep, backend, "high")
    translate_chunk(ep, [c.id for c in ep.cues], backend, "medium")
    assert all(c.thai for c in ep.cues), [c.id for c in ep.cues if not c.thai]
    for c in ep.cues:
        if "お姉ちゃん" in c.src:
            assert "พี่" in c.thai, c.thai
```

Run (only on request; costs money / Max quota): `pytest -m live -v`
Expected: one pass per configured backend; every line translated and `お姉ちゃん` rendered with "พี่".

- [ ] **Step 2: Write the PyInstaller recipe**

`SubTranslator.spec`:

```python
# -*- mode: python ; coding: utf-8 -*-
a = Analysis(
    ["subtitle_translator/__main__.py"],
    pathex=["."],
    binaries=[("bin/ffmpeg.exe", "bin"), ("bin/ffprobe.exe", "bin")],
    datas=[],
    hiddenimports=["keyring.backends.Windows"],
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="SubTranslator", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="SubTranslator")
```

- [ ] **Step 3: Build**

Run: `pyinstaller --noconfirm SubTranslator.spec`
Expected: `dist\SubTranslator\SubTranslator.exe` and `dist\SubTranslator\_internal\bin\ffmpeg.exe` exist.

- [ ] **Step 4: Write `README.md`**

```markdown
# แปลซับไทย (SubTranslator)

แปลซับญี่ปุ่น/อังกฤษในไฟล์ MKV/MP4 เป็นภาษาไทยแบบรู้บริบทตัวละคร แล้วส่งออก `.th.srt` และ `.th.ass` ข้างวิดีโอ

## ใช้งาน
1. เปิด `SubTranslator.exe`
2. ตั้งค่า → เลือกวิธีเชื่อมต่อ
   - Anthropic API key: สร้าง key ที่ Claude Console (ต้องเติมเงินก่อน) แล้ววางในช่อง กด "บันทึกและทดสอบ"
   - Claude Code (บัญชี Max): ติดตั้ง Claude Code แล้วพิมพ์ `claude` ใน Terminal เพื่อล็อกอินครั้งแรก
3. คิวงาน → ลากไฟล์หรือโฟลเดอร์มาวาง → เริ่มแปลทั้งคิว
4. ตรวจ/แก้ → แก้คำแปล → ส่งออก

หยุดกลางทางได้ เปิดโปรแกรมใหม่แล้วกดเริ่ม งานจะทำต่อจากที่ค้าง (ข้อมูลอยู่ใน `<ชื่อวิดีโอ>.stproj.json`)

## สำหรับนักพัฒนา
    python -m venv .venv && .venv\Scripts\activate
    pip install -e ".[dev]"
    pytest                 # ไม่เรียก AI จริง
    pytest -m live         # เรียก AI จริง เสียเงิน/โควตา
    pyinstaller --noconfirm SubTranslator.spec
```

- [ ] **Step 5: Release check on a clean machine**

On a Windows PC without Python:
1. Copy `dist\SubTranslator\` and run `SubTranslator.exe`. Expected: dark window opens, no "ไม่พบ ffmpeg" warning.
2. Settings → API key → save and test. Expected: "เชื่อมต่อ API ได้".
3. Drop one real MKV with a Japanese subtitle track → start. Expected: progress through วิเคราะห์ตัวละคร → แปล → พร้อมตรวจ.
4. Close the app during แปล, reopen, add the same file, start. Expected: it continues from the saved chunk (cost does not restart from 0).
5. Export, open the video in VLC. Expected: VLC loads `.th.srt` automatically; Thai renders correctly; timing matches the source; in `.th.ass` each character has its own colour.
6. Switch to Claude Code (Max) and translate a second file. Expected: "ใช้โควตา Max" instead of a price; result exported.

- [ ] **Step 6: Commit**

```bash
git add tests/test_live.py SubTranslator.spec README.md
git commit -m "chore: live tests, PyInstaller build and README"
```

---

## Spec coverage map

| Spec section | Task |
|---|---|
| 2 Phase 1 scope: MKV/MP4 text tracks + side-car files | 3, 10, 14 |
| 3 Tech stack, .exe | 1, 12, 16 |
| 4 Structure / no gui imports in core | all (File Structure) |
| 5 Data model, `.stproj.json` | 1 |
| 6.1–6.2 Detect + extract, effect lines kept out | 2, 3 |
| 6.3 Cost estimate (count_tokens; Max shows quota) | 8, 10, 14 |
| 6.4 Pass 1 analysis | 6 |
| 6.5–6.6 Pass 2 chunks, continuity, id checks, retry | 7 |
| 6.7 Review/edit | 15 |
| 6.8 Export srt (BOM) + ass per-character colours, effects copied | 11 |
| Translation rules | 6 (`SYSTEM_TRANSLATE`) |
| 7.1 API backend | 8 |
| 7.2 Claude Code backend | 9 |
| 8 GUI pages, worker threads | 12–15 |
| 9 Error table | 7, 8, 9, 10, 14, 15 |
| 10 Tests | every task + 16 |
| 11 Done criteria | 16 Step 5 |
