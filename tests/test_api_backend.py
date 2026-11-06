from types import SimpleNamespace

import anthropic
import httpx2 as httpx   # anthropic 2.x builds on httpx2 (plain httpx is not installed)
import pytest

from subtitle_translator.ai.api_backend import ApiBackend, build_params
from subtitle_translator.ai.backend import AIErrorKind, AIRequest

REQ = AIRequest(system="SYS", context="CTX", task="TASK",
                schema={"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}},
                effort="high")


def message(text='{"ok": true}', stop="end_turn", model="claude-opus-5-5"):
    usage = SimpleNamespace(input_tokens=1000, output_tokens=500,
                            cache_read_input_tokens=2000, cache_creation_input_tokens=0)
    return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking=""),
                                    SimpleNamespace(type="text", text=text)],
                           stop_reason=stop, usage=usage, model=model)


class Stream:
    def __init__(self, result):
        self.result = result

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeMessages:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def stream(self, **kw):
        self.calls.append(kw)
        return Stream(self.result)

    def count_tokens(self, **kw):
        return SimpleNamespace(input_tokens=250_000)


def fake_client(result):
    beta_msgs = FakeMessages(result)
    msgs = FakeMessages(result)
    return SimpleNamespace(messages=msgs, beta=SimpleNamespace(messages=beta_msgs),
                           models=SimpleNamespace(retrieve=lambda m: SimpleNamespace(id=m)))


def status_error(cls, code, msg="x"):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls(message=msg, response=httpx.Response(code, request=req), body=None)


def test_build_params():
    p = build_params("claude-opus-5-5", REQ)
    assert p["model"] == "claude-opus-5-5" and p["max_tokens"] == 64000
    assert p["thinking"] == {"type": "adaptive"}
    assert p["output_config"]["effort"] == "high"
    assert p["output_config"]["format"] == {"type": "json_schema", "schema": REQ.schema}
    assert p["system"][0]["text"] == "SYS"
    assert p["system"][1] == {"type": "text", "text": "CTX", "cache_control": {"type": "ephemeral"}}
    assert p["messages"] == [{"role": "user", "content": "TASK"}]
    assert "temperature" not in p


def test_opus_uses_beta_fallback_and_prices_usage():
    client = fake_client(message())
    res = ApiBackend("k", "claude-opus-5-5", client=client).run_json(REQ)
    call = client.beta.messages.calls[0]
    assert call["betas"] == ["server-side-fallback-2026-07-01"] and call["fallbacks"] == "default"
    assert client.messages.calls == []
    assert res.data == {"ok": True}
    assert res.usage.cache_read == 2000
    assert res.cost_usd == pytest.approx((1000 * 4 + 500 * 20 + 2000 * 0.2) / 1e6)


def test_haiku_has_no_fallback():
    client = fake_client(message(model="claude-haiku-5-5"))
    ApiBackend("k", "claude-haiku-5-5", client=client).run_json(REQ)
    assert client.beta.messages.calls == []
    assert "fallbacks" not in client.messages.calls[0]


@pytest.mark.parametrize("msg, kind", [
    (message(stop="refusal"), AIErrorKind.REFUSAL),
    (message(stop="max_tokens"), AIErrorKind.TRUNCATED),
    (message(text="not json"), AIErrorKind.INVALID_OUTPUT),
    (message(text='{"nope": 1}'), AIErrorKind.INVALID_OUTPUT),
])
def test_bad_responses(msg, kind):
    with pytest.raises(Exception) as e:
        ApiBackend("k", "claude-opus-5-5", client=fake_client(msg)).run_json(REQ)
    assert e.value.kind is kind


@pytest.mark.parametrize("exc, kind", [
    (status_error(anthropic.AuthenticationError, 401), AIErrorKind.AUTH),
    (status_error(anthropic.RateLimitError, 429), AIErrorKind.RATE_LIMIT),
    (status_error(anthropic.BadRequestError, 400, "Your credit balance is too low"), AIErrorKind.QUOTA),
    (status_error(anthropic.BadRequestError, 400, "bad"), AIErrorKind.BAD_REQUEST),
    (status_error(anthropic.InternalServerError, 500), AIErrorKind.NETWORK),
])
def test_sdk_errors_are_mapped(exc, kind):
    with pytest.raises(Exception) as e:
        ApiBackend("k", "claude-opus-5-5", client=fake_client(exc)).run_json(REQ)
    assert e.value.kind is kind


def test_estimate_and_check():
    b = ApiBackend("k", "claude-opus-5-5", client=fake_client(message()))
    assert b.estimate_cost([REQ, REQ], 100_000) == pytest.approx((500_000 * 4 + 100_000 * 20) / 1e6)
    assert b.check() == "เชื่อมต่อ API ได้"
