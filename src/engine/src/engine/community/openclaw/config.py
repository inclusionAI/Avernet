"""
OpenClaw Gateway 配置

配置加载优先级（从高到低）：
1. 环境变量 (OPENCLAW_GATEWAY_URL, OPENCLAW_GATEWAY_TOKEN 等)
2. 默认值
"""

import os
from dataclasses import dataclass
from typing import Optional

from engine.community.config import DEFAULT_CONNECTION_TIMEOUT


def _default_gateway_token() -> str:
    """Gateway auth token default: env > internal_defaults > empty.

    The internal/local-dev token lives in ``engine.community.internal_defaults``, which is
    excluded from the public GitHub export. In an open-source checkout that module
    is absent, so this returns "" (no secret ships) — community deployments set
    ``OPENCLAW_GATEWAY_TOKEN`` themselves or run a tokenless local gateway.
    """
    env = os.getenv("OPENCLAW_GATEWAY_TOKEN")
    if env:
        return env
    try:
        from engine import internal_defaults
    except ImportError:
        return ""
    return (getattr(internal_defaults, "OPENCLAW_GATEWAY_TOKEN", "") or "").strip()


@dataclass
class OpenClawConfig:
    """OpenClaw Gateway 配置"""

    gateway_url: str = "ws://127.0.0.1:18789"
    # Cloud envs auto-generate OPENCLAW_GATEWAY_TOKEN (stable across restarts).
    # Resolution: env > internal_defaults (local dev, export-excluded) > "".
    # No secret is hardcoded in this exported file.
    gateway_token: str = _default_gateway_token()
    connection_timeout: int = DEFAULT_CONNECTION_TIMEOUT
    ws_pool_size: int = 4

    @classmethod
    def from_env(cls) -> "OpenClawConfig":
        """从环境变量加载配置"""
        return cls(
            gateway_url=os.getenv("OPENCLAW_GATEWAY_URL", cls.gateway_url),
            gateway_token=os.getenv("OPENCLAW_GATEWAY_TOKEN", cls.gateway_token),
            connection_timeout=int(
                os.getenv("OPENCLAW_CONNECTION_TIMEOUT", str(DEFAULT_CONNECTION_TIMEOUT))
            ),
            ws_pool_size=int(os.getenv("OPENCLAW_WS_POOL_SIZE", "4")),
        )


# 全局配置实例
_config: Optional[OpenClawConfig] = None


def get_config() -> OpenClawConfig:
    """获取全局配置实例"""
    global _config
    if _config is None:
        _config = OpenClawConfig.from_env()
    return _config


def set_config(config: OpenClawConfig) -> None:
    """设置全局配置实例"""
    global _config
    _config = config


def reset_config() -> None:
    """重置全局配置（重新从环境变量加载）"""
    global _config
    _config = None
