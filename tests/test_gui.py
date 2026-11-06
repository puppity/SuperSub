from subtitle_translator.core.settings import Settings
from subtitle_translator.gui.main_window import MainWindow
from subtitle_translator.gui.worker import CallWorker


def test_main_window_switches_pages(qtbot, tmp_path):
    w = MainWindow(Settings(), settings_path=tmp_path / "s.json")
    qtbot.addWidget(w)
    for i in range(3):
        w.go(i)
        assert w.stack.currentIndex() == i
    w.persist()
    assert (tmp_path / "s.json").exists()


def test_call_worker_reports_result_and_error(qtbot):
    ok = CallWorker(lambda: 42)
    with qtbot.waitSignal(ok.done, timeout=3000) as blocker:
        ok.start()
    ok.wait()
    assert blocker.args == [42]
    bad = CallWorker(lambda: 1 / 0)
    with qtbot.waitSignal(bad.failed, timeout=3000) as blocker:
        bad.start()
    bad.wait()
    assert isinstance(blocker.args[0], ZeroDivisionError)


import json

import pytest

from subtitle_translator.core.settings import save_settings
from subtitle_translator.gui import settings_page as sp
from subtitle_translator.gui.settings_page import SettingsPage


class FakeApi:
    def __init__(self, key, model):
        pass

    def check(self):
        return "ok"


@pytest.fixture
def fakes(monkeypatch):
    calls = {"set": []}
    monkeypatch.setattr(sp, "get_api_key", lambda: calls["set"][-1] if calls["set"] else None)
    monkeypatch.setattr(sp, "set_api_key", lambda k: calls["set"].append(k))
    monkeypatch.setattr(sp, "find_claude", lambda: None)
    monkeypatch.setattr(sp, "ApiBackend", FakeApi)
    return calls


def test_settings_page_switches_backend_and_persists(qtbot, fakes):
    saved = []
    s = Settings()
    page = SettingsPage(s, lambda: saved.append(True))
    qtbot.addWidget(page)
    page.show()
    assert page.api_box.isVisible() and not page.cc_box.isVisible()
    page.backend_cc.click()
    assert s.backend == "claude_code" and saved
    assert page.cc_box.isVisible() and not page.api_box.isVisible()
    page.model_buttons["claude-sonnet-5-5"].click()
    assert s.model == "claude-sonnet-5-5"
    page.chunk_spin.setValue(100)
    assert s.chunk_size == 100
    page.srt_colors.click()
    assert s.srt_colors is True


def test_save_key_never_persisted_in_settings(qtbot, fakes, tmp_path):
    s = Settings()
    path = tmp_path / "s.json"
    page = SettingsPage(s, lambda: save_settings(s, path))
    qtbot.addWidget(page)
    page.key_edit.setText("sk-ant-fake")
    page.save_test_btn.click()
    assert fakes["set"] == ["sk-ant-fake"]
    assert page.key_edit.text() == ""
    qtbot.waitUntil(lambda: page.status_label.text() == "ok", timeout=5000)
    page._worker.wait()
    assert page.save_test_btn.isEnabled()
    page._persist()
    assert "sk-" not in path.read_text(encoding="utf-8")


def test_keyring_failure_does_not_break_page(qtbot, monkeypatch, fakes):
    def boom(*a):
        raise RuntimeError("no keyring")
    monkeypatch.setattr(sp, "get_api_key", boom)
    monkeypatch.setattr(sp, "set_api_key", boom)
    page = SettingsPage(Settings(), lambda: None)
    qtbot.addWidget(page)
    page.key_edit.setText("sk-ant-fake")
    page.save_test_btn.click()
    assert "no keyring" in page.status_label.text()
    assert page.key_edit.text() == "sk-ant-fake"
    assert page._worker is None


def test_check_ignored_while_running(qtbot, fakes):
    import threading
    gate = threading.Event()
    page = SettingsPage(Settings(), lambda: None)
    qtbot.addWidget(page)
    page._run_check(lambda: gate.wait(5) and "done")
    first = page._worker
    assert not page.save_test_btn.isEnabled() and not page.cc_check_btn.isEnabled()
    page._run_check(lambda: "second")
    assert page._worker is first
    gate.set()
    qtbot.waitUntil(lambda: page.status_label.text() == "done", timeout=5000)
    first.wait()
    assert page.save_test_btn.isEnabled() and page.cc_check_btn.isEnabled()
