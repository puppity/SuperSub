import json
import os

import pytest

from helpers import make_episode
from subtitle_translator.core.models import Character, Project
from subtitle_translator.core.project_store import ProjectFileError, load_project, project_path, save_project


def test_project_roundtrip_through_dict():
    ep = make_episode(3)
    ep.characters.append(Character(key="yuki", name_src="ユキ", name_th="ยูกิ",
                                   address_terms={"お姉ちゃん": "พี่"}, color="#FBBF24"))
    ep.cues[0].speaker = "yuki"
    ep.cues[0].thai = "สวัสดี"
    p = Project(episode=ep, backend="api", model="claude-opus-5-5", done_chunks=[0], cost_usd=0.12)
    again = Project.from_dict(json.loads(json.dumps(p.to_dict())))
    assert again == p
    assert again.episode.cue(1).thai == "สวัสดี"
    assert again.episode.character("yuki").address_terms == {"お姉ちゃん": "พี่"}
    assert again.episode.character("nobody") is None


def test_save_and_load_project(tmp_path):
    video = tmp_path / "EP01.mkv"
    video.write_bytes(b"")
    ep = make_episode(2)
    ep.video_path = str(video)
    p = Project(episode=ep, backend="api", model="claude-opus-5-5")
    path = save_project(p)
    assert path == project_path(video) == tmp_path / "EP01.stproj.json"
    assert load_project(video) == p
    assert load_project(tmp_path / "missing.mkv") is None


def _project(tmp_path):
    video = tmp_path / "EP01.mkv"
    video.write_bytes(b"")
    ep = make_episode(2)
    ep.video_path = str(video)
    return video, Project(episode=ep, backend="api", model="claude-opus-5-5")


def test_corrupt_project_file_raises_thai_error(tmp_path):
    video, _ = _project(tmp_path)
    project_path(video).write_text("{not json", encoding="utf-8")
    with pytest.raises(ProjectFileError) as e:
        load_project(video)
    assert "ไฟล์งาน EP01.stproj.json เสีย" in str(e.value)
    assert isinstance(e.value, ValueError)


def test_save_retries_replace_on_permission_error(tmp_path, monkeypatch):
    video, p = _project(tmp_path)
    real, calls = os.replace, []

    def flaky(a, b):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("locked")
        return real(a, b)

    monkeypatch.setattr(os, "replace", flaky)
    path = save_project(p)
    assert len(calls) == 3 and path.exists()
    assert not path.with_name(path.name + ".tmp").exists()


def test_save_failure_cleans_tmp(tmp_path, monkeypatch):
    video, p = _project(tmp_path)
    monkeypatch.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(PermissionError("locked")))
    with pytest.raises(PermissionError):
        save_project(p)
    assert list(tmp_path.glob("*.tmp")) == []
