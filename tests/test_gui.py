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
from PySide6.QtCore import QThread

from subtitle_translator.core.settings import save_settings
from subtitle_translator.gui import settings_page as sp
from subtitle_translator.gui.settings_page import SettingsPage


@pytest.fixture(autouse=True)
def join_gui_threads(qtbot, monkeypatch):
    """Every page built by a test is shut down (and its threads joined) at teardown."""
    from subtitle_translator.gui import queue_page as qp, review_page as rp
    made = []
    for cls in (qp.QueuePage, rp.ReviewPage, rp.VideoPreview):
        orig = cls.__init__

        def init(self, *a, _orig=orig, **k):
            _orig(self, *a, **k)
            made.append(self)
        monkeypatch.setattr(cls, "__init__", init)
    # Never hand a real file to QtMultimedia in tests: its native FFmpeg threads
    # race widget teardown and crash the test process intermittently.
    monkeypatch.setattr(rp.VideoPreview, "_load_media", lambda self, path: None)
    yield
    assert all([p.shutdown(5000) for p in made]), "a GUI thread was still running at teardown"


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
    qtbot.waitUntil(lambda: not page._busy(), timeout=5000)
    assert len(page.items) == 1
    assert page.items[0].source == tmp_path / "EP01.srt"
    page.add_paths([v])
    qtbot.waitUntil(lambda: not page._busy(), timeout=5000)
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
    qtbot.waitUntil(lambda: not page._busy(), timeout=5000)
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


def test_review_load_resets_selection_so_edit_cannot_leak(qtbot, tmp_path, monkeypatch, no_real_ai):
    from subtitle_translator.gui import review_page as rp
    monkeypatch.setattr(rp, "grab_frame", lambda *a, **k: tmp_path / "x.png")
    a = review_project(tmp_path)
    b = review_project(tmp_path / "other")
    (tmp_path / "other").mkdir()
    page = ReviewPage(Settings(), lambda: None)
    qtbot.addWidget(page)
    page.load(a)
    page.select(2)
    page.editor.setPlainText("พิมพ์ค้างไว้")
    page.load(b)
    assert page.current is None and page.editor.toPlainText() == ""
    assert page.timeline.selected is None
    page.save_edit()
    assert b.episode.cue(2).thai == "ไทย 2" and b.episode.cue(2).status == "translated"
    assert not (tmp_path / "other" / "EP01.stproj.json").exists()


def test_add_paths_probes_off_gui_thread_and_queues(qtbot, tmp_path, monkeypatch, no_real_ai):
    import threading
    from subtitle_translator.gui import queue_page as qp
    for n in ("EP01", "EP02"):
        (tmp_path / f"{n}.mkv").write_bytes(b"")
        (tmp_path / f"{n}.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n", encoding="utf-8")
    gate = threading.Event()
    seen = []

    def probe(p):
        seen.append(threading.current_thread() is threading.main_thread())
        gate.wait(5)
        if p.name == "EP02.mkv":
            raise RuntimeError("probe boom")
        return []
    monkeypatch.setattr(qp, "list_sub_tracks", probe)
    page = QueuePage(Settings(backend="claude_code"), lambda: None)
    qtbot.addWidget(page)
    page.add_paths([tmp_path / "EP01.mkv"])
    first = page.prober
    assert first is not None and page._busy()
    page.add_paths([tmp_path / "EP02.mkv"])          # queued, running thread untouched
    assert page.prober is first
    page.start()                                     # blocked while probing
    assert page.runner is None
    gate.set()
    qtbot.waitUntil(lambda: not page._busy(), timeout=5000)
    first.wait()
    assert seen and not any(seen)
    assert [i.video.name for i in page.items] == ["EP01.mkv", "EP02.mkv"]
    assert page.items[0].source is not None
    assert page.items[1].error and "probe boom" in page.items[1].error


