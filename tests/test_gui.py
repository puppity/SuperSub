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


from pathlib import Path

from subtitle_translator.core.media import SubTrack
from subtitle_translator.gui.queue_page import QueuePage, describe_source


def T(i, codec, lang):
    return SubTrack(sub_index=i, codec=codec, language=lang, title="")


def test_describe_source_rules(tmp_path):
    v = tmp_path / "EP01.mkv"
    side = tmp_path / "EP01.srt"
    assert describe_source(v, [T(0, "ass", "jpn")], [], ["ja", "en"])[0].sub_index == 0
    src, cands, meta = describe_source(v, [T(0, "hdmv_pgs_subtitle", "jpn")], [side], ["ja", "en"])
    assert src == side and "ไฟล์ซับข้างวิดีโอ" in meta
    src, cands, meta = describe_source(v, [], [], ["ja", "en"])
    assert src is None and "ต้องถอดเสียง" in meta
    src, cands, meta = describe_source(v, [T(0, "hdmv_pgs_subtitle", "jpn")], [], ["ja", "en"])
    assert src is None and "ซับภาพ" in meta
    src, cands, meta = describe_source(v, [T(0, "ass", "jpn"), T(1, "subrip", "jpn")], [], ["ja", "en"])
    assert src is None and len(cands) == 2


def test_queue_page_adds_sidecar_file_without_ffmpeg(qtbot, tmp_path, monkeypatch):
    v = tmp_path / "EP01.mkv"
    v.write_bytes(b"")
    (tmp_path / "EP01.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n", encoding="utf-8")
    monkeypatch.setattr("subtitle_translator.gui.queue_page.list_sub_tracks", lambda p: [])
    page = QueuePage(Settings(backend="claude_code"), lambda: None)   # no API estimate thread in tests
    qtbot.addWidget(page)
    page.add_paths([tmp_path])
    assert len(page.items) == 1
    assert page.items[0].source == tmp_path / "EP01.srt"
    page.add_paths([v])
    assert len(page.items) == 1


@pytest.fixture
def no_real_ai(monkeypatch):
    """Nothing in these tests may reach the real API, CLI or keyring."""
    monkeypatch.setattr("subtitle_translator.ai.factory.get_api_key", lambda: None)
    monkeypatch.setattr("subtitle_translator.ai.claude_code_backend.find_claude", lambda: None)

    def refuse(settings):
        raise AssertionError("real backend requested")
    monkeypatch.setattr("subtitle_translator.gui.queue_page.make_backend", refuse)
    monkeypatch.setattr("subtitle_translator.gui.review_page.make_backend", refuse, raising=False)


def _page_with_item(qtbot, tmp_path, monkeypatch):
    v = tmp_path / "EP01.mkv"
    v.write_bytes(b"")
    (tmp_path / "EP01.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n", encoding="utf-8")
    monkeypatch.setattr("subtitle_translator.gui.queue_page.list_sub_tracks", lambda p: [])
    page = QueuePage(Settings(backend="claude_code"), lambda: None)
    qtbot.addWidget(page)
    page.add_paths([v])
    return page


def test_queue_runner_failure_and_double_start_guard(qtbot, tmp_path, monkeypatch, no_real_ai):
    import threading
    from subtitle_translator.gui import queue_page as qp
    page = _page_with_item(qtbot, tmp_path, monkeypatch)
    gate = threading.Event()

    def slow_backend(settings):
        gate.wait(5)
        raise RuntimeError("boom")
    monkeypatch.setattr(qp, "make_backend", slow_backend)
    page.start()
    first = page.runner
    assert first is not None and not page.start_btn.isEnabled()
    page.start()                       # ignored while running
    assert page.runner is first
    gate.set()
    qtbot.waitUntil(lambda: page.runner is None, timeout=5000)
    first.wait()
    assert page.items[0].error and "boom" in page.items[0].error
    assert page.start_btn.isEnabled() and not page.stop_btn.isEnabled()


def test_estimate_runner_reports_none_when_backend_unavailable(qtbot, tmp_path, no_real_ai):
    from subtitle_translator.gui.queue_page import EstimateRunner, QueueItem
    item = QueueItem(video=tmp_path / "a.mkv", source=tmp_path / "a.srt")
    runner = EstimateRunner([(0, item)], Settings(backend="api"))
    with qtbot.waitSignal(runner.estimated, timeout=3000) as blocker:
        runner.start()
    runner.wait()
    assert blocker.args == [0, None]


from helpers import make_episode
from subtitle_translator.core.models import Character, Project
from subtitle_translator.gui.review_page import ReviewPage, cps, filter_ids


def review_project(tmp_path):
    ep = make_episode(4)
    ep.video_path = str(tmp_path / "EP01.mkv")
    ep.characters = [Character(key="yuki", name_src="ユキ", name_th="ยูกิ", color="#FBBF24")]
    for c in ep.cues:
        c.thai, c.status, c.speaker, c.speaker_conf = f"ไทย {c.id}", "translated", "yuki", 0.9
    ep.cues[1].speaker_conf = 0.3
    ep.cues[2].status = "failed"
    return Project(episode=ep, backend="api", model="claude-opus-5-5", stage="done")


def test_filters_and_cps(tmp_path):
    ep = review_project(tmp_path).episode
    assert filter_ids(ep.cues, "all", "") == [1, 2, 3, 4]
    assert filter_ids(ep.cues, "unsure", "") == [2]
    assert filter_ids(ep.cues, "failed", "") == [3]
    assert filter_ids(ep.cues, "all", "ไทย 4") == [4]
    assert cps(ep.cues[0], "x" * 15) == 10.0


def test_review_page_edit_saves_project(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("subtitle_translator.gui.review_page.grab_frame", lambda *a, **k: tmp_path / "x.png")
    p = review_project(tmp_path)
    page = ReviewPage(Settings(), lambda: None)
    qtbot.addWidget(page)
    page.load(p)
    assert page.table.rowCount() == 4
    page.select(2)
    assert page.editor.toPlainText() == "ไทย 2"
    page.editor.setPlainText("แก้แล้ว")
    page.save_edit()
    assert p.episode.cue(2).thai == "แก้แล้ว" and p.episode.cue(2).status == "edited"
    assert (tmp_path / "EP01.stproj.json").exists()
    page.set_filter("failed")
    assert page.table.rowCount() == 1


def test_review_retranslate_guard_and_failure(qtbot, tmp_path, monkeypatch, no_real_ai):
    import threading
    from subtitle_translator.gui import review_page as rp
    monkeypatch.setattr(rp, "grab_frame", lambda *a, **k: (_ for _ in ()).throw(rp.MediaError("no ffmpeg")))
    p = review_project(tmp_path)
    page = ReviewPage(Settings(), lambda: None)
    qtbot.addWidget(page)
    page.load(p)
    page.select(2)
    gate = threading.Event()

    def slow(settings):
        gate.wait(5)
        raise RuntimeError("down")
    monkeypatch.setattr(rp, "make_backend", slow)
    monkeypatch.setattr(rp.QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    page.retranslate()
    first = page._worker
    assert not page.retranslate_btn.isEnabled()
    page.retranslate()                 # ignored while running
    assert page._worker is first
    gate.set()
    qtbot.waitUntil(lambda: page.retranslate_btn.isEnabled(), timeout=5000)
    first.wait()
    assert p.episode.cue(2).thai == "ไทย 2"
