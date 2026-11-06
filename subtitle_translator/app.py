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
