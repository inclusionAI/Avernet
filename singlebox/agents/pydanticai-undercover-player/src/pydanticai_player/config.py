"""Composition-root configuration. Only this module reads process environment."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str
    api_key: SecretStr
    model: str = Field(min_length=1)
    bcs_ws_url: str = "ws://127.0.0.1:21000/ws/bot"
    bcs_api_url: str = "http://127.0.0.1:21000"
    disable_thinking: bool = True
    name: str = "PydanticAI 玩家"
    state_file: Path = Path(".state/identity.json")
    timeout: float = Field(default=60, gt=0, le=120)

    @field_validator("base_url", "bcs_api_url", "bcs_ws_url")
    @classmethod
    def valid_endpoint(cls, value: str) -> str:
        url = urlsplit(value)
        if url.scheme not in {"http", "https", "ws", "wss"} or not url.hostname:
            raise ValueError("A complete HTTP(S) or WS(S) endpoint is required")
        if url.username or url.password or url.query or url.fragment:
            raise ValueError("Endpoint must not contain credentials, query or fragment")
        return value.rstrip("/")


def load_settings() -> Settings:
    required = {
        "base_url": "PLAYER_MODEL_BASE_URL",
        "api_key": "DEEPSEEK_API_KEY",
        "model": "PLAYER_MODEL",
    }
    missing = [key for key in required.values() if not os.environ.get(key)]
    if missing:
        raise ValueError("Set required environment variables: " + ", ".join(missing))
    values = {name: os.environ[key] for name, key in required.items()}
    for field, key in {
        "bcs_ws_url": "PLAYER_BCS_WS_URL",
        "bcs_api_url": "PLAYER_BCS_API_URL",
        "name": "PLAYER_NAME",
        "state_file": "PLAYER_STATE_FILE",
        "timeout": "PLAYER_TIMEOUT",
        "disable_thinking": "PLAYER_DISABLE_THINKING",
    }.items():
        if key in os.environ:
            values[field] = os.environ[key]
    return Settings(**values)
