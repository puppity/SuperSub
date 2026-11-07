"""Playable preview copies of videos Qt cannot decode (AV1 without a hardware decoder).

The bundled ffmpeg decodes AV1 in software (libdav1d) and writes a small H.264 copy into the
app cache. The copy keeps the original timeline, so cue times line up. A copy is built once per
video file and reused; building runs in the background at low priority and can be started from
the queue (while the AI works) or from the review page.
"""
from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import threading
from pathlib import Path

from .media import _run, no_window, tool_path
from .settings import cache_dir

log = logging.getLogger(__name__)

UNPLAYABLE = {"av1"}
KEEP = 6                                  # newest proxies kept in the cache


def video_codec(video: str | Path) -> str | None:
    res = _run([tool_path("ffprobe"), "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(video)], timeout=60)
    name = res.stdout.decode("utf-8", "replace").strip()
    return name or None


def duration_of(video: str | Path) -> float:
    res = _run([tool_path("ffprobe"), "-v", "error", "-show_entries", "format=duration",
                "-of", "csv=p=0", str(video)], timeout=60)
    try:
        return float(res.stdout.decode().strip())
    except ValueError:
        return 0.0


def proxy_dir() -> Path:
    p = cache_dir() / "proxy"
    p.mkdir(parents=True, exist_ok=True)
    return p


def proxy_path(video: str | Path) -> Path:
    v = Path(video)
    st = v.stat()
    key = f"{v.resolve()}|{st.st_size}|{int(st.st_mtime)}"
    return proxy_dir() / (hashlib.sha1(key.encode("utf-8")).hexdigest()[:16] + ".mp4")


def needs_proxy(video: str | Path) -> bool:
    try:
        return (video_codec(video) or "") in UNPLAYABLE
    except Exception:
        log.exception("cannot probe %s", video)
        return False


class ProxyJob:
    def __init__(self, video: Path, out: Path):
        self.video, self.out = video, out
        self.fraction = 1.0 if out.exists() else 0.0
        self.error: str | None = None
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._cancel = False

    @property
    def ready(self) -> bool:
        return self.out.exists()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.ready or self.running:
            return
        self.error, self._cancel = None, False
        self._thread = threading.Thread(target=self._work, name="preview-proxy", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.kill()
        if self._thread is not None:
            self._thread.join(5)

    def _work(self) -> None:
        part = self.out.with_suffix(".part.mp4")
        total = max(1.0, duration_of(self.video))
        args = [tool_path("ffmpeg"), "-y", "-v", "error", "-nostats", "-progress", "pipe:1",
                "-c:v", "libdav1d", "-i", str(self.video),
                "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
                "-vf", "scale=-2:540", "-c:v", "libx264", "-preset", "ultrafast", "-tune", "fastdecode",
                "-crf", "27", "-g", "48", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(part)]
        flags = no_window().get("creationflags", 0)
        if os.name == "nt":
            flags |= subprocess.BELOW_NORMAL_PRIORITY_CLASS     # keep the GUI and the AI calls snappy
        try:
            self._proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                          creationflags=flags)
            for raw in self._proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                    try:
                        self.fraction = min(0.99, int(line.split("=", 1)[1]) / 1e6 / total)
                    except ValueError:
                        pass
            err = self._proc.stderr.read().decode("utf-8", "replace").strip()
            code = self._proc.wait()
            if self._cancel:
                return
            if code != 0 or not part.exists():
                self.error = "สร้างวิดีโอพรีวิวไม่สำเร็จ: " + err[-200:]
                log.warning("%s", self.error)
                return
            os.replace(part, self.out)
            self.fraction = 1.0
            _prune()
        except OSError as exc:
            self.error = f"สร้างวิดีโอพรีวิวไม่สำเร็จ: {exc}"
        finally:
            if not self.ready:
                try:
                    part.unlink()
                except OSError:
                    pass


_jobs: dict[Path, ProxyJob] = {}
_lock = threading.Lock()


def ensure_proxy(video: str | Path, start: bool = True) -> ProxyJob:
    """The shared job for this video (one per file), started unless the copy already exists."""
    out = proxy_path(video)
    with _lock:
        job = _jobs.get(out)
        if job is None:
            job = _jobs[out] = ProxyJob(Path(video), out)
    if start:
        job.start()
    return job


def stop_all() -> None:
    with _lock:
        jobs = list(_jobs.values())
    for job in jobs:
        job.cancel()


def _prune() -> None:
    files = sorted(proxy_dir().glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in [f for f in files if not f.name.endswith(".part.mp4")][KEEP:]:
        try:
            old.unlink()
        except OSError:
            pass
