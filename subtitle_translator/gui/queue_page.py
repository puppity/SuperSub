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
        except Exception:
            log.exception("estimate: backend unavailable")
            for row, _ in self.jobs:
                self.estimated.emit(row, None)
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
        self._est_queue: list[int] = []
        self._paused = False
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
        drop.setStyleSheet(f"border: 1.5px dashed {COLORS['stroke2']}; border-radius: 10px; padding: 16px; color: {COLORS['text2']};")
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
        if self.s.model in MODELS:
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
        elif not self._busy():
            card.setParent(None)
            del self.items[row], self.cards[row]
            for r, c in enumerate(self.cards):
                c.action.clicked.disconnect()
                c.action.clicked.connect(lambda _=False, rr=r: self._action(rr))
            self._refresh_total()

    def _busy(self) -> bool:
        """True while a background thread holds row indices (rows must not move)."""
        return bool((self.runner and self.runner.isRunning())
                    or (self.estimator and self.estimator.isRunning()) or self._est_queue)

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
        self._est_queue += [r for r in rows if r not in self._est_queue]
        self._kick_estimator()

    def _kick_estimator(self) -> None:
        if self.estimator is not None and self.estimator.isRunning():
            return                      # rows stay queued; started on finish
        rows, self._est_queue = self._est_queue, []
        if not rows:
            return
        est = EstimateRunner([(r, self.items[r]) for r in rows], self.s)
        est.estimated.connect(self._on_estimated)
        est.finished.connect(self._on_estimator_finished)
        self.estimator = est
        est.start()

    def _on_estimator_finished(self) -> None:
        if self.estimator is not None:
            self.estimator.wait()
            self.estimator = None
        self._kick_estimator()

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
        if not jobs or (self.runner is not None and self.runner.isRunning()):
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
        self.message.setText("กำลังแปล…")
        runner.start()

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

    def _on_paused(self, msg: str) -> None:
        self._paused = True
        self.message.setText(msg)

    def _on_finished(self) -> None:
        if self.runner is not None:
            self.runner.wait()          # never drop a thread that is still winding down
            self.runner = None
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.backend_box.setEnabled(True)
        self.model_box.setEnabled(True)
        if not self._paused:
            self.message.setText("เสร็จแล้ว — กด ตรวจ/แก้ ที่แต่ละไฟล์")

    def export_all(self) -> None:
        done = [it for it in self.items if it.project and it.project.stage == "done"]
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
        self.message.setText(f"ส่งออกแล้ว {written} ไฟล์")