def test_queue_shutdown_stops_runner(qtbot, tmp_path, monkeypatch, no_real_ai):
    from subtitle_translator.gui import queue_page as qp
    page = _page_with_item(qtbot, tmp_path, monkeypatch)
    monkeypatch.setattr(qp, "make_backend", lambda s: object())
    monkeypatch.setattr(qp, "prepare_project", lambda *a, **k: object())

    def spin(project, backend, settings, progress, should_stop):
        while not should_stop():
            QThread.msleep(10)
        raise qp.Stopped()
    monkeypatch.setattr(qp, "run_project", spin)
    page.start()
    runner = page.runner
    assert runner.isRunning()
    assert page.shutdown(5000) is True
    assert not runner.isRunning()
    runner.wait()


def test_estimate_deferred_while_run_active_and_stale_model_ignored(qtbot, tmp_path, monkeypatch, no_real_ai):
    from subtitle_translator.gui import queue_page as qp
    page = _page_with_item(qtbot, tmp_path, monkeypatch)
    page.s.backend = "api"
    started = []
    monkeypatch.setattr(qp.EstimateRunner, "start", lambda self: started.append(self))
    page.runner = type("R", (), {"isRunning": lambda self: True})()
    page._estimate_rows([0])
    assert started == [] and page._est_queue == [0]
    page.runner = None
    page._kick_estimator()
    assert len(started) == 1
    page.estimator = None
    page._on_estimated(0, 1.5, "some-other-model")
    assert page.items[0].estimate is None
    page._on_estimated(0, 1.5, page.s.model)
    assert page.items[0].estimate == 1.5


def test_preview_frame_grab_is_async_and_latest_wins(qtbot, tmp_path, monkeypatch, no_real_ai):
    import threading
    from subtitle_translator.gui import review_page as rp
    gate = threading.Event()
    calls = []

    def grab(video, t, out, width=640):
        calls.append((threading.current_thread() is threading.main_thread(), t))
        gate.wait(5)
        if t > 100:
            raise rp.MediaError("bad")
        return tmp_path / "x.png"
    monkeypatch.setattr(rp, "grab_frame", grab)
    ep = review_project(tmp_path).episode
    pv = rp.VideoPreview()
    qtbot.addWidget(pv)
    pv.path, pv.fallback = str(tmp_path / "EP01.mkv"), True
    pv.show_cue(ep.cues[0], "a", "#fff")
    qtbot.waitUntil(lambda: len(calls) == 1, timeout=3000)
    pv.show_cue(ep.cues[1], "b", "#fff")             # arrives while one is running
    pv.show_cue(ep.cues[2], "c", "#fff")             # replaces the pending one
    gate.set()
    qtbot.waitUntil(lambda: len(calls) == 2 and pv._grab is None, timeout=5000)
    assert not any(c[0] for c in calls)
    assert calls[1][1] == (ep.cues[2].start + ep.cues[2].end) / 2
    pv.shutdown(3000)


def test_main_window_close_waits_for_threads(qtbot, tmp_path, monkeypatch, no_real_ai):
    from PySide6.QtGui import QCloseEvent
    w = MainWindow(Settings(backend="claude_code"), settings_path=tmp_path / "s.json")
    qtbot.addWidget(w)
    ev = QCloseEvent()
    w.closeEvent(ev)
    assert ev.isAccepted()
    w.queue_page.shutdown = lambda timeout_ms=5000: False
    ev = QCloseEvent()
    w.closeEvent(ev)
    assert not ev.isAccepted()
    del w.queue_page.shutdown          # back to the real method for the teardown join


def test_queue_runner_snapshots_settings(tmp_path):
    from subtitle_translator.gui.queue_page import QueueRunner
    s = Settings(model="claude-opus-5-5")
    r = QueueRunner([], s)
    s.model = "claude-haiku-5-5"
    assert r.settings.model == "claude-opus-5-5" and r.settings is not s


