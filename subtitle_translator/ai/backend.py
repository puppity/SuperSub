from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

import jsonschema


class AIErrorKind(str, Enum):
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    QUOTA = "quota"
    BAD_REQUEST = "bad_request"
    REFUSAL = "refusal"
    TRUNCATED = "truncated"
    INVALID_OUTPUT = "invalid_output"
    NETWORK = "network"
    NOT_INSTALLED = "not_installed"


class AIError(Exception):
    def __init__(self, kind: AIErrorKind, message: str, retry_after: str | None = None):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0


@dataclass
class AIResult:
    data: Any
    usage: Usage = field(default_factory=Usage)
    cost_usd: float | None = None


@dataclass
class AIRequest:
    system: str
    context: str
    task: str
    schema: dict
    effort: str = "medium"


class AIBackend(Protocol):
    name: str
    model: str

    def run_json(self, req: AIRequest) -> AIResult: ...

    def estimate_cost(self, reqs: list[AIRequest], expected_output_tokens: int) -> float | None: ...

    def check(self) -> str: ...


def validate_json(data: Any, schema: dict) -> None:
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as exc:
        raise AIError(AIErrorKind.INVALID_OUTPUT, f"ผลลัพธ์ไม่ตรงรูปแบบ: {exc.message}") from exc
