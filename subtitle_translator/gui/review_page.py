from __future__ import annotations

import logging
import re
from pathlib import Path

from PySide6.QtCore import QSizeF, Qt, QTimer, QUrl
from PySide6.QtGui import QBrush, QColor, QFont, QKeySequence, QPen, QPixmap, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QColorDialog, QComboBox, QFrame, QGraphicsPixmapItem, QGraphicsScene,
    QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..ai.factory import make_backend
from ..core.media import MediaError, grab_frame
from ..core.models import Cue, Project, untranslated_ids
from ..core.project_store import save_project
from ..core.settings import Settings, cache_dir
from ..export import export_project
from ..translate.analyze import UNKNOWN_COLOR
from ..translate.translate import make_chunks, translate_chunk
from .theme import COLORS
from .timeline_widget import TimelineWidget
from .worker import CallWorker

log = logging.getLogger(__name__)
UNSURE_THRESHOLD = 0.6
CPS_LIMIT = 17
RETRY_ALL_TEXT = "แปลบรรทัดที่ล้มเหลวใหม่ทั้งหมด"
_HEX_COLOR = re.compile(r"#[0-9A-F]{6}")


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
        self._want: tuple[str, float] | None = None     # latest requested still (path, time)
        self._grab: CallWorker | None = None
        self._started: tuple[str, float] | None = None
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(150)
        self._debounce.timeout.connect(self._start_grab)

    def set_source(self, path: str) -> None:
        self.path, self.fallback = path, False
        self._want = None
        self.still.setPixmap(QPixmap())
        self._load_media(path)

    def _load_media(self, path: str) -> None:
        self.player.setSource(QUrl.fromLocalFile(path))

    def _on_error(self, *_):
        log.warning("video playback failed, using still frames: %s", self.player.errorString())
        self.fallback = True

    def show_cue(self, cue: Cue, text: str, color: str) -> None:
        self.text.setText(text)
        self.text.setBrush(QBrush(QColor(color)))
        if self.fallback and self.path:
            self._want = (self.path, (cue.start + cue.end) / 2)
            self._debounce.start()
        else:
            self.player.pause()
            self.player.setPosition(int(cue.start * 1000))
        self._layout()

    def _start_grab(self) -> None:
        if self._grab is not None:
            return                      # the finish handler re-runs with the latest request
        if self._want is None:
            return
        path, t = self._want
        self._started = self._want
        out = cache_dir() / "preview.png"
        worker = CallWorker(lambda: grab_frame(path, t, out))
        worker.done.connect(lambda png, k=(path, t): self._on_frame(k, png))
        worker.failed.connect(lambda exc, k=(path, t): self._on_frame(k, None))
        worker.finished.connect(self._on_grab_finished)
        self._grab = worker
        worker.start()

    def _on_frame(self, key: tuple[str, float], png) -> None:
        if self._want is not None and self._want != key:
            return                      # a newer cue was requested; its own result will follow
        if self.path != key[0]:
            return
        if png is None:
            self.still.setPixmap(QPixmap())
            return
        pix = QPixmap(str(png))
        self.still.setPixmap(pix.scaled(self.viewport().size(), Qt.KeepAspectRatio) if not pix.isNull() else pix)

    def _on_grab_finished(self) -> None:
        if self._grab is not None:
            self._grab.wait()
            self._grab = None
        if self._want is not None and self._want != self._started and not self._debounce.isActive():
            self._start_grab()

    def shutdown(self, timeout_ms: int = 5000) -> bool:
        self._debounce.stop()
        self._want = None
        # Release the media before the widget dies: the FFmpeg backend's native
        # threads can otherwise outlive the player and crash on teardown.
        self.player.stop()
        self.player.setSource(QUrl())
        if self._grab is not None:
            self._grab.wait(timeout_ms)
            if self._grab.isRunning():
                return False
        return True

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
        self.retry_failed_btn = QPushButton(RETRY_ALL_TEXT)
        self.retry_failed_btn.clicked.connect(self.retranslate_failed)
        head.addWidget(self.retry_failed_btn)
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
        self.chars.setToolTip("ดับเบิลคลิกเพื่อเปลี่ยนสีตัวละคร")
        self.chars.itemDoubleClicked.connect(self.edit_character_color)
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
        self.current = None
        self.editor.clear()
        self.cps_label.setText("")
        self.edit_label.setText("แก้บรรทัด")
        self.timeline.set_selected(None)
        self.project = project
        ep = project.episode
        self.title.setText(Path(ep.video_path).name)
        lang = {"ja": "ญี่ปุ่น", "en": "อังกฤษ"}.get(ep.src_lang, ep.src_lang)
        self.subtitle.setText(f"{lang} → ไทย · {len(ep.cues)} บรรทัด · {project.model} · ใช้ไป ${project.cost_usd:.2f}")
        names, colors = self._names(), self._colors()
        self._fill_chars()
        self.speaker_box.clear()
        for key, name in names.items():
            self.speaker_box.addItem(name, key)
        self.preview.set_source(ep.video_path)
        self.timeline.set_data(ep.cues, colors, names)
        self.filter_buttons["all"].setChecked(True)
        self.mode = "all"
        self._update_counts()
        self._fill_table()

    def _fill_chars(self) -> None:
        ep = self.project.episode
        names, colors = self._names(), self._colors()
        self.chars.clear()
        for key, name in names.items():
            count = sum(1 for c in ep.cues if c.speaker == key and not c.skip)
            ch = ep.character(key)
            info = f"{ch.gender} · {ch.role}" if ch else "ยังระบุคนพูดไม่ได้"
            self.chars.addItem(f"● {name} — {info} · {count} บรรทัด")
            item = self.chars.item(self.chars.count() - 1)
            item.setForeground(QColor(colors[key]))
            item.setData(Qt.UserRole, key)

    def edit_character_color(self, item) -> None:
        if not self.project:
            return
        ch = self.project.episode.character(item.data(Qt.UserRole))
        if ch is None:                       # the "unknown speaker" row has no colour of its own
            return
        picked = QColorDialog.getColor(QColor(ch.color), self, "เลือกสีตัวละคร")
        if not picked.isValid():
            return
        hex_color = picked.name().upper()
        if not _HEX_COLOR.fullmatch(hex_color):
            return
        ch.color = hex_color
        save_project(self.project)
        self._refresh_after_color_change()

    def _refresh_after_color_change(self) -> None:
        ep = self.project.episode
        self._fill_chars()
        self.timeline.set_data(ep.cues, self._colors(), self._names())
        self.timeline.set_selected(self.current)
        current = self.current
        self._fill_table()
        if current is not None:
            self.select(current)

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

    def shutdown(self, timeout_ms: int = 5000) -> bool:
        """Join background threads. True when nothing is left running."""
        ok = self.preview.shutdown(timeout_ms)
        if self._worker is not None:
            self._worker.wait(timeout_ms)
            ok = ok and not self._worker.isRunning()
        return ok

    def _set_translating(self, busy: bool) -> None:
        self.retranslate_btn.setEnabled(not busy)
        self.retry_failed_btn.setEnabled(not busy)

    def retranslate_failed(self) -> None:
        """Retry every cue that has no Thai text, in chunk-sized batches, in one background job."""
        if not self.project:
            return
        if self._worker is not None and self._worker.isRunning():
            return
        project = self.project
        ep = project.episode
        ids = set(untranslated_ids(ep.cues))
        if not ids:
            return
        chunks = make_chunks([c for c in ep.cues if c.id in ids], max(1, self.s.chunk_size))
        self._set_translating(True)
        self.retry_failed_btn.setText("กำลังแปล…")

        def job():
            backend = make_backend(self.s)
            cost, error = 0.0, None
            for chunk in chunks:
                try:
                    cost += translate_chunk(ep, chunk, backend, self.s.effort_translate)
                except Exception as exc:         # keep what earlier chunks produced, report this one
                    error = exc
                    break
            return cost, error

        def finish(result=None, error=None):
            if self._worker is not None:
                self._worker.wait()
            self._set_translating(False)
            self.retry_failed_btn.setText(RETRY_ALL_TEXT)
            if error is None:
                cost, error = result
                project.cost_usd += cost
            try:
                save_project(project)
            except (ValueError, OSError) as exc:
                log.warning("save failed after bulk retry: %s", exc)
                QMessageBox.warning(self, "บันทึกไม่สำเร็จ", str(exc))
            if self.project is project:
                self._update_counts()
                self._fill_table()
                if self.current is not None:
                    self.select(self.current)
            if error is not None:
                QMessageBox.warning(self, "แปลไม่สำเร็จ", str(error))

        self._worker = CallWorker(job)
        self._worker.done.connect(lambda r: finish(result=r))
        self._worker.failed.connect(lambda e: finish(error=e))
        self._worker.start()

    def retranslate(self) -> None:
        if self.current is None or not self.project:
            return
        if self._worker is not None and self._worker.isRunning():
            return
        project = self.project
        cue_id, ep = self.current, project.episode
        self._set_translating(True)
        self.retranslate_btn.setText("กำลังแปล…")

        def job():
            backend = make_backend(self.s)
            return translate_chunk(ep, [cue_id], backend, self.s.effort_translate)

        def finish(result=None, error=None):
            if self._worker is not None:
                self._worker.wait()
            self._set_translating(False)
            self.retranslate_btn.setText("แปลบรรทัดนี้ใหม่")
            if error is not None:
                QMessageBox.warning(self, "แปลไม่สำเร็จ", str(error))
                return
            project.cost_usd += result or 0.0
            save_project(project)
            if self.project is project:          # user may have opened another file meanwhile
                self._fill_table()
                self.select(cue_id)

        self._worker = CallWorker(job)
        self._worker.done.connect(lambda r: finish(result=r))
        self._worker.failed.connect(lambda e: finish(error=e))
        self._worker.start()

    def export(self) -> None:
        if not self.project:
            return
        missing = len(untranslated_ids(self.project.episode.cues))
        if missing and QMessageBox.question(
                self, "ยังมีบรรทัดที่ไม่ได้แปล",
                f"มี {missing} บรรทัดที่ยังไม่ได้แปล จะใส่ข้อความต้นฉบับแทน — ส่งออกต่อไหม?") != QMessageBox.Yes:
            return
        try:
            try:
                srt, ass = export_project(self.project, self.s)
            except FileExistsError as exc:
                if QMessageBox.question(self, "มีไฟล์อยู่แล้ว", f"เขียนทับ {exc}?") != QMessageBox.Yes:
                    return
                srt, ass = export_project(self.project, self.s, overwrite=True)
        except (ValueError, OSError) as exc:
            log.warning("export failed: %s", exc)
            QMessageBox.warning(self, "ส่งออกไม่สำเร็จ", str(exc))
            return
        QMessageBox.information(self, "ส่งออกแล้ว", f"{srt}\n{ass}")
