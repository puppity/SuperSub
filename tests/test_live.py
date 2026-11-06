import pytest

from helpers import make_episode
from subtitle_translator.ai.api_backend import ApiBackend
from subtitle_translator.ai.claude_code_backend import ClaudeCodeBackend, find_claude
from subtitle_translator.core.settings import get_api_key
from subtitle_translator.translate.analyze import analyze
from subtitle_translator.translate.translate import translate_chunk

LINES = [
    ("お姉ちゃん、起きて！遅刻するよ！", "Haru"), ("うるさいなぁ…あと五分だけ。", "Yuki"),
    ("もう！お母さんに言いつけるからね！", "Haru"), ("わかった、わかったよ、ハル。", "Yuki"),
    ("お姉ちゃん、朝ごはんできてるよ。", "Haru"), ("ありがとう。ハルは本当にしっかり者だね。", "Yuki"),
] * 3 + [("おはよう。二人とも元気だね。", "Tanaka"), ("おはようございます、先生！", "Yuki")]


def live_episode():
    ep = make_episode(len(LINES))
    for c, (text, actor) in zip(ep.cues, LINES):
        c.src, c.actor = text, actor
    return ep


def backends():
    out = []
    if get_api_key():
        out.append(ApiBackend(get_api_key(), "claude-opus-5-5"))
    if find_claude():
        out.append(ClaudeCodeBackend("claude-sonnet-5-5"))
    return out


@pytest.mark.live
@pytest.mark.parametrize("backend", backends(), ids=lambda b: b.name)
def test_live_translation(backend):
    ep = live_episode()
    analyze(ep, backend, "high")
    translate_chunk(ep, [c.id for c in ep.cues], backend, "medium")
    assert all(c.thai for c in ep.cues), [c.id for c in ep.cues if not c.thai]
    for c in ep.cues:
        if "お姉ちゃん" in c.src:
            assert "พี่" in c.thai, c.thai
