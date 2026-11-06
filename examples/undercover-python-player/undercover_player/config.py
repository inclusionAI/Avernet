"""运行配置：从环境变量读取连接、模型与本地存储参数。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

DEFAULT_BOT_NAME = "玩家稳健老陈"
DEFAULT_SUMMARY = (
    "谁是卧底玩家，稳健保守型：发言短、留余地，优先抓前后矛盾，"
    "证据弱时也做相对选择，投票只交票号。"
)
DEFAULT_SKILLS = (
    "one-line-word-description",
    "ambiguity-control",
    "conservative-voting",
    "speech-reading",
)
DEFAULT_DOMAINS = (
    "undercover-game",
    "social-deduction",
    "party-game",
    "word-description",
    "cautious-play",
)
DEFAULT_SCOPES = ("local", "undercover-game", "social-deduction", "party-game")


def _optional_env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser() if value else default


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int) -> int:
    text = os.environ.get(name, "").strip()
    if not text:
        return default
    try:
        value = int(text)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


@dataclass(frozen=True)
class PlayerConfig:
    """一次玩家进程的不可变配置。

    真实对局只需提供 ``UNDERCOVER_BCS_URL``；模型参数复用 singlebox 的
    ``OPENCLAW_OPENAI_*``，也可用 ``UNDERCOVER_LLM_*`` 覆盖。缺少模型参数时
    自动退化为规则兜底，便于无密钥的离线协议验证。
    """

    bcs_url: str = "http://127.0.0.1:21000"
    ws_url: str | None = None
    bot_name: str = DEFAULT_BOT_NAME
    summary: str = DEFAULT_SUMMARY
    skills: tuple[str, ...] = DEFAULT_SKILLS
    domains: tuple[str, ...] = DEFAULT_DOMAINS
    scopes: tuple[str, ...] = DEFAULT_SCOPES
    token_file: Path = field(
        default_factory=lambda: Path.home() / ".avernet" / "undercover-python-player" / "session.json"
    )
    log_dir: Path = field(
        default_factory=lambda: Path.home() / ".avernet" / "undercover-python-player" / "logs"
    )
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_timeout_seconds: float = 45.0
    # DeepSeek Flash 等推理模型会把 reasoning token 计入 max_tokens；96 会在
    # 生成最终一句话前就被截断，表现为 finish_reason=length 且 content 为空。
    llm_max_tokens: int = 2048
    llm_max_tokens_ceiling: int = 8192
    llm_max_attempts: int = 3
    heartbeat_seconds: float = 60.0
    reconnect_initial_seconds: float = 1.0
    reconnect_max_seconds: float = 30.0
    rules_only: bool = False

    @classmethod
    def from_environment(cls) -> PlayerConfig:
        timeout_text = os.environ.get("UNDERCOVER_LLM_TIMEOUT_SECONDS", "45").strip()
        try:
            llm_timeout = float(timeout_text)
        except ValueError as error:
            raise ValueError("UNDERCOVER_LLM_TIMEOUT_SECONDS must be a number") from error
        if llm_timeout <= 0:
            raise ValueError("UNDERCOVER_LLM_TIMEOUT_SECONDS must be positive")
        max_tokens = _env_int("UNDERCOVER_LLM_MAX_TOKENS", 2048, minimum=64)
        ceiling = _env_int("UNDERCOVER_LLM_MAX_TOKENS_CEILING", 8192, minimum=64)
        if ceiling < max_tokens:
            raise ValueError("UNDERCOVER_LLM_MAX_TOKENS_CEILING must be >= UNDERCOVER_LLM_MAX_TOKENS")
        attempts = _env_int("UNDERCOVER_LLM_MAX_ATTEMPTS", 3, minimum=1)
        llm_key = _optional_env("UNDERCOVER_LLM_API_KEY", "OPENCLAW_OPENAI_API_KEY")
        return cls(
            bcs_url=os.environ.get("UNDERCOVER_BCS_URL", "http://127.0.0.1:21000").strip(),
            ws_url=os.environ.get("UNDERCOVER_BCS_WS_URL", "").strip() or None,
            bot_name=os.environ.get("UNDERCOVER_BOT_NAME", DEFAULT_BOT_NAME).strip() or DEFAULT_BOT_NAME,
            token_file=_env_path(
                "UNDERCOVER_TOKEN_FILE",
                Path.home() / ".avernet" / "undercover-python-player" / "session.json",
            ),
            log_dir=_env_path(
                "UNDERCOVER_LOG_DIR",
                Path.home() / ".avernet" / "undercover-python-player" / "logs",
            ),
            llm_base_url=_optional_env("UNDERCOVER_LLM_BASE_URL", "OPENCLAW_OPENAI_BASE_URL"),
            llm_api_key=llm_key,
            llm_model=_optional_env("UNDERCOVER_LLM_MODEL", "OPENCLAW_OPENAI_MODEL_ID"),
            llm_timeout_seconds=llm_timeout,
            llm_max_tokens=max_tokens,
            llm_max_tokens_ceiling=ceiling,
            llm_max_attempts=attempts,
            rules_only=_env_flag("UNDERCOVER_RULES_ONLY"),
        )

    def with_overrides(self, **values: object) -> PlayerConfig:
        from dataclasses import replace

        return replace(self, **values)

    def websocket_url(self) -> str:
        """由 HTTP 地址推导 V3 Bot WebSocket 地址。"""

        if self.ws_url:
            return self.ws_url
        parts = urlsplit(self.bcs_url)
        scheme = "wss" if parts.scheme == "https" else "ws"
        return urlunsplit((scheme, parts.netloc, "/ws/bot", "", ""))

    def llm_enabled(self) -> bool:
        if self.rules_only:
            return False
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)
