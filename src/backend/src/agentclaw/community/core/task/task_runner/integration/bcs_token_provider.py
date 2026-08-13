"""BCS HMAC 凭据(driver bot 签名取数)。real 从配置/double 注入。"""
from __future__ import annotations
from typing import Protocol, runtime_checkable


@runtime_checkable
class BcsTokenProvider(Protocol):
    @property
    def token(self) -> str: ...
    @property
    def secret(self) -> str: ...
    @property
    def base_url(self) -> str: ...
