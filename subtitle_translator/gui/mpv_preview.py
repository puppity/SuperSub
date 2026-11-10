"""Video preview backed by libmpv.

mpv decodes AV1 (dav1d) and everything else in software or hardware, so the original file
plays at once: no preview copy to build. mpv draws into a native child window; the Thai
caption is drawn by mpv itself as an ASS overlay (libass), so nothing has to sit on top of
that native window.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QWidget

from ..core.models import Cue

log = logging.getLogger(__name__)

_mpv = None          # the python-mpv module once libmpv loaded, False if it cannot load
CAPTION_FONT = "Leelawadee UI"


def _bin_dirs() -> list[Path]:
    return [Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "bin",
            Path(sys.executable).parent / "bin"]


def load_mpv():
    """The python-mpv module, or None when libmpv-2.dll is not available."""
    global _mpv
    if _mpv is None:
        try:
            for d in _bin_dirs():
                if (d / "libmpv-2.dll").exists():
                    if hasattr(os, "add_dll_directory"):
                        os.add_dll_directory(str(d))
                    os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
                    break
            import mpv  # noqa: PLC0415  (needs the DLL directory set first)
            _mpv = mpv
        except (OSError, ImportError) as exc:
            log.warning("libmpv unavailable, using the Qt player: %s", exc)
            _mpv = False
    return _mpv or None


def mpv_available() -> bool:
    return load_mpv() is not None


def ass_escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
            .replace("\r", "").replace("\n", "\\N"))


def ass_color(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&"


def caption_event(text: str, color: str) -> str:
    if not text:
        return ""
    style = (f"{{\\an2\\fn{CAPTION_FONT}\\b1\\fs54\\bord3.6\\shad0.8\\1c{ass_color(color)}"
             f"\\3c&H000000&\\4a&H60&\\pos(640,692)}}")   # bottom centre on a 1280x720 canvas
    return style + ass_escape(text)


class MpvPreview(QWidget):
    fallback_changed = Signal(bool)       # never True: mpv plays everything; kept for the page
    status_changed = Signal(str)
    position_changed = Signal(float)
    duration_changed = Signal(float)
    playing_changed = Signal(bool)

    POLL_MS = 40             # while playing
    IDLE_POLL_MS = 120       # paused: nothing moves, poll less
    SEEK_GAP_S = 0.05        # at most one seek per this long; newer requests replace waiting ones

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_NativeWindow)
        self.setAttribute(Qt.WA_DontCreateNativeAncestors)
        self.setStyleSheet("background: #0A0A0A;")
        self.setMinimumSize(420, 236)
        self.fallback = False
        self.path: str | None = None
        self._last_cue: Cue | None = None
        self._caption = ("", "#FFFFFF")
        self._player = None
        self._dur = 0.0
        self._was_playing = False
        self._last_pos = -1.0
        self._pending_seek: float | None = None
        self._queued: tuple[float, bool] | None = None
        self._last_seek = 0.0
        self._poll = QTimer(self)
        self._poll.setInterval(self.POLL_MS)
        self._poll.timeout.connect(self._tick)

    # ----- player lifetime -----
    def _ensure_player(self):
        if self._player is None:
            mpv = load_mpv()
            self._player = mpv.MPV(
                wid=str(int(self.winId())), vo="gpu", hwdec="auto-safe", keep_open="yes",
                idle="yes", osc=False, osd_level=0, input_default_bindings=False,
                input_vo_keyboard=False, input_cursor=False, cursor_autohide="no",
                hr_seek="yes", sub_auto="no", sid="no", audio_display="no",
                background_color="#0A0A0A", pause=True, load_scripts="no", ytdl="no",
                config="no", terminal="no")
        return self._player

    def set_source(self, path: str) -> None:
        self.path = path
        self._last_cue = None
        self._dur = 0.0
        self._last_pos = -1.0
        self._pending_seek = None
        self._queued = None
        p = self._ensure_player()
        p.pause = True
        p.loadfile(path)
        self.status_changed.emit("")
        self._poll.start()

    def shutdown(self, timeout_ms: int = 5000) -> bool:
        self._poll.stop()
        if self._player is not None:
            try:
                self._player.terminate()
            except Exception:                     # already gone
                log.exception("mpv terminate")
            self._player = None
        return True

    # ----- state -----
    @property
    def playing(self) -> bool:
        p = self._player
        return bool(p is not None and not p.pause and self.path)

    def _tick(self) -> None:
        p = self._player
        if p is None:
            return
        try:
            pos = p.time_pos
            dur = p.duration
            paused = p.pause
        except Exception:
            return
        if dur and abs(dur - self._dur) > 0.01:
            self._dur = float(dur)
            self.duration_changed.emit(self._dur)
            if self._pending_seek is not None:          # the file is open now: honour the seek
                p.seek(self._pending_seek, "absolute+exact")
                self._pending_seek = None
        if pos is not None and abs(pos - self._last_pos) > 0.001:
            self._last_pos = float(pos)
            self.position_changed.emit(self._last_pos)
        if self._queued is not None and time.monotonic() - self._last_seek >= self.SEEK_GAP_S:
            (seconds, exact), self._queued = self._queued, None
            self._seek(seconds, exact)
        playing = not paused
        if playing != self._was_playing:
            self._was_playing = playing
            self.playing_changed.emit(playing)
        if self._queued is None:
            self._poll.setInterval(self.POLL_MS if playing else self.IDLE_POLL_MS)

    # ----- commands -----
    def _seek(self, seconds: float, exact: bool = True) -> None:
        p = self._player
        if p is None or not self.path:
            return
        if not self._dur:
            self._pending_seek = seconds                 # still opening: mpv would drop it
            return
        now = time.monotonic()
        if now - self._last_seek < self.SEEK_GAP_S:
            # Seeks arriving faster than mpv can show them (dragging the bar) would queue up and
            # lag behind the hand. Keep only the newest; _tick sends it.
            self._queued = (seconds, exact)
            self._poll.setInterval(self.POLL_MS)
            return
        self._last_seek, self._queued = now, None
        try:
            p.seek(max(0.0, seconds), "absolute+exact" if exact else "absolute+keyframes")
        except Exception:
            self._pending_seek = seconds

    def show_cue(self, cue: Cue, text: str, color: str) -> None:
        self._last_cue = cue
        self.set_caption(text, color)
        if self._player is not None and not self.playing:
            self._player.pause = True
        self._seek(cue.start)

    def set_caption(self, text: str, color: str) -> None:
        self._caption = (text, color)
        p = self._player
        if p is None:
            return
        try:
            p.command("osd-overlay", id=1, format="ass-events", data=caption_event(text, color),
                      res_x=1280, res_y=720, z=0, hidden=False)
        except Exception:
            log.exception("caption overlay")

    def toggle(self) -> None:
        p = self._player
        if p is None or not self.path:
            return
        p.pause = not p.pause

    def stop(self) -> None:
        p = self._player
        if p is None or not self.path:
            return
        p.pause = True
        self._seek(self._last_cue.start if self._last_cue else 0.0)

    def seek(self, seconds: float) -> None:
        self._seek(seconds)

    def scrub(self, seconds: float) -> None:
        """Seek while the bar is being dragged: jump to the nearest keyframe, which is instant.
        An exact seek decodes from the keyframe up to the target, slow for AV1 in software."""
        self._seek(seconds, exact=False)
