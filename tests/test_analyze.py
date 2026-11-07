from fakes import FakeBackend
from helpers import make_episode
from subtitle_translator.ai.backend import validate_json
from subtitle_translator.translate.analyze import PALETTE, analyze, build_analyze_request
from subtitle_translator.translate.prompts import ANALYZE_SCHEMA, format_cues

ANALYSIS = {
    "series_title": "Sample Show", "episode_number": 1,
    "summary": "พี่น้องสาวตื่นสายไปโรงเรียน",
    "glossary": [{"source": "Cyclone Magnum", "thai": "ไซโคลนแม็กนั่ม"}],
    "characters": [
        {"key": "yuki", "name_src": "ユキ", "name_th": "ยูกิ", "gender": "female",
         "role": "พี่สาว ม.ปลาย", "relations": ["พี่สาวของฮารุ"],
         "address_terms": [{"source": "お姉ちゃん", "thai": "พี่"}],
         "alignment": "hero", "self_pronoun": "ฉัน", "you_pronoun": "เธอ", "speech_style": "สุภาพ"},
        {"key": "haru", "name_src": "ハル", "name_th": "ฮารุ", "gender": "female",
         "role": "น้องสาว ม.ต้น", "relations": ["น้องสาวของยูกิ"], "address_terms": [],
         "alignment": "neutral", "self_pronoun": "หนู", "you_pronoun": "พี่", "speech_style": ""},
    ],
    "cues": [
        {"id": 1, "speaker": "haru", "addressee": "ยูกิ", "confidence": 0.9},
        {"id": 2, "speaker": "yuki", "addressee": "ฮารุ", "confidence": 1.4},
        {"id": 3, "speaker": "ghost", "addressee": "", "confidence": 0.3},
    ],
}


def test_schema_accepts_sample():
    validate_json(ANALYSIS, ANALYZE_SCHEMA)


def test_format_cues_skips_effects_and_flattens_newlines():
    ep = make_episode(3)
    ep.cues[0].src = "a\nb"
    ep.cues[1].skip = True
    ep.cues[2].speaker = "yuki"
    ep.cues[2].addressee = "ฮารุ"
    out = format_cues(ep.cues, names={"yuki": "ยูกิ"})
    lines = out.splitlines()
    assert lines[0] == "1\t00:02.0\t1.5s\t?→?\ta / b"
    assert lines[1] == "3\t00:06.0\t1.5s\tยูกิ→ฮารุ\tline 3"
    assert len(lines) == 2


def test_format_cues_rounds_time_before_splitting_minutes():
    ep = make_episode(1)
    ep.cues[0].start = 59.96
    ep.cues[0].end = 61.0
    out = format_cues(ep.cues)
    assert out == "1\t01:00.0\t1.0s\t-\tline 1"


def test_format_cues_blank_speaker_name_falls_back_to_question_mark():
    ep = make_episode(1)
    ep.cues[0].speaker = "yuki"
    ep.cues[0].addressee = "ฮารุ"
    out = format_cues(ep.cues, names={"yuki": ""})
    assert out.split("\t")[3] == "?→ฮารุ"


def test_analyze_applies_characters_and_speakers():
    ep = make_episode(3)
    backend = FakeBackend([ANALYSIS])
    res = analyze(ep, backend, "high")
    req = backend.requests[0]
    assert req.effort == "high" and req.schema is ANALYZE_SCHEMA
    assert "Japanese" in req.context and "line 3" in req.context
    assert [c.key for c in ep.characters] == ["yuki", "haru"]
    assert ep.characters[0].address_terms == {"お姉ちゃん": "พี่"}
    assert [c.color for c in ep.characters] == PALETTE[:2]
    assert ep.summary.startswith("พี่น้อง")
    assert (ep.cues[0].speaker, ep.cues[0].speaker_conf) == ("haru", 0.9)
    assert ep.cues[1].speaker_conf == 1.0
    assert ep.cues[2].speaker is None
    assert res.cost_usd == 0.01


def test_build_request_is_stable_for_caching():
    ep = make_episode(2)
    a = build_analyze_request(ep, "high")
    b = build_analyze_request(ep, "high")
    assert (a.system, a.context) == (b.system, b.context)


def test_voice_fields_reach_episode_and_translate_sheet():
    from subtitle_translator.translate.analyze import apply_analysis
    from subtitle_translator.translate.prompts import format_sheet
    ep = make_episode(3)
    apply_analysis(ep, ANALYSIS)
    yuki = ep.character("yuki")
    assert (yuki.alignment, yuki.self_pronoun, yuki.you_pronoun) == ("hero", "ฉัน", "เธอ")
    sheet = format_sheet(ep)
    assert '"self_pronoun": "ฉัน"' in sheet and '"alignment": "hero"' in sheet
