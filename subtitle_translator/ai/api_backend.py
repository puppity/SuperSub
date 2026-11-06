from __future__ import annotations

import json
import logging

import anthropic

from ..core.pricing import MODELS, cost_usd
from .backend import AIError, AIErrorKind, AIRequest, AIResult, Usage, validate_json

log = logging.getLogger(__name__)
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def build_params(model: str, req: AIRequest) -> dict:
    return {
        "model": model,
        "max_tokens": 64000,
        "system": [
            {"type": "text", "text": req.system},
            {"type": "text", "text": req.context, "cache_control": {"type": "ephemeral"}},
        ],
        "messages": [{"role": "user", "content": req.task}],
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": req.effort,
                          "format": {"type": "json_schema", "schema": req.schema}},
    }


class ApiBackend:
    name = "api"

    def __init__(self, api_key: str, model: str, client=None):
        self.model = model
        self.client = client or anthropic.Anthropic(api_key=api_key, max_retries=4)

    def _send(self, params: dict):
        if self.model in FALLBACK_MODELS:
            with self.client.beta.messages.stream(**params, betas=[FALLBACK_BETA], fallbacks="default") as s:
                return s.get_final_message()
        with self.client.messages.stream(**params) as s:
            return s.get_final_message()

    def run_json(self, req: AIRequest) -> AIResult:
        try:
            msg = self._send(build_params(self.model, req))
        except anthropic.AuthenticationError as exc:
            raise AIError(AIErrorKind.AUTH, "API key ไม่ถูกต้อง") from exc
        except anthropic.PermissionDeniedError as exc:
            raise AIError(AIErrorKind.AUTH, "API key นี้ไม่มีสิทธิ์ใช้โมเดลนี้") from exc
        except anthropic.RateLimitError as exc:
            raise AIError(AIErrorKind.RATE_LIMIT, "เรียก API ถี่เกินไป ลองใหม่อีกครู่") from exc
        except anthropic.BadRequestError as exc:
            if "credit balance" in str(exc).lower():
                raise AIError(AIErrorKind.QUOTA, "เครดิต API หมด — เติมเงินที่ Claude Console") from exc
            raise AIError(AIErrorKind.BAD_REQUEST, f"คำขอไม่ถูกต้อง: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise AIError(AIErrorKind.NETWORK, f"เซิร์ฟเวอร์ตอบ {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise AIError(AIErrorKind.NETWORK, "เชื่อมต่ออินเทอร์เน็ตไม่ได้") from exc

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
        for req in reqs:
            p = build_params(self.model, req)
            counted = self.client.messages.count_tokens(model=p["model"], system=p["system"],
                                                        messages=p["messages"])
            total_in += counted.input_tokens
        return cost_usd(self.model, total_in, expected_output_tokens)

    def check(self) -> str:
        try:
            self.client.models.retrieve(self.model)
        except anthropic.AuthenticationError as exc:
            raise AIError(AIErrorKind.AUTH, "API key ไม่ถูกต้อง") from exc
        except anthropic.APIConnectionError as exc:
            raise AIError(AIErrorKind.NETWORK, "เชื่อมต่ออินเทอร์เน็ตไม่ได้") from exc
        return "เชื่อมต่อ API ได้"
