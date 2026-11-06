from __future__ import annotations

from ..core.settings import Settings, get_api_key
from .api_backend import ApiBackend
from .backend import AIBackend, AIError, AIErrorKind
from .claude_code_backend import ClaudeCodeBackend


def make_backend(settings: Settings) -> AIBackend:
    if settings.backend == "claude_code":
        return ClaudeCodeBackend(settings.model)
    key = get_api_key()
    if not key:
        raise AIError(AIErrorKind.AUTH, "ยังไม่ได้ใส่ API key — ไปที่หน้าตั้งค่า")
    return ApiBackend(key, settings.model)
