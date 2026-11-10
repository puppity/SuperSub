from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import time

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ..ai.backend import AIError, AIErrorKind
from ..ai.factory import make_backend
from ..core.media import MediaError, SubTrack, lang_of, list_sub_tracks, pick_track, sidecar_subs
from ..core.proxy import ensure_proxy, needs_proxy
from .icons import set_icon
from .motion import animate_progress, polish_controls
from .mpv_preview import mpv_available
from ..core.transcribe import TRANSCRIBE, Transcribe, TranscribeError
from ..core.models import Project, untranslated_ids
from ..core.pricing import MODELS
from ..core.project_store import ProjectFileError
from ..core.settings import Settings, cache_dir
from ..core.subio import SubtitleReadError
from ..export import export_project
from ..translate.pipeline import Stopped, estimate_project_cost, prepare_project, run_project
from .theme import COLORS, set_tone

log = logging.getLogger(__name__)
VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".webm", ".avi"}
STOP_QUEUE = {AIErrorKind.AUTH, AIErrorKind.QUOTA, AIErrorKind.RATE_LIMIT,
              AIErrorKind.NOT_INSTALLED, AIErrorKind.NETWORK}


@dataclass
class QueueItem:
    video: Path
    source: SubTrack | Path | Transcribe | None = None
    meta: str = ""
    status: str = "รอคิว"
    project: Project | None = None
    estimate: float | None = None
    error: str | None = None


def describe_source(video: Path, tracks: list[SubTrack], sidecars: list[Path], prefer: list[str],
                    auto_transcribe: bool = True
                    ) -> tuple[SubTrack | Path | Transcribe | None, list[SubTrack], str]:
    chosen, cands = pick_track(tracks, tuple(prefer))
    if chosen is not None:
        name = {"ja": "ญี่ปุ่น", "en": "อังกฤษ"}.get(lang_of(chosen) or "", chosen.language)
        return chosen, [], f"แทร็กซับ {name} · {chosen.codec}"
    if sidecars:
        return sidecars[0], [], f"ไฟล์ซับข้างวิดีโอ · {sidecars[0].name}"
    if cands:
        return None, cands, f"มีแทร็กซับ {len(cands)} แทร็ก · เลือกแทร็ก"
    if not auto_transcribe:
        return None, [], ("ซับภาพ (PGS/VobSub) · เปิด “ถอดเสียง” ในตั้งค่าเพื่อทำต่อ" if tracks
                          else "ไม่พบซับ · เปิด “ถอดเสียง” ในตั้งค่าเพื่อทำต่อ")
    if tracks:
        return TRANSCRIBE, [], "ซับเป็นภาพ อ่านไม่ได้ · จะถอดเสียงแทน"
    return TRANSCRIBE, [], "ไม่พบซับ · จะถอดเสียงจากวิดีโอ"


def friendly(exc: Exception) -> str:
    if isinstance(exc, AIError) and exc.retry_after:
        return f"{exc} — ใช้ต่อได้หลัง {exc.retry_after}"
    return str(exc)


class QueueRunner(QThread):
    item_progress = Signal(int, str, float)
    item_done = Signal(int, object)
    item_failed = Signal(int, str)
    paused = Signal(str, str)                  # message, AIErrorKind value ("" when user-stopped)

    def __init__(self, jobs: list[tuple[int, QueueItem]], settings: Settings, parent=None):
        super().__init__(parent)
        self.jobs = jobs
        self.settings = replace(settings)      # snapshot: settings edited mid-run must not leak in
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        for row, item in self.jobs:
            if self._stop:
                self.paused.emit("หยุดแล้ว — กดเริ่มเพื่อทำต่อ", "")
                return
            try:
                backend = make_backend(self.settings)
                # Without mpv, build a playable preview copy while the AI works.
                if not mpv_available() and needs_proxy(item.video):
                    ensure_proxy(item.video)
                report = lambda stage, f, r=row: self.item_progress.emit(r, stage, f)
                project = prepare_project(item.video, item.source, self.settings, cache_dir(),
                                          report, lambda: self._stop)
                project = run_project(project, backend, self.settings,
                                      lambda stage, f, r=row: self.item_progress.emit(r, stage, f),
                                      lambda: self._stop)
                self.item_done.emit(row, project)
            except Stopped:
                self.paused.emit("หยุดแล้ว — กดเริ่มเพื่อทำต่อ", "")
                return
            except AIError as exc:
                self.item_failed.emit(row, friendly(exc))
                if exc.kind in STOP_QUEUE:
                    self.paused.emit(friendly(exc), exc.kind.value)
                    return
            except (MediaError, SubtitleReadError, ProjectFileError) as exc:
                self.item_failed.emit(row, str(exc))
            except Exception as exc:
                log.exception("unexpected failure on %s", item.video)
                self.item_failed.emit(row, f"ผิดพลาดที่ไม่คาดคิด: {exc}")


