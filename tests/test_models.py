import json

from helpers import make_episode
from subtitle_translator.core.models import Character, Project
from subtitle_translator.core.project_store import load_project, project_path, save_project


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
