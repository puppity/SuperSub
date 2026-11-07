from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import keyring
import platformdirs
from keyring.errors import PasswordDeleteError

from .pricing import DEFAULT_MODEL

APP = "SubTranslator"
KEYRING_SERVICE = "SubTranslator"
KEYRING_USER = "anthropic_api_key"


@dataclass
class Settings:
    backend: str = "api"                    # "api" | "claude_code"
    model: str = DEFAULT_MODEL
    effort_analyze: str = "high"
    effort_translate: str = "medium"
    chunk_size: int = 80
    prefer_langs: list[str] = field(default_factory=lambda: ["ja", "en"])
    output_dir: str | None = None           # None = beside the video
    ass_font: str = "Leelawadee UI"
    ass_size: int = 52
    srt_colors: bool = False
    auto_transcribe: bool = True            # no subtitle at all: make one from the audio
    whisper_model: str = "large-v3-turbo"
    mux_mkv: bool = False                   # also write <video>.th.mkv with the Thai track
    vision_mode: bool = False               # also show the AI frames from the video when analysing
    vision_frames: int = 36


def default_settings_path() -> Path:
    return Path(platformdirs.user_config_dir(APP, appauthor=False)) / "settings.json"


def cache_dir() -> Path:
    p = Path(platformdirs.user_cache_dir(APP, appauthor=False))
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_dir() -> Path:
    p = Path(platformdirs.user_log_dir(APP, appauthor=False))
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_settings(path: Path | None = None) -> Settings:
    path = path or default_settings_path()
    if not path.exists():
        return Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Settings()
    if not isinstance(raw, dict):
        return Settings()
    known = {f.name for f in fields(Settings)}
    return Settings(**{k: v for k, v in raw.items() if k in known})


def save_settings(s: Settings, path: Path | None = None) -> None:
    path = path or default_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(s), ensure_ascii=False, indent=1), encoding="utf-8")


def get_api_key() -> str | None:
    return keyring.get_password(KEYRING_SERVICE, KEYRING_USER)


def set_api_key(key: str) -> None:
    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key.strip())


def clear_api_key() -> None:
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
    except PasswordDeleteError:
        pass
