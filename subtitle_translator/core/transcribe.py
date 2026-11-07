"""Speech-to-text for videos that ship without subtitles (faster-whisper, runs locally).

The model is downloaded once into the app cache on first use. On an NVIDIA GPU it runs in
float16; without one it falls back to the CPU (int8), which works but is much slower.
"""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import pysubs2

from .media import MediaError, _run, tool_path
from .settings import cache_dir

log = logging.getLogger(__name__)

MODELS = {"large-v3-turbo": "เร็ว (แนะนำ)", "large-v3": "แม่นสุด แต่ช้ากว่า"}
DEFAULT_MODEL = "large-v3-turbo"
MAX_CUE_S = 6.0
MAX_CHARS = {"ja": 40, "zh": 36, "ko": 44}      # per cue, about two subtitle lines
MAX_CHARS_DEFAULT = 84


class TranscribeError(MediaError):
    pass


class Transcribe:
    """Queue-source marker: no subtitle exists, make one from the audio."""
    _inst = None

    def __new__(cls):
        if cls._inst is None:
            cls._inst = super().__new__(cls)
        return cls._inst

    def __repr__(self) -> str:
        return "TRANSCRIBE"


TRANSCRIBE = Transcribe()


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word]


def models_dir() -> Path:
    p = cache_dir() / "whisper"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _add_cuda_dll_dirs() -> None:
    """pip's nvidia-cublas/cudnn wheels put their DLLs in site-packages/nvidia/*/bin."""
    if os.name != "nt":
        return
    roots = [Path(getattr(sys, "_MEIPASS", "")) / "nvidia"] + [Path(p) / "nvidia" for p in sys.path]
    for root in roots:
        if not root.is_dir():
            continue
        for bin_dir in root.glob("*/bin"):
            try:
                os.add_dll_directory(str(bin_dir))
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
            except OSError:
                pass


def gpu_available() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def load_model(name: str):
    """Return a faster_whisper.WhisperModel (downloads it the first time)."""
    _add_cuda_dll_dirs()
    from faster_whisper import WhisperModel
    if gpu_available():
        try:
            return WhisperModel(name, device="cuda", compute_type="float16",
                                download_root=str(models_dir()))
        except Exception as exc:          # missing cuDNN/cuBLAS, too little VRAM...
            log.warning("GPU whisper failed, using CPU: %s", exc)
    return WhisperModel(name, device="cpu", compute_type="int8", download_root=str(models_dir()))


def extract_audio(video: str | Path, out_wav: str | Path) -> Path:
    out_wav = Path(out_wav)
    res = _run([tool_path("ffmpeg"), "-y", "-v", "error", "-i", str(video), "-vn", "-ac", "1",
                "-ar", "16000", "-c:a", "pcm_s16le", str(out_wav)], timeout=1800)
    if res.returncode != 0 or not out_wav.exists():
        raise TranscribeError("ดึงเสียงจากวิดีโอไม่ได้: "
                              + res.stderr.decode("utf-8", "replace").strip()[-300:])
    return out_wav


def read_wav(path: str | Path):
    """16 kHz mono PCM → float32 array. Done here so faster-whisper never needs PyAV."""
    import wave

    import numpy as np
    with wave.open(str(path), "rb") as w:
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0


def _joined(words: list[Word], lang: str) -> str:
    # Whisper words carry their own leading space in spaced languages, none in Japanese.
    return "".join(w.text for w in words).strip()


BREAK_AFTER = tuple("、。！？!?,.…")


def _is_break(prev: Word, nxt: Word) -> bool:
    """A natural place to cut: after punctuation or before a spoken pause / space."""
    return (prev.text.rstrip().endswith(BREAK_AFTER) or nxt.text.startswith((" ", "　"))
            or nxt.start - prev.end >= 0.3)


