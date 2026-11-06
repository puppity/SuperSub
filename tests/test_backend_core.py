import pytest

from subtitle_translator.ai.backend import AIError, AIErrorKind, validate_json
from subtitle_translator.ai.json_repair import extract_json

SCHEMA = {"type": "object", "required": ["lines"], "additionalProperties": False,
          "properties": {"lines": {"type": "array", "items": {"type": "integer"}}}}


def test_validate_json():
    validate_json({"lines": [1, 2]}, SCHEMA)
    with pytest.raises(AIError) as e:
        validate_json({"lines": ["x"]}, SCHEMA)
    assert e.value.kind is AIErrorKind.INVALID_OUTPUT


@pytest.mark.parametrize("text, expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('Here you go:\n{"a": [1, {"b": 2}]}\nThanks', {"a": [1, {"b": 2}]}),
    ('result: [{"id": 1}, {"id": 2}] done', [{"id": 1}, {"id": 2}]),
    ('result: [{"id": 1}] done', [{"id": 1}]),
])
def test_extract_json(text, expected):
    assert extract_json(text) == expected


def test_extract_json_fails_cleanly():
    with pytest.raises(ValueError):
        extract_json("no json here")
