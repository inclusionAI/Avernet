"""Mint the ``X-Avernet-Principal`` JWT for app-caller-connection requests.

Backend 的 decode 契约规定这枚 token 的形状：HS256 共享 HMAC 密钥、必填 claims
``exp``/``iat``/``iss``。本模块是该契约的 encode 侧：BaaS 持有同一把共享密钥
（经 ``SecretStorePlugin`` 取出），claims 的取值（issuer / audience / TTL）
全部来自 ``CallerPrincipalConfig`` 配置，为每次请求现签短 TTL 的 principal
——token 不再由调用方传入。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import jwt

from secbaas.community.logger import get_logger
from secbaas.community.spi.secret import SecretStorePlugin

logger = get_logger("plugin-bot-service")

# 与 backend verifier 对称的 pin：签什么算法由契约决定，不由 token 决定。
_ALGORITHM = "HS256"


@dataclass(frozen=True)
class CallerPrincipalConfig:
    """签发 principal 的部署事实（密钥本身除外，密钥走 secret 插件）。

    ``secret_name`` 是共享密钥在 secret 插件里的名字；``issuer`` / ``audience``
    是对端 decode 值校验的两个契约值（改任一侧须同步另一侧，否则 401）。
    """

    secret_name: str = "other_manual_teamclawgw_principal_signing_key"
    issuer: str = "gateway"
    audience: str = "backend"
    ttl_seconds: int = 60


class CallerPrincipalSigner:
    """app principal JWT 签发器（real 插件自用，非 SPI）。"""

    def __init__(
        self,
        secret_store: SecretStorePlugin,
        config: CallerPrincipalConfig,
    ) -> None:
        self._secret_store = secret_store
        self._config = config
        self._key: str | None = None

    def mint(self) -> str:
        """现签一枚新的 app principal token。

        每次调用现签（调用方为每次 HTTP 尝试取新 token）：短 TTL 覆盖单次
        请求绰绰有余，且轮询跨越 TTL 边界时天然拿到新 token——对应契约里
        "JWT 到期时获取新的 Principal"。
        """
        key = self._resolve_key()
        cfg = self._config
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": cfg.issuer,
            "aud": cfg.audience,
            "iat": now,
            "exp": now + cfg.ttl_seconds,
        }
        return jwt.encode(claims, key, algorithm=_ALGORITHM)

    def _resolve_key(self) -> str:
        return self._secret_store.get_secret(self._config.secret_name)