class ProbeRunner(QThread):
    """Runs ffprobe for a list of videos off the GUI thread."""
    probed = Signal(object, object, str)       # video, tracks | None, error

    def __init__(self, jobs: list[Path], parent=None):
        super().__init__(parent)
        self.jobs = jobs
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        for video in self.jobs:
            if self._stop:
                return
            try:
                self.probed.emit(video, list_sub_tracks(video), "")
            except MediaError as exc:
                self.probed.emit(video, None, str(exc))
            except Exception as exc:
                log.exception("probe failed for %s", video)
                self.probed.emit(video, None, f"ผิดพลาดที่ไม่คาดคิด: {exc}")


class EstimateRunner(QThread):
    estimated = Signal(int, object)

    def __init__(self, jobs: list[tuple[int, QueueItem]], settings: Settings, parent=None):
        super().__init__(parent)
        self.jobs = jobs
        self.settings = replace(settings)      # snapshot: model changes must not leak in mid-run
        self.model = self.settings.model
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            backend = make_backend(self.settings)
        except Exception:
            log.exception("estimate: backend unavailable")
            for row, _ in self.jobs:
                self.estimated.emit(row, None)
            return
        for row, item in self.jobs:
            if self._stop:
                return
            try:
                project = prepare_project(item.video, item.source, self.settings, cache_dir(),
                                          allow_transcribe=False)
                self.estimated.emit(row, estimate_project_cost(project, backend))
            except TranscribeError:             # cost is known only after the audio is transcribed
                self.estimated.emit(row, None)
            except Exception:
                log.exception("estimate failed for %s", item.video)
                self.estimated.emit(row, None)


def status_tone(item: QueueItem) -> str | None:
    if item.error or item.status == "ผิดพลาด":
        return "bad"
    if item.project is not None and item.project.stage == "done":
        return "warn" if "ต้องแปลเอง" in item.status else "good"
    if "%" in item.status:
        return "busy"
    return None


