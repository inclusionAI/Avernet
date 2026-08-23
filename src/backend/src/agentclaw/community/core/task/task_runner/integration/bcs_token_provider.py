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
    def provider_id(self) -> str: ...

    @property
    def provider_admin_token(self) -> str: ...


@dataclass
class LocalBcsTokenProvider:
    """singlebox 本地 BCS 凭据:复用 BcsHttpAdapter 直连本地 BCS(:21000)。

    本地 BCS 与生产同 REST,``require_authentication=false`` → HMAC ``X-ECB-*`` 头被本地忽略,
    故 ``token``/``secret`` 仅占位(BcsHttpAdapter 仍会算 HMAC 并发送,本地不校验)。``base_url``
    由 ``SINGLEBOX_BCS_URL`` 注入(默认 http://localhost:21000)。``provider_id``/``provider_admin_token``
    用于 provider 圈定任务模式 roster(``GET /providers/{provider_id}/bots/by-task-modes``),由
    ``SINGLEBOX_BCS_PROVIDER_ID``/``SINGLEBOX_BCS_PROVIDER_ADMIN_TOKEN`` 注入。满足
    ``BcsTokenProvider`` 契约。
    """

    base_url: str
    token: str = ""
    secret: str = ""
    provider_id: str = ""
    provider_admin_token: str = ""
    provider_name: str = "singlebox-task-bbs"
    provider_owner_id: str = "146836"
    provider_webhook_url: str = "https://example.com/singlebox-task-provider-webhook"
    provider_state_file: str = ""

    @classmethod
    def from_env(cls) -> "LocalBcsTokenProvider":
        return cls(
            base_url=os.environ.get("SINGLEBOX_BCS_URL", "http://localhost:21000"),
            provider_id=os.environ.get("SINGLEBOX_BCS_PROVIDER_ID", ""),
            provider_admin_token=os.environ.get("SINGLEBOX_BCS_PROVIDER_ADMIN_TOKEN", ""),
            provider_name=os.environ.get("SINGLEBOX_BCS_PROVIDER_NAME", "singlebox-task-bbs"),
            provider_owner_id=os.environ.get("SINGLEBOX_USER_ID", "146836"),
            provider_webhook_url=os.environ.get(
                "SINGLEBOX_BCS_PROVIDER_WEBHOOK_URL",
                "https://example.com/singlebox-task-provider-webhook",
            ),
            provider_state_file=os.environ.get(
                "SINGLEBOX_BCS_PROVIDER_STATE_FILE",
                "/tmp/agentclaw-singlebox-task-provider.json",
            ),
        )
