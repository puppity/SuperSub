from __future__ import annotations

import base64

import json
import logging
from contextlib import contextmanager

from ..core.pricing import MODELS, cost_usd
from .backend import AIError, AIErrorKind, AIRequest, AIResult, Usage, validate_json

log = logging.getLogger(__name__)
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def map_sdk_error(exc: Exception) -> AIError | None:
    """Translate an Anthropic SDK exception into an AIError with a Thai message.

    Returns None for exceptions that are not SDK errors. Raw English SDK text is never
    forwarded; only the HTTP status code is appended.
    """
    import anthropic        # lazy: the SDK costs seconds to import
    if isinstance(exc, anthropic.APIStatusError):
        code = exc.status_code
        if code in (401, 403):
            msg = "API key ไม่ถูกต้อง" if code == 401 else "API key นี้ไม่มีสิทธิ์ใช้โมเดลนี้"
            return AIError(AIErrorKind.AUTH, f"{msg} ({code})")
        if code == 429:
            return AIError(AIErrorKind.RATE_LIMIT, f"เรียก API ถี่เกินไป ลองใหม่อีกครู่ ({code})")
        if code >= 500:
            return AIError(AIErrorKind.NETWORK, f"เซิร์ฟเวอร์ Claude ขัดข้อง ({code})")
        if "credit balance" in str(exc).lower():
            return AIError(AIErrorKind.QUOTA, "เครดิต API หมด — เติมเงินที่ Claude Console")
        return AIError(AIErrorKind.BAD_REQUEST, f"คำขอไม่ถูกต้อง ({code})")
    if isinstance(exc, anthropic.APITimeoutError):
        return AIError(AIErrorKind.NETWORK, "เซิร์ฟเวอร์ตอบช้าเกินไป ลองใหม่อีกครั้ง")
    if isinstance(exc, anthropic.APIConnectionError):
        return AIError(AIErrorKind.NETWORK, "เชื่อมต่ออินเทอร์เน็ตไม่ได้")
    return None


@contextmanager
def sdk_errors():
    try:
        yield
    except AIError:
        raise
    except Exception as exc:
        mapped = map_sdk_error(exc)
        if mapped is None:
            raise
        raise mapped from exc


def user_content(req: AIRequest):
    if not req.images:
        return req.task
    blocks = []
    for label, data in req.images:
        blocks.append({"type": "text", "text": label})
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                   "data": base64.b64encode(data).decode("ascii")}})
    blocks.append({"type": "text", "text": req.task})
    return blocks


def build_params(model: str, req: AIRequest) -> dict:
    return {
        "model": model,
        "max_tokens": 64000,
        "system": [
            {"type": "text", "text": req.system},
            {"type": "text", "text": req.context, "cache_control": {"type": "ephemeral"}},
        ],
        "messages": [{"role": "user", "content": user_content(req)}],
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": req.effort,
                          "format": {"type": "json_schema", "schema": req.schema}},
    }


class ApiBackend:
    name = "api"

    def __init__(self, api_key: str, model: str, client=None):
        self.model = model
        if client is None:
            import anthropic        # lazy: the SDK costs seconds to import and the GUI imports this module
            client = anthropic.Anthropic(api_key=api_key, max_retries=4)
        self.client = client

    def _send(self, params: dict):
        if self.model in FALLBACK_MODELS:
            with self.client.beta.messages.stream(**params, betas=[FALLBACK_BETA], fallbacks="default") as s:
                return s.get_final_message()
        with self.client.messages.stream(**params) as s:
            return s.get_final_message()

    def run_json(self, req: AIRequest) -> AIResult:
        with sdk_errors():
            msg = self._send(build_params(self.model, req))

        if msg.stop_reason == "refusal":
            raise AIError(AIErrorKind.REFUSAL, "Claude ปฏิเสธการแปลช่วงนี้")
        if msg.stop_reason == "max_tokens":
            raise AIError(AIErrorKind.TRUNCATED, "คำตอบยาวเกินและถูกตัด")
        text = "".join(b.text for b in msg.content if b.type == "text")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AIError(AIErrorKind.INVALID_OUTPUT, "คำตอบไม่ใช่ JSON") from exc
        validate_json(data, req.schema)

        u = msg.usage
        usage = Usage(input_tokens=u.input_tokens, output_tokens=u.output_tokens,
                      cache_read=u.cache_read_input_tokens or 0,
                      cache_write=u.cache_creation_input_tokens or 0)
        priced = msg.model if msg.model in MODELS else self.model
        cost = cost_usd(priced, usage.input_tokens, usage.output_tokens, usage.cache_read, usage.cache_write)
        log.info("api call model=%s in=%d out=%d cache_read=%d cost=%.4f",
                 priced, usage.input_tokens, usage.output_tokens, usage.cache_read, cost)
        return AIResult(data, usage, cost)

    def estimate_cost(self, reqs: list[AIRequest], expected_output_tokens: int) -> float | None:
        total_in = 0
        with sdk_errors():
            for req in reqs:
                p = build_params(self.model, req)
                counted = self.client.messages.count_tokens(model=p["model"], system=p["system"],
                                                            messages=p["messages"])
                total_in += counted.input_tokens
        return cost_usd(self.model, total_in, expected_output_tokens)

    def check(self) -> str:
        with sdk_errors():
            self.client.models.retrieve(self.model)
        return "เชื่อมต่อ API ได้"
