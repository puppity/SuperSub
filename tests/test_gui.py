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
    assert blocker.args == [42]
    bad = CallWorker(lambda: 1 / 0)
    with qtbot.waitSignal(bad.failed, timeout=3000) as blocker:
        bad.start()
    assert isinstance(blocker.args[0], ZeroDivisionError)


from subtitle_translator.gui.settings_page import SettingsPage


def test_settings_page_switches_backend_and_persists(qtbot):
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
