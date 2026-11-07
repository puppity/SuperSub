import subprocess
from pathlib import Path

import pytest

from fakes import FakeBackend
from helpers import make_episode
from subtitle_translator.ai.backend import validate_json
from subtitle_translator.core import frames as fr
from subtitle_translator.core.media import tool_path
from subtitle_translator.core.models import Character
from subtitle_translator.core.series import SeriesMemory, load_memory
from subtitle_translator.core.settings import Settings
from subtitle_translator.translate import pipeline
from subtitle_translator.translate.analyze import analyze, build_analyze_request

CHAR = {"key": "gou", "name_src": "Gou", "name_th": "โก", "gender": "male", "role": "หัวหน้าแก๊ง",
        "relations": [], "address_terms": [], "alignment": "villain", "self_pronoun": "ข้า",
        "you_pronoun": "แก", "speech_style": "ข่มขู่", "face_line": 2}


def data(face_line=2):
    return {"summary": "s", "glossary": [], "cues": [{"id": 2, "speaker": "gou", "addressee": "", "confidence": 0.9}],
            "characters": [dict(CHAR, face_line=face_line)]}


def test_pick_cues_spreads_and_prefers_long_lines():
    ep = make_episode(100)
    ep.cues[10].end = ep.cues[10].start + 0.3
    picked = fr.pick_cues(ep.cues, 10)
    assert len(picked) == 10 and all(c.end - c.start >= fr.MIN_CUE_S for c in picked)
    assert picked[0].id < 15 and picked[-1].id > 85
    assert fr.pick_cues(make_episode(3).cues, 10) == make_episode(3).cues


def test_vision_request_labels_frames_and_faces_and_keeps_face(tmp_path):
    ep = make_episode(3)
    ep.video_path = str(tmp_path / "Show - 02.mkv")
    shots = []
    for i in (1, 2):
        p = tmp_path / f"f{i}.jpg"
        p.write_bytes(b"jpeg%d" % i)
        shots.append(fr.Frame(i, float(i), p))
    faces = tmp_path / "Show" / "faces"
    faces.mkdir(parents=True)
    (faces / "jun.jpg").write_bytes(b"face")
    mem = SeriesMemory(title="Show", characters=[Character("jun", "Jun", "จุน", "female", face="jun.jpg")])
    backend = FakeBackend([data()])
    analyze(ep, backend, "high", mem, shots)
    req = backend.requests[0]
    labels = [label for label, _ in req.images]
    assert labels[0].startswith('REFERENCE FACE of known character key "jun"')
    assert labels[1:] == ["FRAME at 00:01.0, during line 1:", "FRAME at 00:02.0, during line 2:"]
    assert "PICTURES:" in req.context and "face_line" in str(req.schema)
    validate_json(data(), req.schema)
    gou = ep.character("gou")
    assert gou.face == "gou.jpg" and (faces / "gou.jpg").read_bytes() == b"jpeg2"
    assert (gou.alignment, gou.self_pronoun, gou.you_pronoun) == ("villain", "ข้า", "แก")


def test_text_mode_sends_no_pictures():
    req = build_analyze_request(make_episode(2), "high")
    assert req.images == [] and "face_line" not in str(req.schema) and "PICTURES" not in req.context


SRT = "".join(f"{i}\n00:00:0{i},000 --> 00:00:0{i},900\nline {i}\n\n" for i in range(1, 4))


@pytest.fixture
def tiny_video(tmp_path):
    v = tmp_path / "Show - 01.mp4"
    r = subprocess.run([tool_path("ffmpeg"), "-y", "-v", "error", "-f", "lavfi", "-i",
                        "testsrc=s=320x240:d=5:r=10", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(v)],
                       capture_output=True)
    if r.returncode != 0:
        pytest.skip("ffmpeg cannot make a test video here")
    (tmp_path / "Show - 01.srt").write_text(SRT, encoding="utf-8")
    return v


def test_pipeline_picture_mode_captures_real_frames_and_saves_face(tiny_video, tmp_path):
    from test_translate import echo
    s = Settings(vision_mode=True, vision_frames=2, chunk_size=10)
    p = pipeline.prepare_project(tiny_video, tiny_video.with_suffix(".srt"), s, tmp_path / "cache")
    seen = []
    backend = FakeBackend([lambda req: data(face_line=int(req.images[-1][0].split("line ")[1][:-1])), echo])
    pipeline.run_project(p, backend, s, lambda st, f: seen.append(st), lambda: False)
    req = backend.requests[0]
    assert len(req.images) == 2 and all(img[:2] == b"\xff\xd8" for _, img in req.images)
    assert "เก็บภาพจากวิดีโอ" in seen and "วิเคราะห์ตัวละคร (อ่านภาพ)" in seen
    assert (tmp_path / "Show" / "faces" / "gou.jpg").exists()
    assert load_memory(tiny_video).characters[0].face == "gou.jpg"


def test_picture_mode_falls_back_to_text_when_video_unreadable(tmp_path):
    from test_translate import echo
    v = tmp_path / "Show - 01.mkv"
    v.write_bytes(b"not a video")
    (tmp_path / "Show - 01.srt").write_text(SRT, encoding="utf-8")
    s = Settings(vision_mode=True, chunk_size=10)
    p = pipeline.prepare_project(v, v.with_suffix(".srt"), s, tmp_path / "cache")
    backend = FakeBackend([data(), echo])
    pipeline.run_project(p, backend, s, lambda *a: None, lambda: False)
    assert backend.requests[0].images == [] and p.stage == "done"
