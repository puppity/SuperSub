from __future__ import annotations

import logging
import re
from pathlib import Path

from PySide6.QtCore import QSettings, QSizeF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QKeySequence, QPainterPath, QPen, QPixmap, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QColorDialog, QComboBox, QFrame, QGraphicsPixmapItem, QGraphicsScene,
    QGraphicsPathItem, QGraphicsView, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
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
        # Subtitle overlay drawn as a path: a thick dark outline under a coloured fill,
        # like a real subtitle renderer. The old 1px pen on a simple text item made Thai
        # glyphs look broken and its tight bounding box clipped upper vowels/tone marks.
        self.outline = QGraphicsPathItem()
        self.outline.setPen(QPen(QColor(0, 0, 0, 230), 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        self.outline.setBrush(Qt.NoBrush)
        self.text = QGraphicsPathItem()
        self.text.setPen(Qt.NoPen)
        self._caption = ""
        for item in (self.video, self.still, self.outline, self.text):
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
        # Qt's FFmpeg backend can open a file yet never decode a frame (AV1 without a
        # hardware decoder) and it raises no error. If no frame shows up soon after a
        # seek, switch to still frames grabbed by our bundled ffmpeg instead.
        self._got_frame = False
        self.video.videoSink().videoFrameChanged.connect(self._on_video_frame)
        self.player.mediaStatusChanged.connect(self._on_media_status)
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.setInterval(2500)
        self._watchdog.timeout.connect(self._check_decoding)
        self._last_cue: Cue | None = None

    fallback_changed = Signal(bool)

    def set_source(self, path: str) -> None:
        self.path = path
        self._set_fallback(False)
        self._want = None
        self._got_frame = False
        self._last_cue = None
        self.still.setPixmap(QPixmap())
        self._load_media(path)

    def _load_media(self, path: str) -> None:
        self.player.setSource(QUrl.fromLocalFile(path))

    def _set_fallback(self, on: bool) -> None:
        if self.fallback == on:
            return
        self.fallback = on
        self.video.setVisible(not on)
        self.fallback_changed.emit(on)

    def _go_still(self, reason: str) -> None:
        if self.fallback or not self.path:
            return
        log.warning("in-app video unavailable (%s), using still frames", reason)
        self.player.stop()
        self._set_fallback(True)
        if self._last_cue is not None:
            self._want = (self.path, (self._last_cue.start + self._last_cue.end) / 2)
            self._debounce.start()

    def _on_error(self, *_):
        self._go_still(self.player.errorString() or "player error")

    def _on_media_status(self, status) -> None:
        if status == QMediaPlayer.InvalidMedia:
            self._go_still("invalid media")

    def _on_video_frame(self, frame) -> None:
        if frame.isValid():
            self._got_frame = True
            self._watchdog.stop()

    def _check_decoding(self) -> None:
        if not self._got_frame:
            self._go_still("no decoded frame")

    def show_cue(self, cue: Cue, text: str, color: str) -> None:
        self._caption = text
        self.text.setBrush(QBrush(QColor(color)))
        self._last_cue = cue
        if self.fallback and self.path:
            self._want = (self.path, (cue.start + cue.end) / 2)
            self._debounce.start()
        else:
            self.player.pause()
            self.player.setPosition(int(cue.start * 1000))
            if not self._got_frame and self.path:
                self._watchdog.start()
        self._layout()

    def _start_grab(self) -> None:
        if self._grab is not None:
            return                      # the finish handler re-runs with the latest request
        if self._want is None:
            return
        path, t = self._want
        self._started = self._want
        out = cache_dir() / "preview.png"
        worker = CallWorker(lambda: grab_frame(path, t, out, width=960))
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
        if not pix.isNull():
            pix = pix.scaled(self.viewport().size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.still.setPixmap(pix)
        self._layout()

    def _on_grab_finished(self) -> None:
        if self._grab is not None:
            self._grab.wait()
            self._grab = None
        if self._want is not None and self._want != self._started and not self._debounce.isActive():
            self._start_grab()

    def shutdown(self, timeout_ms: int = 5000) -> bool:
        self._debounce.stop()
        self._watchdog.stop()
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
        if self.fallback:
            return
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
        if not self.still.pixmap().isNull():
            pix = self.still.pixmap()
            self.still.setPos((size.width() - pix.width()) / 2, (size.height() - pix.height()) / 2)
        self._layout_caption(size.width(), size.height())

    def _layout_caption(self, w: float, h: float) -> None:
        pix = self.still.pixmap()
        frame_h = pix.height() if self.fallback and not pix.isNull() else min(h, w * 9 / 16)
        frame_w = pix.width() if self.fallback and not pix.isNull() else w
        bottom = (h + frame_h) / 2 if self.fallback and not pix.isNull() else h
        font = QFont("Leelawadee UI")
        font.setWeight(QFont.DemiBold)
        lines = self._caption.splitlines() or [""]
        size = max(14, int(frame_h * 0.065))
        while size > 12:                          # shrink until the widest line fits the frame
            font.setPixelSize(size)
            fm = QFontMetricsF(font)
            if max(fm.horizontalAdvance(line) for line in lines) <= frame_w * 0.92:
                break
            size -= 1
        line_h = font.pixelSize() * 1.45          # room for Thai upper vowels and tone marks
        path = QPainterPath()
        for i, line in enumerate(lines):
            x = (w - fm.horizontalAdvance(line)) / 2
            y = bottom - 14 - (len(lines) - 1 - i) * line_h - fm.descent()
            path.addText(x, y, font, line)
        self.outline.setPath(path)
        self.text.setPath(path)


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
        # Rebuilding the table on every keystroke made typing stutter; wait for a pause.
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(220)
        self._search_timer.timeout.connect(self._fill_table)
        self.search.textChanged.connect(lambda _: self._search_timer.start())
        head.addWidget(self.search)
        self.retry_failed_btn = QPushButton(RETRY_ALL_TEXT)
        self.retry_failed_btn.clicked.connect(self.retranslate_failed)
        head.addWidget(self.retry_failed_btn)
        export = QPushButton("ส่งออก .srt + .ass")
        export.setProperty("accent", True)
        export.clicked.connect(self.export)
        head.addWidget(export)
        root.addLayout(head)

        # Drag the splitter handles to resize: preview vs. table, preview vs. cast, body vs. timeline.
        self.body_split = QSplitter(Qt.Horizontal)
        self.left_split = QSplitter(Qt.Vertical)
        self.outer_split = QSplitter(Qt.Vertical)
        for s in (self.body_split, self.left_split, self.outer_split):
            s.setChildrenCollapsible(False)
            s.setHandleWidth(8)

        preview_box = QWidget()
        left = QVBoxLayout(preview_box)
        left.setContentsMargins(0, 0, 0, 0)
        self.preview = VideoPreview()
        self.preview.setMinimumSize(240, 135)
        left.addWidget(self.preview, 1)
        self.play_btn = play = QPushButton("เล่น / หยุด  (F5)")
        play.clicked.connect(self.preview.toggle)
        self.preview.fallback_changed.connect(self._on_preview_fallback)
        left.addWidget(play)
        self.left_split.addWidget(preview_box)
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
        self.left_split.addWidget(chars_card)
        self.left_split.setStretchFactor(0, 3)
        self.left_split.setStretchFactor(1, 2)
        self.body_split.addWidget(self.left_split)

        right_box = QWidget()
        right = QVBoxLayout(right_box)
        right.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(True)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setFocusPolicy(Qt.NoFocus)
        # Fixed two-line rows: resizeRowsToContents() re-laid out every wrapped cell on each
        # refresh and was the main source of lag on long episodes.
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.verticalHeader().setDefaultSectionSize(52)
        self._row_of: dict[int, int] = {}
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
        self.body_split.addWidget(right_box)
        self.body_split.setStretchFactor(0, 2)
        self.body_split.setStretchFactor(1, 3)
        self.outer_split.addWidget(self.body_split)

        self.timeline = TimelineWidget()
        self.timeline.cue_clicked.connect(self.select)
        self.outer_split.addWidget(self.timeline)
        self.outer_split.setStretchFactor(0, 1)
        self.outer_split.setStretchFactor(1, 0)
        root.addWidget(self.outer_split, 1)
        self._restore_splits()

        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.save_edit)
        QShortcut(QKeySequence("F5"), self, activated=self.preview.toggle)

    def _on_preview_fallback(self, still: bool) -> None:
        self.play_btn.setEnabled(not still)
        self.play_btn.setText("วิดีโอนี้เล่นในโปรแกรมไม่ได้ · แสดงภาพนิ่งของแต่ละบรรทัดแทน"
                              if still else "เล่น / หยุด  (F5)")

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
        by_id = {c.id: c for c in ep.cues}          # ep.cue() is a linear scan: O(n²) per refresh
        names, colors = self._names(), self._colors()
        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)
        self.table.clearContents()
        self.table.setRowCount(len(ids))
        self._row_of = {}
        for row, cue_id in enumerate(ids):
            self._set_row(row, by_id[cue_id], names, colors)
        self.table.blockSignals(False)
        self.table.setUpdatesEnabled(True)

    def _set_row(self, row: int, c: Cue, names: dict, colors: dict) -> None:
        self._row_of[c.id] = row
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
            if col >= 3:
                item.setToolTip(value)
            self.table.setItem(row, col, item)

    def _refresh_row(self, cue_id: int) -> None:
        """Update one row in place; fall back to a rebuild if it left the current filter."""
        row = self._row_of.get(cue_id)
        c = self.project.episode.cue(cue_id)
        if row is None or cue_id not in filter_ids([c], self.mode, self.search.text()):
            self._fill_table()
            return
        self.table.blockSignals(True)
        self._set_row(row, c, self._names(), self._colors())
        self.table.blockSignals(False)

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
            row = self._row_of.get(cue_id)
            if row is not None:
                self.table.blockSignals(True)
                self.table.selectRow(row)
                self.table.blockSignals(False)
                self.table.scrollToItem(self.table.item(row, 0), QAbstractItemView.PositionAtCenter)

    def _update_cps(self) -> None:
        if self.current is None or not self.project:
            return
        value = cps(self.project.episode.cue(self.current), self.editor.toPlainText())
        warn = value > CPS_LIMIT
        self.cps_label.setText(f"ความเร็วอ่าน {value:.0f} ตัวอักษร/วินาที" + (" · ยาวไป" if warn else ""))
        if self.cps_label.property("warn") != warn:   # restyle only when the state flips, not per keystroke
            self.cps_label.setProperty("warn", warn)
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
        self._refresh_row(current)
        self.select(current)

    SPLITS = ("body_split", "left_split", "outer_split")

    @staticmethod
    def _split_store() -> QSettings:
        return QSettings("SubTranslator", "review")

    def _restore_splits(self) -> None:
        """Bring back the pane sizes the user dragged to last time."""
        store = self._split_store()
        for name in self.SPLITS:
            state = store.value(name)
            if state is not None:
                getattr(self, name).restoreState(state)
            getattr(self, name).splitterMoved.connect(self._save_splits)

    def _save_splits(self, *_):
        store = self._split_store()
        for name in self.SPLITS:
            store.setValue(name, getattr(self, name).saveState())

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
