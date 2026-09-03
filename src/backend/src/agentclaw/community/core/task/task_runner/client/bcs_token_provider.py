"""BCS HMAC 凭据(driver bot 签名取数)。real 从配置/double 注入。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class BcsTokenProvider(Protocol):
    @property
    def token(self) -> str: ...

    @property
    def secret(self) -> str: ...

    @property
    def base_url(self) -> str: ...

    @property
    def task_callback_url(self) -> str: ...  # 任务回投 origin(scheme://netloc);corp env-aware 注入,空→TaskExecutor 兜底 api_base_url

@dataclass(frozen=True)
class LocalBcsTokenProvider:
    """singlebox 本地 BCS 凭据:复用 BcsHttpAdapter 直连本地 BCS(:21000)。

    本地 BCS 与生产同 REST,``require_authentication=false`` → HMAC ``X-ECB-*`` 头被本地忽略,
    故 ``token``/``secret`` 仅占位(BcsHttpAdapter 仍会算 HMAC 并发送,本地不校验)。``base_url``
    由 ``SINGLEBOX_BCS_URL`` 注入(默认 http://localhost:21000)。满足
    ``BcsTokenProvider`` 契约。任务模式候选查询调用 BCS 全局内部 API。
    """

    base_url: str
    token: str = ""
    secret: str = ""
    task_callback_url: str = ""  # 任务回投 origin(scheme://netloc);singlebox 空 → TaskExecutor 兜底 api_base_url

    @classmethod
    def from_env(cls) -> "LocalBcsTokenProvider":
        return cls(
            base_url=os.environ.get("SINGLEBOX_BCS_URL", "http://localhost:21000"),
        )