class FileCard(QFrame):
    def __init__(self, on_action, parent=None):
        super().__init__(parent)
        self.setProperty("card", True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 14, 14)
        lay.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(12)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.name = QLabel()
        self.name.setProperty("role", "h2")
        self.meta = QLabel()
        self.meta.setProperty("role", "muted")
        self.meta.setWordWrap(True)
        text.addWidget(self.name)
        text.addWidget(self.meta)
        top.addLayout(text, 1)
        self.status = QLabel()
        self.status.setProperty("role", "pill")
        self.cost = QLabel()
        self.cost.setProperty("role", "muted")
        self.cost.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.action = QPushButton()
        self.action.setCursor(Qt.PointingHandCursor)
        self.action.clicked.connect(on_action)
        top.addWidget(self.status, 0, Qt.AlignVCenter)
        top.addWidget(self.cost, 0, Qt.AlignVCenter)
        top.addWidget(self.action, 0, Qt.AlignVCenter)
        lay.addLayout(top)
        self.bar = QProgressBar(textVisible=False, maximum=1000)
        self.bar.setVisible(False)
        lay.addWidget(self.bar)
        self._anim = QPropertyAnimation(self.bar, b"value", self)
        self._anim.setDuration(350)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def set_progress(self, value: int) -> None:
        """Glide the bar to `value` instead of jumping, so progress reads as motion."""
        self.bar.setVisible(True)
        self._anim.stop()
        if value <= self.bar.value():
            animate_progress(self.bar, value)
            return
        self._anim.setStartValue(self.bar.value())
        self._anim.setEndValue(value)
        self._anim.start()

    def update_from(self, item: QueueItem) -> None:
        self.name.setText(item.video.name)
        meta = item.error or item.meta
        if self.meta.text() != meta:
            self.meta.setText(meta)
        if self.status.text() != item.status:
            self.status.setText(item.status)
        set_tone(self.status, status_tone(item))
        if item.project and item.project.cost_usd:
            cost = f"${item.project.cost_usd:.2f}"
        elif item.estimate is not None:
            cost = f"~${item.estimate:.2f}"
        else:
            cost = ""                       # no number to show (e.g. Claude Max): show nothing, not a dash
        self.cost.setText(cost)
        self.cost.setVisible(bool(cost))
        done = item.project is not None and item.project.stage == "done"
        if done:
            label = "ตรวจ/แก้"
        elif item.source is None and "เลือก" in item.meta:
            label = "เลือกแทร็ก"
        else:
            label = "ลบ"
        self.action.setText(label)
        primary = label != "ลบ"
        if self.action.property("accent") != primary:
            self.action.setProperty("accent", primary)
            self.action.setProperty("flat", not primary)
            self.action.style().unpolish(self.action)
            self.action.style().polish(self.action)


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
        self._est_queue: list[int] = []
        self.prober: ProbeRunner | None = None
        self._probe_queue: list[Path] = []
        self._new_rows: list[int] = []
        self._closing = False
        self._paused = False
        # One AI call (e.g. whole-episode analysis) can run for minutes with no progress
        # event; a ticking elapsed time shows the run is alive, not frozen.
        self._stage: dict[int, tuple[str, float, float]] = {}    # row -> (stage, frac, started)
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._refresh_elapsed)
        self.setAcceptDrops(True)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(32, 28, 32, 28)
        outer.setSpacing(24)

        left = QVBoxLayout()
        head = QHBoxLayout()
        title = QLabel("คิวงาน")
        title.setProperty("role", "h1")
        head.addWidget(title, 1)
        add_files = QPushButton("เพิ่มไฟล์")
        set_icon(add_files, "plus")
        add_files.clicked.connect(self._pick_files)
        add_dir = QPushButton("เพิ่มโฟลเดอร์")
        set_icon(add_dir, "folder")
        add_dir.clicked.connect(self._pick_dir)
        head.addWidget(add_files)
        head.addWidget(add_dir)
        left.addLayout(head)
        drop = QLabel("ลากไฟล์ .mkv หรือทั้งโฟลเดอร์มาวางที่นี่ · ระบบหาแทร็กซับญี่ปุ่น/อังกฤษ และไฟล์ .srt/.ass ข้างวิดีโอให้เอง")
        drop.setWordWrap(True)
        drop.setStyleSheet("border: 1.5px dashed #3C3C3C; border-radius: 12px; padding: 18px; color: #C8C8C8;")
        left.addWidget(drop)
        self.list_box = QVBoxLayout()
        self.list_box.setSpacing(10)
        self.empty = QLabel("ยังไม่มีไฟล์ในคิว\nเริ่มจากกด “เพิ่มไฟล์” หรือลากวิดีโอมาวาง")
        self.empty.setProperty("role", "empty")
        self.empty.setAlignment(Qt.AlignCenter)
        self.list_box.addWidget(self.empty)
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
        sl.setContentsMargins(22, 22, 22, 22)
        sl.setSpacing(8)
        h = QLabel("สรุปก่อนเริ่ม")
        h.setProperty("role", "h2")
        sl.addWidget(h)
        sl.addWidget(self._field_label("เชื่อมต่อผ่าน"))
        self.backend_box = QComboBox()
        self.backend_box.addItems(["Anthropic API key", "Claude Code (บัญชี Max)"])
        self.backend_box.setCurrentIndex(1 if self.s.backend == "claude_code" else 0)
        self.backend_box.currentIndexChanged.connect(self._backend_changed)
        sl.addWidget(self.backend_box)
        sl.addWidget(self._field_label("โมเดลแปล"))
        self.model_box = QComboBox()
        for mid, info in MODELS.items():
            self.model_box.addItem(info.label + (" · แนะนำ" if info.recommended else ""), mid)
        if self.s.model in MODELS:
            self.model_box.setCurrentIndex(list(MODELS).index(self.s.model))
        self.model_box.currentIndexChanged.connect(self._model_changed)
        sl.addWidget(self.model_box)
        sl.addWidget(self._field_label("โหมดวิเคราะห์ตัวละคร"))
        self.mode_box = QComboBox()
        self.mode_box.addItem("ปกติ · อ่านจากซับอย่างเดียว", False)
        self.mode_box.addItem("อ่านภาพ · ดูหน้าตัวละครจากวิดีโอด้วย", True)
        self.mode_box.setCurrentIndex(1 if self.s.vision_mode else 0)
        self.mode_box.setToolTip("โหมดอ่านภาพเก็บภาพจากวิดีโอราว 36 ภาพ ให้ AI ดูหน้า อายุ ฝ่ายดี/ร้าย "
                                 "แล้วเลือกคำแทนตัว (ผม/ฉัน/ข้า/แก) ให้เหมาะ · จำหน้าตัวละครไว้ใช้ตอนต่อไป "
                                 "· ใช้ token เพิ่มราว 10k ต่อตอน")
        self.mode_box.currentIndexChanged.connect(self._mode_changed)
        sl.addWidget(self.mode_box)
        sl.addWidget(self._field_label("ค่าใช้จ่ายโดยประมาณ"))
        self.total = QLabel()
        self.total.setProperty("role", "stat")
        self.total.setWordWrap(True)
        sl.addWidget(self.total)
        self.est_btn = QPushButton("ประมาณราคา")
        set_icon(self.est_btn, "coins")
        self.est_btn.clicked.connect(self.estimate)
        sl.addWidget(self.est_btn)
        sl.addSpacing(6)
        self.start_btn = QPushButton("เริ่มแปลทั้งคิว")
        self.start_btn.setProperty("accent", True)
        set_icon(self.start_btn, "play", COLORS["on_accent"])
        self.start_btn.clicked.connect(self.start)
        self.stop_btn = QPushButton("หยุด", enabled=False)
        set_icon(self.stop_btn, "stop")
        self.stop_btn.clicked.connect(self.stop)
        export_all = QPushButton("ส่งออกไฟล์ที่เสร็จทั้งหมด")
        set_icon(export_all, "export")
        export_all.clicked.connect(self.export_all)
        for b in (self.start_btn, self.stop_btn, export_all):
            b.setCursor(Qt.PointingHandCursor)
            sl.addWidget(b)
        sl.addSpacing(6)
        self.message = QLabel("หยุดได้ทุกเมื่อ ผลที่เสร็จแล้วบันทึกไว้ กลับมาทำต่อไม่เสียเงินซ้ำ")
        self._idle_message = self.message.text()
        self.message.setWordWrap(True)
        self.message.setProperty("role", "hint")
        sl.addWidget(self.message)
        self.login_btn = QPushButton("เปิด Terminal เพื่อล็อกอิน Claude")
        set_icon(self.login_btn, "terminal")
        self.login_btn.setCursor(Qt.PointingHandCursor)
        self.login_btn.clicked.connect(self.open_login)
        self.login_btn.setVisible(False)
        sl.addWidget(self.login_btn)
        sl.addStretch(1)
        outer.addWidget(side)
        self._refresh_total()

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "field")
        return label

    def _say(self, text: str, bad: bool = False) -> None:
        self.message.setText(text)
        set_tone(self.message, "bad" if bad else None)
        if not bad:
            self.login_btn.setVisible(False)

    def open_login(self) -> None:
        """Open a console running `claude` so the user can sign in to their Max account."""
        exe = shutil.which("claude")
        if exe is None:
            self._say("ไม่พบคำสั่ง claude — ติดตั้ง Claude Code ก่อน แล้วลองใหม่", bad=True)
            return
        if sys.platform == "win32":
            subprocess.Popen(["cmd", "/k", exe], creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            subprocess.Popen([exe])
        self._say("ล็อกอินในหน้าต่าง Terminal ให้เสร็จ แล้วกด “เริ่มแปลทั้งคิว” อีกครั้ง")

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
        if self._closing:
            return
        videos: list[Path] = []
        for p in paths:
            if p.is_dir():
                videos += sorted(x for x in p.rglob("*") if x.suffix.lower() in VIDEO_EXT)
            elif p.suffix.lower() in VIDEO_EXT:
                videos.append(p)
        known = {i.video for i in self.items} | set(self._probe_queue)
        if self.prober is not None:
            known |= set(self.prober.jobs)
        for v in videos:
            if v not in known:
                known.add(v)
                self._probe_queue.append(v)
        self._kick_probe()

    def _kick_probe(self) -> None:
        if self._closing or self.prober is not None:
            return                      # queued paths start when the current probe's finished handler runs
        jobs, self._probe_queue = self._probe_queue, []
        if not jobs:
            return
        probe = ProbeRunner(jobs)
        probe.probed.connect(self._on_probed)
        probe.finished.connect(self._on_probe_finished)
        self.prober = probe
        self.start_btn.setEnabled(False)
        self._say("กำลังตรวจแทร็กซับ…")
        probe.start()

    def _on_probed(self, v: Path, tracks, error: str) -> None:
        try:
            if tracks is None:
                self._append(QueueItem(video=v, status="ผิดพลาด", error=error))
                return
            source, cands, meta = describe_source(v, tracks, sidecar_subs(v), self.s.prefer_langs,
                                                  self.s.auto_transcribe)
            item = QueueItem(video=v, source=source, meta=meta,
                             status="รอคิว" if source is not None else "ข้าม")
            self._append(item, cands)
            if item.source is not None:
                self._new_rows.append(len(self.items) - 1)
        except Exception as exc:        # a bad file must never take the GUI down
            log.exception("could not add %s", v)
            self._append(QueueItem(video=v, status="ผิดพลาด", error=f"ผิดพลาดที่ไม่คาดคิด: {exc}"))

    def _on_probe_finished(self) -> None:
        if self.prober is not None:
            self.prober.wait()
            self.prober = None
        self._kick_probe()
        if self.prober is not None:
            return
        rows, self._new_rows = self._new_rows, []
        if rows and self.s.backend == "api":
            self._estimate_rows(rows)
        running = self.runner is not None
        self.start_btn.setEnabled(not running)
        if not running:
            self._say(self._idle_message)
        self._refresh_total()

    def _append(self, item: QueueItem, cands: list[SubTrack] | None = None) -> None:
        self.items.append(item)
        row = len(self.items) - 1
        card = FileCard(lambda _=False, r=row: self._action(r))
        polish_controls(card)
        card._cands = cands or []
        self.cards.append(card)
        self.list_box.insertWidget(self.list_box.count() - 1, card)
        self.empty.setVisible(False)
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
        elif not self._busy():
            card.setParent(None)
            del self.items[row], self.cards[row]
            for r, c in enumerate(self.cards):
                c.action.clicked.disconnect()
                c.action.clicked.connect(lambda _=False, rr=r: self._action(rr))
            self.empty.setVisible(not self.cards)
            self._refresh_total()

    def _busy(self) -> bool:
        """True while a background thread holds row indices (rows must not move)."""
        # A thread counts as busy until its finished handler has cleared it, not merely until
        # isRunning() turns False: results queued by the thread are delivered before that handler.
        return bool(self.runner is not None or self.estimator is not None or self._est_queue
                    or self.prober is not None or self._probe_queue)

    # ----- settings shortcuts -----
    def _backend_changed(self, index: int) -> None:
        self.s.backend = "claude_code" if index == 1 else "api"
        self._persist()
        self._refresh_total()

    def _mode_changed(self, index: int) -> None:
        self.s.vision_mode = bool(self.mode_box.itemData(index))
        self._persist()

    def _model_changed(self, index: int) -> None:
        self.s.model = self.model_box.itemData(index)
        self._persist()
        for it in self.items:
            it.estimate = None
        for card, it in zip(self.cards, self.items):
            card.update_from(it)
        self._refresh_total()
        if self.s.backend == "api":
            self.estimate()

    # ----- estimate -----
    def estimate(self) -> None:
        self._estimate_rows([r for r, it in enumerate(self.items) if it.source is not None])

    def _estimate_rows(self, rows: list[int]) -> None:
        if self.s.backend != "api" or not rows:
            self._refresh_total()
            return
        self._est_queue += [r for r in rows if r not in self._est_queue]
        self._kick_estimator()

    def _kick_estimator(self) -> None:
        if self._closing or self.estimator is not None:
            return                      # rows stay queued; started on finish
        if self.runner is not None:
            return                      # never estimate while a run may write the same project files
        rows, self._est_queue = self._est_queue, []
        if not rows:
            return
        est = EstimateRunner([(r, self.items[r]) for r in rows], self.s)
        est.estimated.connect(lambda r, v, m=est.model: self._on_estimated(r, v, m))
        est.finished.connect(self._on_estimator_finished)
        self.estimator = est
        est.start()

    def _on_estimator_finished(self) -> None:
        if self.estimator is not None:
            self.estimator.wait()
            self.estimator = None
        self._kick_estimator()

    def _on_estimated(self, row: int, value, model: str | None = None) -> None:
        if model is not None and model != self.s.model:
            return                      # computed for a model the user has since left
        self.items[row].estimate = value
        self.cards[row].update_from(self.items[row])
        self._refresh_total()

    def _refresh_total(self) -> None:
        max_plan = self.s.backend == "claude_code"
        self.est_btn.setVisible(not max_plan)
        if max_plan:
            self.total.setText("ไม่เสียเงินเพิ่ม · ใช้โควตา Max")
            return
        total = sum(it.estimate or 0 for it in self.items if it.source is not None)
        self.total.setText(f"~${total:.2f}")

    # ----- run -----
    def start(self) -> None:
        jobs = [(r, it) for r, it in enumerate(self.items)
                if it.source is not None and not (it.project and it.project.stage == "done")]
        if not jobs or self._closing or self.runner is not None:
            return
        if self.prober is not None or self._probe_queue:
            self._say("กำลังตรวจแทร็กซับ รอสักครู่…")
            return
        if self.estimator is not None:
            self._say("กำลังประมาณราคา รอสักครู่…")
            return
        runner = QueueRunner(jobs, self.s)
        runner.item_progress.connect(self._on_progress)
        runner.item_done.connect(self._on_done)
        runner.item_failed.connect(self._on_failed)
        runner.paused.connect(self._on_paused)
        runner.finished.connect(self._on_finished)
        self.runner = runner
        self._paused = False
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.backend_box.setEnabled(False)
        self.model_box.setEnabled(False)
        self._say("กำลังแปล…")
        runner.start()

    def stop(self) -> None:
        if self.runner:
            self.runner.stop()
            self._say("กำลังหยุดหลังจบช่วงปัจจุบัน…")

    def _on_progress(self, row: int, stage: str, frac: float) -> None:
        item = self.items[row]
        prev = self._stage.get(row)
        started = prev[2] if prev and prev[0] == stage else time.monotonic()
        self._stage = {row: (stage, frac, started)}
        if not self._tick.isActive():
            self._tick.start()
        item.status, item.error = self._status_text(stage, frac, started), None
        self.cards[row].set_progress(int(frac * 1000))
        self.cards[row].update_from(item)

    @staticmethod
    def _status_text(stage: str, frac: float, started: float) -> str:
        secs = int(time.monotonic() - started)
        text = f"{stage} {int(frac * 100)}%"
        return text + (f" · {secs // 60}:{secs % 60:02d}" if secs >= 5 else "")

    def _refresh_elapsed(self) -> None:
        for row, (stage, frac, started) in self._stage.items():
            if row < len(self.items):
                self.items[row].status = self._status_text(stage, frac, started)
                self.cards[row].update_from(self.items[row])

    def _stop_elapsed(self) -> None:
        self._tick.stop()
        self._stage = {}

    def _on_done(self, row: int, project) -> None:
        self._stop_elapsed()
        item = self.items[row]
        item.project, item.status = project, "พร้อมตรวจ"
        failed = sum(1 for c in project.episode.cues if c.status == "failed")
        if failed:
            item.status = f"พร้อมตรวจ · ต้องแปลเอง {failed} บรรทัด"
        self.cards[row].set_progress(1000)
        self.cards[row].update_from(item)

    def _on_failed(self, row: int, msg: str) -> None:
        self._stop_elapsed()
        item = self.items[row]
        item.status, item.error = "ผิดพลาด", msg
        self.cards[row].update_from(item)

    def _on_paused(self, msg: str, kind: str = "") -> None:
        self._paused = True
        self._say(msg, bad=bool(kind))
        needs_login = kind in (AIErrorKind.AUTH.value, AIErrorKind.NOT_INSTALLED.value)
        needs_login = needs_login and self.s.backend == "claude_code"
        self.login_btn.setVisible(needs_login)
        if needs_login:
            self._prompt_login(msg)

    def _prompt_login(self, msg: str) -> None:
        """Pop the sign-in prompt by itself instead of leaving the user to find the hint.

        Non-modal (open(), not exec()) so a queued finished signal is never stuck behind it.
        """
        if getattr(self, "_login_box", None) is not None and self._login_box.isVisible():
            return
        box = QMessageBox(QMessageBox.Information, "ต้องล็อกอิน Claude ก่อน",
                          "โหมด Claude Max ต้องล็อกอินบัญชี Claude ในเครื่องนี้ก่อนแปล\n\n"
                          "กด “ล็อกอินตอนนี้” โปรแกรมจะเปิดหน้าต่าง Terminal ให้ "
                          "ทำตามขั้นตอนในหน้าต่างนั้นจนเสร็จ แล้วกลับมากด “เริ่มแปลทั้งคิว” อีกครั้ง",
                          parent=self)
        box.setInformativeText(msg)
        login = box.addButton("ล็อกอินตอนนี้", QMessageBox.AcceptRole)
        box.addButton("ไว้ทีหลัง", QMessageBox.RejectRole)
        box.setDefaultButton(login)
        box.setAttribute(Qt.WA_DeleteOnClose)
        box.buttonClicked.connect(lambda btn: self.open_login() if btn is login else None)
        box.finished.connect(lambda _=0: setattr(self, "_login_box", None))   # box deletes itself on close
        self._login_box = box
        box.open()

    def _on_finished(self) -> None:
        self._stop_elapsed()
        if self.runner is not None:
            self.runner.wait()          # never drop a thread that is still winding down
            self.runner = None
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.backend_box.setEnabled(True)
        self.model_box.setEnabled(True)
        if self.prober is not None or self._probe_queue:
            self.start_btn.setEnabled(False)
        self._kick_estimator()
        if not self._paused:
            self._say("เสร็จแล้ว — กด ตรวจ/แก้ ที่แต่ละไฟล์")

    def shutdown(self, timeout_ms: int = 5000) -> bool:
        """Stop and join every background thread. True when nothing is left running."""
        self._closing = True
        self._probe_queue, self._est_queue = [], []
        threads = [t for t in (self.runner, self.estimator, self.prober) if t is not None]
        for t in threads:
            t.stop()
        for t in threads:
            t.wait(timeout_ms)
        return not any(t.isRunning() for t in threads)

    def reopen(self) -> None:
        """Allow new work again after a refused close."""
        self._closing = False

    def export_all(self) -> None:
        done = [it for it in self.items if it.project and it.project.stage == "done"]
        missing = sum(len(untranslated_ids(it.project.episode.cues)) for it in done)
        if missing and QMessageBox.question(
                self, "ยังมีบรรทัดที่ไม่ได้แปล",
                f"มี {missing} บรรทัดที่ยังไม่ได้แปล จะใส่ข้อความต้นฉบับแทน — ส่งออกต่อไหม?") != QMessageBox.Yes:
            return
        written = 0
        for it in done:
            try:
                try:
                    export_project(it.project, self.s)
                except FileExistsError as exc:
                    ans = QMessageBox.question(self, "มีไฟล์อยู่แล้ว", f"เขียนทับ {exc}?")
                    if ans != QMessageBox.Yes:
                        continue
                    export_project(it.project, self.s, overwrite=True)
            except (ValueError, OSError) as exc:
                log.warning("export failed for %s: %s", it.video, exc)
                it.error = f"ส่งออกไม่สำเร็จ: {exc}"
                self.cards[self.items.index(it)].update_from(it)
                continue
            written += 1
        self._say(f"ส่งออกแล้ว {written} ไฟล์")