def _cut(words: list[Word], lang: str, limit: int) -> list[list[Word]]:
    """Split one segment's words into pieces that fit, preferring natural break points."""
    if (words[-1].end - words[0].start <= MAX_CUE_S and len(_joined(words, lang)) <= limit):
        return [words]
    # longest prefix that still fits
    fit = 1
    while fit < len(words) and (words[fit].end - words[0].start <= MAX_CUE_S
                                and len(_joined(words[:fit + 1], lang)) <= limit):
        fit += 1
    # back up to the last natural break in the second half of that prefix
    cut = fit
    for i in range(fit, max(1, fit // 2) - 1, -1):
        if i < len(words) and _is_break(words[i - 1], words[i]):
            cut = i
            break
    rest = words[cut:]
    # never leave a tiny tail on its own: let this piece run a little long instead
    if rest and len(_joined(rest, lang)) <= limit // 4 and len(_joined(words, lang)) <= limit * 1.25             and words[-1].end - words[0].start <= MAX_CUE_S * 1.25:
        return [words]
    return [words[:cut]] + (_cut(rest, lang, limit) if rest else [])


def split_segments(segments: Iterable[Segment], lang: str) -> list[tuple[float, float, str]]:
    """Cut Whisper segments into subtitle-sized cues (about MAX_CUE_S and two lines), at word edges."""
    limit = MAX_CHARS.get(lang, MAX_CHARS_DEFAULT)
    out: list[tuple[float, float, str]] = []
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        if not seg.words or (seg.end - seg.start <= MAX_CUE_S and len(text) <= limit):
            out.append((seg.start, seg.end, text))
            continue
        for piece in _cut(list(seg.words), lang, limit):
            out.append((piece[0].start, piece[-1].end, _joined(piece, lang)))
    return [(s, e, t) for s, e, t in out if t]


def transcribe(video: str | Path, out_dir: str | Path, model_name: str = DEFAULT_MODEL,
               progress: Callable[[str, float], None] = lambda s, f: None,
               should_stop: Callable[[], bool] = lambda: False,
               prefer_langs: list[str] | None = None, model=None) -> tuple[Path, str]:
    """Write ``<video stem>.whisper.srt`` in out_dir. Returns (path, detected language)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(video).stem
    wav = out_dir / f"{stem}.16k.wav"
    progress("ดึงเสียง", 0.0)
    extract_audio(video, wav)
    try:
        if model is None:
            cached = any(model_name in d.name for d in models_dir().iterdir())
            progress("โหลดโมเดลถอดเสียง" if cached
                     else "ดาวน์โหลดโมเดลถอดเสียง (ครั้งแรกครั้งเดียว ~1.6–3GB)", 0.0)
            model = load_model(model_name)
        progress("ถอดเสียง", 0.0)
        seg_iter, info = model.transcribe(read_wav(wav), vad_filter=True, word_timestamps=True,
                                          condition_on_previous_text=False, beam_size=5)
        lang = info.language or "ja"
        duration = max(1.0, float(info.duration or 1.0))
        segments: list[Segment] = []
        for s in seg_iter:                       # generator: the work happens while iterating
            if should_stop():
                from ..translate.pipeline import Stopped
                raise Stopped()
            words = [Word(w.start, w.end, w.word) for w in (s.words or [])]
            segments.append(Segment(s.start, s.end, s.text, words))
            progress("ถอดเสียง", min(1.0, s.end / duration))
    finally:
        try:
            wav.unlink()
        except OSError:
            pass
    cues = split_segments(segments, lang)
    if not cues:
        raise TranscribeError("ถอดเสียงแล้วไม่พบคำพูดในวิดีโอ")
    subs = pysubs2.SSAFile()
    for start, end, text in cues:
        subs.append(pysubs2.SSAEvent(start=int(start * 1000), end=int(end * 1000), text=text))
    path = out_dir / f"{stem}.whisper.srt"
    subs.save(str(path), format_="srt")
    return path, lang
