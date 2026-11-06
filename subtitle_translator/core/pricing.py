from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelInfo:
    id: str
    label: str
    input: float        # USD per 1M input tokens
    output: float       # USD per 1M output tokens
    cache_read: float   # USD per 1M cached input tokens read
    recommended: bool = False
    note: str = ""


# Haiku 5.5: prices are for prompts up to 100K tokens; its cache-read price is
# assumed at 10% of input. Re-check both against the live pricing page before release.
MODELS: dict[str, ModelInfo] = {
    "claude-opus-5-5": ModelInfo("claude-opus-5-5", "Claude Opus 5.5", 4.00, 20.00, 0.20, True,
                                 "แปลดีที่สุด จับน้ำเสียงและความสัมพันธ์ตัวละครได้แม่น"),
    "claude-sonnet-5-5": ModelInfo("claude-sonnet-5-5", "Claude Sonnet 5.5", 2.00, 10.00, 0.20, False,
                                   "คุณภาพรองลงมา ราคาครึ่งหนึ่ง เหมาะแปลทั้งซีซัน"),
    "claude-haiku-5-5": ModelInfo("claude-haiku-5-5", "Claude Haiku 5.5", 0.10, 0.50, 0.01, False,
                                  "ถูกมาก ใช้ร่างงาน ไม่แนะนำสำหรับแปลจริง"),
}
DEFAULT_MODEL = "claude-opus-5-5"
CACHE_WRITE_MULT = 1.25


def cost_usd(model: str, input_tokens: int, output_tokens: int,
             cache_read: int = 0, cache_write: int = 0) -> float:
    m = MODELS[model]
    total = (input_tokens * m.input + output_tokens * m.output
             + cache_read * m.cache_read + cache_write * m.input * CACHE_WRITE_MULT)
    return total / 1_000_000
