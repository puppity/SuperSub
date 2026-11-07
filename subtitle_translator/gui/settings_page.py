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
from ..core.transcribe import MODELS as WHISPER_MODELS
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
        body.setMaximumWidth(960)          # full-width fields on a wide window read as a spreadsheet
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
        keyring_error = None
        try:
            if get_api_key():
                self.key_edit.setPlaceholderText("บันทึก key ไว้แล้ว — วางใหม่เพื่อเปลี่ยน")
        except Exception as exc:  # broken keyring backend must not stop the window opening
            keyring_error = exc
        show = QPushButton("แสดง", checkable=True)
        show.toggled.connect(lambda on: self.key_edit.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        self.save_test_btn = save_test = QPushButton("บันทึกและทดสอบ")
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
        self.cc_check_btn = cc_check = QPushButton("ตรวจการเชื่อมต่อ")
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

        # --- speech to text ---
        col.addWidget(label("ถอดเสียง (วิดีโอที่ไม่มีซับ)", "h2"))
        asr, asr_lay = card()
        self.auto_asr = QCheckBox("ถอดเสียงอัตโนมัติเมื่อวิดีโอไม่มีซับที่อ่านได้", checked=self.s.auto_transcribe)
        self.auto_asr.toggled.connect(lambda on: self._set("auto_transcribe", on))
        asr_lay.addWidget(self.auto_asr)
        aform = QFormLayout()
        self.whisper_box = QComboBox()
        for key, desc in WHISPER_MODELS.items():
            self.whisper_box.addItem(f"{key} — {desc}", key)
        self.whisper_box.setCurrentIndex(max(0, self.whisper_box.findData(self.s.whisper_model)))
        self.whisper_box.currentIndexChanged.connect(
            lambda i: self._set("whisper_model", self.whisper_box.itemData(i)))
        aform.addRow("โมเดลถอดเสียง", self.whisper_box)
        asr_lay.addLayout(aform)
        asr_lay.addWidget(label("ทำในเครื่อง ไม่เสียเงิน · ใช้การ์ดจอ NVIDIA ถ้ามี · "
                                "ครั้งแรกจะดาวน์โหลดโมเดล (~1.6–3GB)", "hint"))
        col.addWidget(asr)

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
        self.mux_mkv = QCheckBox("แนบซับไทยเข้าไฟล์วิดีโอด้วย (สร้างไฟล์ใหม่ .th.mkv · ไม่แตะไฟล์เดิม)",
                                 checked=self.s.mux_mkv)
        self.mux_mkv.toggled.connect(lambda on: self._set("mux_mkv", on))
        out_lay.addWidget(self.mux_mkv)
        col.addWidget(out)
        col.addStretch(1)

        holder = QWidget()
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(body, 1)
        hl.addStretch(0)
        scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        scroll.setWidget(holder)
        QVBoxLayout(self).addWidget(scroll)
        self.layout().setContentsMargins(0, 0, 0, 0)

        (self.backend_cc if self.s.backend == "claude_code" else self.backend_api).setChecked(True)
        self.backend_api.toggled.connect(lambda on: on and self._set("backend", "api"))
        self.backend_cc.toggled.connect(lambda on: on and self._set("backend", "claude_code"))
        self._sync_backend()
        if keyring_error is not None:
            self._show_status(f"เข้าถึงที่เก็บรหัสของ Windows ไม่ได้: {keyring_error}", COLORS["bad"])

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
        if self._worker is not None and self._worker.isRunning():
            return
        self.status_label.setText("กำลังตรวจ…")
        self.status_label.setStyleSheet("")
        self._set_checking(True)
        self._worker = CallWorker(fn)
        self._worker.done.connect(lambda msg: self._finish_check(str(msg), COLORS["good"]))
        self._worker.failed.connect(lambda exc: self._finish_check(str(exc), COLORS["bad"]))
        self._worker.start()

    def shutdown(self, timeout_ms: int = 5000) -> bool:
        """Join the connection-check thread. True when nothing is left running."""
        if self._worker is None:
            return True
        self._worker.wait(timeout_ms)
        return not self._worker.isRunning()

    def _set_checking(self, busy: bool) -> None:
        self.save_test_btn.setEnabled(not busy)
        self.cc_check_btn.setEnabled(not busy)

    def _finish_check(self, text: str, color: str) -> None:
        self._set_checking(False)
        self._show_status(text, color)

    def _show_status(self, text: str, color: str) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color};")

    def _save_and_test_key(self) -> None:
        key = self.key_edit.text().strip()
        try:
            if key:
                set_api_key(key)
                self.key_edit.clear()
                self.key_edit.setPlaceholderText("บันทึก key ไว้แล้ว — วางใหม่เพื่อเปลี่ยน")
            stored = get_api_key()
        except Exception as exc:  # keep the typed key so the user can retry
            self._show_status(f"เข้าถึงที่เก็บรหัสของ Windows ไม่ได้: {exc}", COLORS["bad"])
            return
        if not stored:
            self._show_status("ยังไม่ได้ใส่ API key", COLORS["bad"])
            return
        model = self.s.model
        self._run_check(lambda: ApiBackend(stored, model).check())

    def _test_cc(self) -> None:
        model = self.s.model
        self._run_check(lambda: ClaudeCodeBackend(model).check())
