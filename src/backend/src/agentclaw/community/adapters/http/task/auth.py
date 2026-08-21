"""回调鉴权端口:HMAC(默认,镜像 BCS 出站签名) + Noop(double/singlebox)。

签串 f"{timestamp}{method}{path}{body_sha256_hex}";头 X-TaskLoop-Token/Timestamp/Signature。
不 import ocb(开源边界);自包含 hashlib/hmac 实现。失败 raise CallbackAuthError(DomainError→401)。
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Mapping, Protocol, runtime_checkable

from agentclaw.community.core.errors import ValidationError

_TOKEN_HEADER = "X-TaskLoop-Token"
_TIMESTAMP_HEADER = "X-TaskLoop-Timestamp"
_SIGNATURE_HEADER = "X-TaskLoop-Signature"
_DEFAULT_MAX_SKEW_S = 300


@runtime_checkable
class CallbackAuthenticator(Protocol):
    def verify(
        self, *, source: str, headers: Mapping[str, str], raw_body: bytes,
        method: str, path: str,
    ) -> None: ...


class HmacCallbackAuthenticator:
    """HMAC-SHA256 签名校验,按 source 取共享密钥。"""

    def __init__(self, secrets: Mapping[str, str], *, max_skew_s: int = _DEFAULT_MAX_SKEW_S) -> None:
        self._secrets = dict(secrets)
        self._max_skew_s = max_skew_s

    def verify(self, *, source, headers, raw_body, method, path) -> None:
        secret = self._secrets.get(source)
        if secret is None:
            raise ValidationError(f"unknown callback source: {source}")
        ts = headers.get(_TIMESTAMP_HEADER)
        sig = headers.get(_SIGNATURE_HEADER)
        if not ts or not sig:
            raise ValidationError("missing timestamp/signature header")
        try:
            ts_int = int(ts)
        except ValueError:
            raise ValidationError("invalid timestamp")
        if abs(int(time.time()) - ts_int) > self._max_skew_s:
            raise ValidationError("stale timestamp")
        body_hex = hashlib.sha256(raw_body).hexdigest()
        sign_str = f"{ts}{method}{path}{body_hex}"
        expected = hmac.new(secret.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            raise ValidationError("signature mismatch")


class NoopCallbackAuthenticator:
    """singlebox/test 直通(进程内可信)。"""

    def verify(self, *, source, headers, raw_body, method, path) -> None:  # noqa: D401
        return None