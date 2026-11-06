from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import QThread, Signal

log = logging.getLogger(__name__)


class CallWorker(QThread):
    done = Signal(object)
    failed = Signal(object)

    def __init__(self, fn: Callable[[], object], parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            self.done.emit(self._fn())
        except Exception as exc:  # reported to the GUI, never swallowed silently
            log.exception("background call failed")
            self.failed.emit(exc)
