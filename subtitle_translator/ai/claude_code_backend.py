from __future__ import annotations

import json
import os
import re
import shutil
import atexit
import base64
import subprocess
import tempfile
from pathlib import Path

from ..core.media import no_window
from .backend import AIError, AIErrorKind, AIRequest, AIResult, Usage, validate_json
from .json_repair import extract_json

MODEL_ALIAS = {"claude-opus-5-5": "opus", "claude-sonnet-5-5": "sonnet", "claude-haiku-5-5": "haiku"}
_LIMIT = re.compile(r"usage limit|rate limit|limit reached|out of extra usage", re.I)
_RESET = re.compile(r"resets?\s+(?:at\s+)?(.+?)(?:\.\s|\.$|$)", re.I | re.M)
# "failed to authenticate / OAuth session expired" is the real CLI wording when the Max login lapses.
_LOGIN = re.compile(r"/login|log ?in|not authenticated|invalid api key|failed to authenticate|oauth|session expired", re.I)


_WORKDIR: str | None = None


def _shared_workdir() -> str:
    """One lazily-created scratch dir for every backend instance (keeps project CLAUDE.md out)."""
    global _WORKDIR
    if _WORKDIR is None or not os.path.isdir(_WORKDIR):
        _WORKDIR = tempfile.mkdtemp(prefix="subtrans-cc-")
        atexit.register(shutil.rmtree, _WORKDIR, True)
    return _WORKDIR


# The CLI reads untrusted subtitle text, so run it with no tools, no MCP servers, no skills and
# no transcript on disk. (--bare is NOT used: it disables the Max OAuth login.)
# A whole-episode analysis is one long call; 10 minutes was too tight for long episodes.
CALL_TIMEOUT_S = 1800
SAFETY_FLAGS = ["--tools", "", "--strict-mcp-config", "--disable-slash-commands",
                "--no-session-persistence", "--permission-prompts", "none"]


def _last_result(stream_out: str) -> dict | None:
    """The final {"type": "result"} event of a stream-json transcript."""
    found = None
    for line in stream_out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            found = event
    return found


def find_claude() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    for cand in (Path.home() / ".local" / "bin" / "claude.exe",
                 Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd"):
        if cand.exists():
            return str(cand)
    return None


class ClaudeCodeBackend:
    name = "claude_code"

    def __init__(self, model: str, exe: str | None = None, runner=subprocess.run):
        self.model = model
        self.exe = exe
        self.runner = runner

    @property
    def workdir(self) -> str:
        return _shared_workdir()

    def _prompt(self, req: AIRequest, error_note: str | None = None) -> str:
        return "\n\n".join(self._parts(req, error_note))

    @staticmethod
    def _parts(req: AIRequest, error_note: str | None = None) -> tuple[str, str]:
        """(system + context, task + schema): pictures go between the two."""
        tail = [req.task, "Reply with ONLY one JSON value that matches this JSON Schema. "
                "No prose, no code fences, do not use any tools.\n" + json.dumps(req.schema, ensure_ascii=False)]
        if error_note:
            tail.append(error_note)
        return "\n\n".join([req.system, req.context]), "\n\n".join(tail)

    @staticmethod
    def _image_message(req: AIRequest, error_note: str | None = None) -> str:
        """One stream-json user message carrying text and pictures (the CLI's stdin format)."""
        head, tail = ClaudeCodeBackend._parts(req, error_note)
        content: list[dict] = [{"type": "text", "text": head}]
        for label, data in req.images:
            content.append({"type": "text", "text": label})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                        "data": base64.b64encode(data).decode("ascii")}})
        content.append({"type": "text", "text": tail})
        return json.dumps({"type": "user", "message": {"role": "user", "content": content}}) + "\n"

    def _request(self, req: AIRequest, error_note: str | None = None) -> str:
        if req.images:
            return self._call(self._image_message(req, error_note), req.effort, stream=True)
        return self._call(self._prompt(req, error_note), req.effort)

    def _call(self, prompt: str, effort: str | None = None, stream: bool = False) -> str:
        exe = self.exe or find_claude()
        if not exe:
            raise AIError(AIErrorKind.NOT_INSTALLED, "ไม่พบ Claude Code — ติดตั้งแล้วล็อกอินด้วยบัญชี Max ก่อน")
        args = [exe, "-p"]
        # Pictures need the stream-json stdin format, which needs stream-json output too.
        args += (["--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
                 if stream else ["--output-format", "json"])
        args += ["--model", MODEL_ALIAS.get(self.model, "opus")]
        if effort:
            # Without this the CLI uses the user's own default (often xhigh), which made
            # whole-episode analysis take many minutes.
            args += ["--effort", effort]
        args += SAFETY_FLAGS
        try:
            r = self.runner(args, input=prompt.encode("utf-8"), capture_output=True,
                            timeout=CALL_TIMEOUT_S, cwd=self.workdir, **no_window())
        except FileNotFoundError as exc:
            raise AIError(AIErrorKind.NOT_INSTALLED, "ไม่พบ Claude Code — ติดตั้งแล้วล็อกอินด้วยบัญชี Max ก่อน") from exc
        except subprocess.TimeoutExpired as exc:
            raise AIError(AIErrorKind.NETWORK, f"Claude Code ตอบช้าเกิน {CALL_TIMEOUT_S // 60} นาที") from exc
        out = r.stdout.decode("utf-8", "replace")
        err = r.stderr.decode("utf-8", "replace")
        outer = _last_result(out) if stream else None
        if not stream:
            try:
                outer = json.loads(out)
            except json.JSONDecodeError:
                outer = None
        result = outer.get("result", "") if isinstance(outer, dict) else out
        is_error = r.returncode != 0 or (isinstance(outer, dict) and bool(outer.get("is_error")))
        if is_error:
            blob = f"{result}\n{err}"
            if re.search(r"unknown option|unrecognized option|unknown argument", err, re.I):
                raise AIError(AIErrorKind.NOT_INSTALLED, "อัปเดต Claude Code เป็นเวอร์ชันล่าสุด")
            if _LIMIT.search(blob):
                m = _RESET.search(blob)
                raise AIError(AIErrorKind.QUOTA, "โควตา Claude Max เต็ม",
                              retry_after=m.group(1).strip() if m else None)
            if _LOGIN.search(blob):
                raise AIError(AIErrorKind.AUTH,
                              "Claude Code ยังไม่ได้ล็อกอิน — เปิด Terminal แล้วพิมพ์ claude เพื่อล็อกอิน")
            raise AIError(AIErrorKind.BAD_REQUEST, blob.strip()[:500] or f"claude ออกด้วยรหัส {r.returncode}")
        return result

    def _parse(self, text: str, schema: dict):
        data = extract_json(text)          # ValueError if none
        validate_json(data, schema)        # AIError(INVALID_OUTPUT) if wrong shape
        return data

    def run_json(self, req: AIRequest) -> AIResult:
        text = self._request(req)
        try:
            data = self._parse(text, req.schema)
        except (ValueError, AIError) as first:
            note = f"Your previous reply was invalid ({first}). Return only valid JSON for the schema."
            text = self._request(req, note)
            try:
                data = self._parse(text, req.schema)
            except ValueError as exc:
                raise AIError(AIErrorKind.INVALID_OUTPUT, "Claude Code ตอบไม่เป็น JSON") from exc
        return AIResult(data, Usage(), None)

    def estimate_cost(self, reqs: list[AIRequest], expected_output_tokens: int) -> float | None:
        return None

    def check(self) -> str:
        self._call("Reply with exactly: OK", "low")
        return "เชื่อมต่อ Claude Code ได้"