def test_settings_shutdown_joins_worker_and_close_waits(qtbot, fakes, tmp_path):
    import threading
    from PySide6.QtGui import QCloseEvent
    gate = threading.Event()
    page = SettingsPage(Settings(), lambda: None)
    qtbot.addWidget(page)
    assert page.shutdown(100) is True                  # nothing running
    page._run_check(lambda: gate.wait(5) and "done")
    assert page.shutdown(50) is False                  # still blocked: reported, not hung
    gate.set()
    assert page.shutdown(5000) is True
    assert not page._worker.isRunning()

    w = MainWindow(Settings(backend="claude_code"), settings_path=tmp_path / "s.json")
    qtbot.addWidget(w)
    gate2 = threading.Event()
    w.settings_page._run_check(lambda: gate2.wait(5) and "x")
    w.settings_page.shutdown = lambda timeout_ms=5000: False
    ev = QCloseEvent()
    w.closeEvent(ev)
    assert not ev.isAccepted()
    del w.settings_page.shutdown
    gate2.set()
    ev = QCloseEvent()
    w.closeEvent(ev)
    assert ev.isAccepted()


def _fake_translator(batches):
    """FakeBackend whose responder returns Thai for exactly the ids requested."""
    from fakes import FakeBackend
    from subtitle_translator.translate.prompts import TRANSLATE_MARKER

    def respond(req):
        tail = req.task.split(TRANSLATE_MARKER, 1)[1]
        ids = [int(line.split("\t")[0]) for line in tail.strip().splitlines()]
        batches.append(ids)
        return {"lines": [{"id": i, "thai": f"ใหม่ {i}"} for i in ids]}
    return FakeBackend([respond] * 20)


def _failed_project(tmp_path, n=5):
    p = review_project(tmp_path)
    ep = make_episode(n)
    ep.video_path = str(tmp_path / "EP01.mkv")
    ep.characters = p.episode.characters
    for c in ep.cues:
        c.thai, c.status, c.speaker, c.speaker_conf = f"ไทย {c.id}", "translated", "yuki", 0.9
    for i in (2, 3, 5):
        ep.cues[i - 1].thai, ep.cues[i - 1].status = None, "failed"
    ep.cues[3].skip, ep.cues[3].thai = True, None          # skipped lines are never "missing"
    return Project(episode=ep, backend="api", model="claude-opus-5-5", stage="done")


