"""CallerPrincipalSigner 单测。

签出的 token 为 HS256 短 TTL 的 principal，claims 取值全部来自
``CallerPrincipalConfig``（默认 ``iss="gateway"`` / ``aud="backend"``）、
必填 ``exp``/``iat``/``iss``、不带 ``principals``；密钥每次 mint 从
secret 插件现取（不缓存、不 strip、不做弱钥校验）。
"""

from __future__ import annotations

import time
import warnings

import jwt
import pytest

from secbaas.community.plugins.bot_service import (
    CallerPrincipalConfig,
    CallerPrincipalSigner,
)

SECRET_NAME = "other_manual_teamclawgw_principal_signing_key"
KEY = "unit-test-principal-signing-key-0123456789abcdef"  # 48 bytes ≥ RFC 7518 §3.2


class _FakeSecretStore:
    def __init__(self, secrets: dict[str, str] | None = None) -> None:
        self._secrets = secrets if secrets is not None else {SECRET_NAME: KEY}
        self.calls: list[str] = []

    def get_secret(self, secret_name: str) -> str:
        self.calls.append(secret_name)
        if secret_name not in self._secrets:
            raise RuntimeError(f"Secret not found: {secret_name}")
        return self._secrets[secret_name]


def _config(**overrides) -> CallerPrincipalConfig:
    defaults: dict = {}
    defaults.update(overrides)
    return CallerPrincipalConfig(**defaults)


def _decode(token: str, key: str = KEY) -> dict:
    """按对端 decode 契约解码：HS256、iss/aud 值校验、必填 claims。"""
    return jwt.decode(
        token,
        key,
        algorithms=["HS256"],
        issuer="gateway",
        audience="backend",
        options={"require": ["exp", "iat", "iss"]},
    )


class TestMint:
    def test_minted_token_passes_backend_decode_contract(self):
        signer = CallerPrincipalSigner(_FakeSecretStore(), _config())
        claims = _decode(signer.mint())
        assert "principals" not in claims

    def test_custom_issuer_and_audience_flow_from_config(self):
        """issuer/audience 是配置项：自定义值须原样进入 claims。"""
        signer = CallerPrincipalSigner(
            _FakeSecretStore(), _config(issuer="baas", audience="bcs")
        )
        claims = jwt.decode(
            signer.mint(),
            KEY,
            algorithms=["HS256"],
            issuer="baas",
            audience="bcs",
            options={"require": ["exp", "iat", "iss"]},
        )
        assert claims["iss"] == "baas"

    def test_exp_iat_within_configured_ttl(self):
        signer = CallerPrincipalSigner(_FakeSecretStore(), _config(ttl_seconds=90))
        before = int(time.time())
        claims = _decode(signer.mint())
        after = int(time.time())
        assert before <= claims["iat"] <= after
        assert claims["exp"] == claims["iat"] + 90

    def test_key_fetched_per_mint_without_caching(self):
        """无缓存：每次 mint 现取密钥（secret 轮换对下次 mint 即生效）。"""
        store = _FakeSecretStore()
        signer = CallerPrincipalSigner(store, _config())
        signer.mint()
        signer.mint()
        assert store.calls == [SECRET_NAME, SECRET_NAME]

    def test_key_used_verbatim_without_stripping(self):
        """密钥原样使用（不 strip）——两侧 secret 须存同一字节串。"""
        raw = f"  {KEY}\n"
        store = _FakeSecretStore({SECRET_NAME: raw})
        signer = CallerPrincipalSigner(store, _config())
        _decode(signer.mint(), key=raw)

    def test_weak_key_still_signs(self):
        """<32 字节的弱钥不拒绝（与 backend 语义一致：告警，不 fail）。"""
        weak = "short-key"
        store = _FakeSecretStore({SECRET_NAME: weak})
        signer = CallerPrincipalSigner(store, _config())
        with warnings.catch_warnings():
            # PyJWT 对短 HMAC 密钥可能发 UserWarning；本用例只验证签名行为。
            warnings.simplefilter("ignore")
            claims = _decode(signer.mint(), key=weak)
        assert claims["iss"] == "gateway"

    def test_custom_secret_name_is_used(self):
        store = _FakeSecretStore({"another-secret-name": KEY})
        signer = CallerPrincipalSigner(
            store, _config(secret_name="another-secret-name")
        )
        _decode(signer.mint())
        assert store.calls == ["another-secret-name"]


class TestSecretFailure:
    def test_missing_secret_propagates_runtime_error(self):
        """secret 缺失：RuntimeError 原样上抛（调用方自行决定失败语义）。"""
        signer = CallerPrincipalSigner(_FakeSecretStore({}), _config())
        with pytest.raises(RuntimeError):
            signer.mint()
