"""
ECB Settings

ECB（Enterprise Context Broker）服务的配置。

从环境变量读取配置，YAML 配置会通过 main.py 的 inject_config_to_env() 注入。

环境变量：
- ECB_ENABLED: 是否启用 ECB 功能（默认 false）
- ECB_BASE_URL: ECB API 地址
- ECB_TIMEOUT_MS: 请求超时毫秒数（默认 15000）
"""

from __future__ import annotations

import os
from typing import Optional

from pydantic import BaseModel, Field


class ECBSettings(BaseModel):
    """
    ECB 服务配置

    从环境变量读取配置，YAML 配置会通过 main.py 注入到环境变量。

    Attributes:
        enabled: 是否启用 ECB 功能
        base_url: ECB API 基础地址
        timeout_ms: 请求超时（毫秒）
    """

    enabled: bool = Field(
        default=False,
        description="是否启用 ECB 功能",
    )
    base_url: Optional[str] = Field(
        default=None,
        description="ECB API 基础地址",
    )
    timeout_ms: int = Field(
        default=15000,
        description="请求超时（毫秒）",
    )

    def __init__(self, **data) -> None:
        env_data = self._load_from_env()
        merged = {**env_data, **data}
        super().__init__(**merged)

    @staticmethod
    def _load_from_env() -> dict:
        """从环境变量加载配置（YAML 配置会通过 main.py 注入到 env）"""
        result: dict = {}
        if os.environ.get("ECB_ENABLED"):
            result["enabled"] = os.environ["ECB_ENABLED"].lower() in ("true", "1", "yes")
        if os.environ.get("ECB_BASE_URL"):
            result["base_url"] = os.environ["ECB_BASE_URL"].strip()
        if os.environ.get("ECB_TIMEOUT_MS"):
            try:
                result["timeout_ms"] = int(os.environ["ECB_TIMEOUT_MS"])
            except ValueError:
                pass
        return result

    @property
    def is_configured(self) -> bool:
        """配置是否完整（启用且配置了 URL）"""
        return self.enabled and self.base_url is not None and len(self.base_url) > 0


__all__ = ["ECBSettings"]