def test_review_export_warns_about_untranslated_cues(qtbot, tmp_path, monkeypatch, no_real_ai):
    from subtitle_translator.gui import review_page as rp
    monkeypatch.setattr(rp, "grab_frame", lambda *a, **k: tmp_path / "x.png")
    asked, exported = [], []
    answer = {"v": rp.QMessageBox.No}
    monkeypatch.setattr(rp.QMessageBox, "question",
                        staticmethod(lambda parent, title, text, *a, **k: asked.append(text) or answer["v"]))
    monkeypatch.setattr(rp.QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(rp, "export_project", lambda proj, s, overwrite=False: exported.append(1) or ("a", "b"))
    page = ReviewPage(Settings(), lambda: None)
    qtbot.addWidget(page)
    page.load(_failed_project(tmp_path))
    page.export()
    assert asked == ["มี 3 บรรทัดที่ยังไม่ได้แปล จะใส่ข้อความต้นฉบับแทน — ส่งออกต่อไหม?"]
    assert exported == []
    answer["v"] = rp.QMessageBox.Yes
    page.export()
    assert exported == [1]
    page.load(review_project(tmp_path))                      # nothing missing: no question
    asked.clear()
    page.export()
    assert asked == [] and len(exported) == 2


def test_queue_export_all_asks_once_with_total(qtbot, tmp_path, monkeypatch, no_real_ai):
    from subtitle_translator.gui import queue_page as qp
    page = _page_with_item(qtbot, tmp_path, monkeypatch)
    a, b = _failed_project(tmp_path), _failed_project(tmp_path)
    page.items.append(page.items[0].__class__(video=tmp_path / "EP02.mkv", source=tmp_path / "EP02.srt"))
    page.items[0].project, page.items[1].project = a, b
    asked, exported = [], []
    monkeypatch.setattr(qp.QMessageBox, "question",
                        staticmethod(lambda parent, title, text, *a, **k: asked.append(text) or qp.QMessageBox.No))
    monkeypatch.setattr(qp, "export_project", lambda proj, s, overwrite=False: exported.append(proj))
    page.export_all()
    assert asked == ["มี 6 บรรทัดที่ยังไม่ได้แปล จะใส่ข้อความต้นฉบับแทน — ส่งออกต่อไหม?"]
    assert exported == []
    monkeypatch.setattr(qp.QMessageBox, "question", staticmethod(lambda *a, **k: qp.QMessageBox.Yes))
    page.cards = [type("C", (), {"update_from": lambda self, it: None})()] * 2
    page.export_all()
    assert exported == [a, b]


def test_review_retranslate_all_failed_batches_and_saves(qtbot, tmp_path, monkeypatch, no_real_ai):
    from subtitle_translator.gui import review_page as rp
    monkeypatch.setattr(rp, "grab_frame", lambda *a, **k: tmp_path / "x.png")
    batches = []
    backend = _fake_translator(batches)
    monkeypatch.setattr(rp, "make_backend", lambda s: backend)
    s = Settings(chunk_size=2)
    page = ReviewPage(s, lambda: None)
    qtbot.addWidget(page)
    p = _failed_project(tmp_path)
    page.load(p)
    page.set_filter("failed")
    assert page.table.rowCount() == 3
    page.retranslate_failed()
    first = page._worker
    assert not page.retry_failed_btn.isEnabled() and not page.retranslate_btn.isEnabled()
    page.retranslate_failed()                      # ignored while running
    assert page._worker is first
    qtbot.waitUntil(lambda: page.retry_failed_btn.isEnabled(), timeout=5000)
    first.wait()
    assert batches == [[2, 3], [5]]                # chunk-sized groups, skip cue excluded
    assert [p.episode.cue(i).thai for i in (2, 3, 5)] == ["ใหม่ 2", "ใหม่ 3", "ใหม่ 5"]
    assert p.episode.cue(4).thai is None
    assert p.cost_usd == pytest.approx(0.02)
    assert (tmp_path / "EP01.stproj.json").exists()
    assert page.retry_failed_btn.text() == "แปลบรรทัดที่ล้มเหลวใหม่ทั้งหมด"
    assert page.table.rowCount() == 0              # failed filter refreshed


def test_review_retranslate_all_reports_error_and_keeps_progress(qtbot, tmp_path, monkeypatch, no_real_ai):
    from fakes import FakeBackend
    from subtitle_translator.ai.backend import AIError, AIErrorKind
    from subtitle_translator.gui import review_page as rp
    monkeypatch.setattr(rp, "grab_frame", lambda *a, **k: tmp_path / "x.png")
    batches = []
    ok = _fake_translator(batches).responses[0]
    backend = FakeBackend([ok, AIError(AIErrorKind.NETWORK, "เชื่อมต่อไม่ได้")])
    monkeypatch.setattr(rp, "make_backend", lambda s: backend)
    warned = []
    monkeypatch.setattr(rp.QMessageBox, "warning", staticmethod(lambda p, t, text, *a, **k: warned.append(text)))
    page = ReviewPage(Settings(chunk_size=2), lambda: None)
    qtbot.addWidget(page)
    p = _failed_project(tmp_path)
    page.load(p)
    page.retranslate_failed()
    qtbot.waitUntil(lambda: page.retry_failed_btn.isEnabled(), timeout=5000)
    page._worker.wait()
    assert warned == ["เชื่อมต่อไม่ได้"]
    assert p.episode.cue(2).thai == "ใหม่ 2" and p.episode.cue(5).thai is None
    assert (tmp_path / "EP01.stproj.json").exists()


def test_character_color_double_click(qtbot, tmp_path, monkeypatch, no_real_ai):
    from PySide6.QtGui import QColor
    from subtitle_translator.gui import review_page as rp
    monkeypatch.setattr(rp, "grab_frame", lambda *a, **k: tmp_path / "x.png")
    p = review_project(tmp_path)
    page = ReviewPage(Settings(), lambda: None)
    qtbot.addWidget(page)
    page.load(p)
    page.select(1)
    picked = {"c": QColor("#12abef")}
    monkeypatch.setattr(rp.QColorDialog, "getColor", staticmethod(lambda *a, **k: picked["c"]))
    page.chars.itemDoubleClicked.emit(page.chars.item(0))
    assert p.episode.character("yuki").color == "#12ABEF"
    assert (tmp_path / "EP01.stproj.json").exists()
    assert page.chars.item(0).foreground().color().name().upper() == "#12ABEF"
    assert page.timeline.colors["yuki"] == "#12ABEF"
    assert page.current == 1 and page.timeline.selected == 1
    picked["c"] = QColor()                                   # cancelled dialog: invalid colour
    page.chars.itemDoubleClicked.emit(page.chars.item(0))
    assert p.episode.character("yuki").color == "#12ABEF"
    page.chars.itemDoubleClicked.emit(page.chars.item(page.chars.count() - 1))   # "unknown" row: ignored
    assert p.episode.character("yuki").color == "#12ABEF"


def test_review_edit_updates_row_in_place_and_search_is_debounced(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("subtitle_translator.gui.review_page.grab_frame", lambda *a, **k: tmp_path / "x.png")
    p = review_project(tmp_path)
    page = ReviewPage(Settings(), lambda: None)
    qtbot.addWidget(page)
    page.load(p)
    rebuilds = []
    original = page._fill_table
    monkeypatch.setattr(page, "_fill_table", lambda: (rebuilds.append(1), original())[1])
    page.select(2)
    page.editor.setPlainText("แก้ในแถว")
    page.save_edit()
    assert rebuilds == []                                  # edit refreshed one row, no full rebuild
    assert page.table.item(page._row_of[2], 4).text() == "แก้ในแถว"
    page.search.setText("ไทย 4")
    assert page.table.rowCount() == 4                      # not yet: waits for the typing pause
    qtbot.waitUntil(lambda: page.table.rowCount() == 1, timeout=2000)


def test_auth_pause_pops_login_prompt_for_claude_max(qtbot, monkeypatch):
    from subtitle_translator.gui.queue_page import QueuePage
    page = QueuePage(Settings(backend="claude_code"), lambda: None)
    qtbot.addWidget(page)
    opened = []
    monkeypatch.setattr(page, "open_login", lambda: opened.append(1))
    page._on_paused("Claude Code ยังไม่ได้ล็อกอิน", "auth")
    box = page._login_box
    assert box.isVisible() and not page.login_btn.isHidden()
    login = next(b for b in box.buttons() if b.text() == "ล็อกอินตอนนี้")
    login.click()
    assert opened == [1]
    page._on_paused("หยุดแล้ว — กดเริ่มเพื่อทำต่อ", "")     # a plain user stop must not nag
    assert page.login_btn.isHidden()


def test_api_backend_auth_pause_does_not_offer_claude_login(qtbot):
    from subtitle_translator.gui.queue_page import QueuePage
    page = QueuePage(Settings(backend="api"), lambda: None)
    qtbot.addWidget(page)
    page._on_paused("API key ไม่ถูกต้อง", "auth")
    assert page.login_btn.isHidden()
    assert getattr(page, "_login_box", None) is None


def test_progress_status_shows_elapsed_time_for_long_steps(qtbot, monkeypatch):
    from pathlib import Path
    from subtitle_translator.gui import queue_page as qp
    page = qp.QueuePage(Settings(backend="claude_code"), lambda: None)
    qtbot.addWidget(page)
    page._append(qp.QueueItem(video=Path("a.mkv"), source=Path("a.ass")))
    now = [1000.0]
    monkeypatch.setattr(qp.time, "monotonic", lambda: now[0])
    page._on_progress(0, "วิเคราะห์ตัวละคร", 0.0)
    assert page.items[0].status == "วิเคราะห์ตัวละคร 0%"
    assert page._tick.isActive()
    now[0] += 192
    page._refresh_elapsed()
    assert page.items[0].status == "วิเคราะห์ตัวละคร 0% · 3:12"
    assert page.cards[0].status.property("tone") == "busy"
    page._on_failed(0, "x")
    assert not page._tick.isActive()
