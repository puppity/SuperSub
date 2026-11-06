import json
import subprocess

import pytest

from subtitle_translator.ai.backend import AIErrorKind, AIRequest
from subtitle_translator.ai.claude_code_backend import ClaudeCodeBackend

SCHEMA = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}
REQ = AIRequest(system="SYS", context="CTX", task="TASK", schema=SCHEMA)


def proc(result, is_error=False, code=0, stderr=""):
    out = json.dumps({"type": "result", "is_error": is_error, "result": result})
    return subprocess.CompletedProcess(args=[], returncode=code,
                                       stdout=out.encode("utf-8"), stderr=stderr.encode("utf-8"))


class Runner:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, args, **kw):
        self.calls.append((args, kw))
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_success_with_code_fence_and_prompt_shape():
    runner = Runner(proc('```json\n{"ok": true}\n```'))
    b = ClaudeCodeBackend("claude-opus-5-5", exe="claude", runner=runner)
    res = b.run_json(REQ)
    assert res.data == {"ok": True} and res.cost_usd is None
    args, kw = runner.calls[0]
    assert args[:6] == ["claude", "-p", "--output-format", "json", "--model", "opus"]
    prompt = kw["input"].decode("utf-8")
    assert prompt.index("SYS") < prompt.index("CTX") < prompt.index("TASK")
    assert '"required": ["ok"]' in prompt
    assert kw["timeout"] == 600


def test_invalid_json_is_retried_once():
    runner = Runner(proc("sorry"), proc('{"ok": false}'))
    res = ClaudeCodeBackend("claude-sonnet-5-5", exe="claude", runner=runner).run_json(REQ)
    assert res.data == {"ok": False}
    assert "previous reply was invalid" in runner.calls[1][1]["input"].decode("utf-8")


def test_invalid_twice_raises():
    runner = Runner(proc("x"), proc("y"))
    with pytest.raises(Exception) as e:
        ClaudeCodeBackend("claude-opus-5-5", exe="claude", runner=runner).run_json(REQ)
    assert e.value.kind is AIErrorKind.INVALID_OUTPUT


def test_usage_limit_maps_to_quota_with_reset_time():
    runner = Runner(proc("Claude usage limit reached. Your limit resets at 7pm (Asia/Bangkok).", is_error=True, code=1))
    with pytest.raises(Exception) as e:
        ClaudeCodeBackend("claude-opus-5-5", exe="claude", runner=runner).run_json(REQ)
    assert e.value.kind is AIErrorKind.QUOTA
    assert e.value.retry_after == "7pm (Asia/Bangkok)"


@pytest.mark.parametrize("text", [
    "Invalid API key · Please run /login",
    "Failed to authenticate: OAuth session expired and could not be refreshed",   # observed on the real CLI
])
def test_not_logged_in(text):
    runner = Runner(proc(text, is_error=True, code=1))
    with pytest.raises(Exception) as e:
        ClaudeCodeBackend("claude-opus-5-5", exe="claude", runner=runner).run_json(REQ)
    assert e.value.kind is AIErrorKind.AUTH


def test_not_installed():
    runner = Runner(FileNotFoundError())
    with pytest.raises(Exception) as e:
        ClaudeCodeBackend("claude-opus-5-5", exe="claude", runner=runner).run_json(REQ)
    assert e.value.kind is AIErrorKind.NOT_INSTALLED


def test_estimate_is_none_and_check():
    b = ClaudeCodeBackend("claude-opus-5-5", exe="claude", runner=Runner(proc("OK")))
    assert b.estimate_cost([REQ], 1000) is None
    assert b.check() == "เชื่อมต่อ Claude Code ได้"


def test_factory_picks_backend(monkeypatch):
    from subtitle_translator.ai import factory
    from subtitle_translator.ai.api_backend import ApiBackend
    from subtitle_translator.ai.backend import AIError
    from subtitle_translator.core.settings import Settings

    assert isinstance(factory.make_backend(Settings(backend="claude_code")), ClaudeCodeBackend)
    monkeypatch.setattr(factory, "get_api_key", lambda: None)
    with pytest.raises(AIError) as e:
        factory.make_backend(Settings(backend="api"))
    assert e.value.kind is AIErrorKind.AUTH
    monkeypatch.setattr(factory, "get_api_key", lambda: "k")
    assert isinstance(factory.make_backend(Settings(backend="api")), ApiBackend)
