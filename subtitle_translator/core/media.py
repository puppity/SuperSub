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
