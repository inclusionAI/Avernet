"""Unit tests for AliyunKmsSecretStorePlugin."""

from __future__ import annotations

from typing import Any

import pytest

from secbaas.community.bootstrap._configs import PluginConfig
from secbaas.community.plugins.secret.kms import (
    AliyunKmsClientFactory,
    AliyunKmsSecretStorePlugin,
    KmsSecretStoreConfig,
)


def _base_config(**overrides: object) -> KmsSecretStoreConfig:
    defaults: dict[str, object] = {
        "endpoint": "kms.cn-hangzhou.aliyuncs.com",
        "region_id": "cn-hangzhou",
        "access_key_id": "LTAI-test",
        "access_key_secret": "secret-test",
        "sm4_key_secret_name": "baas/chain/sm4",
        "proxypass_secret_name": "baas/proxypass",
    }
    defaults.update(overrides)
    return KmsSecretStoreConfig(**defaults)


class _FakeResponseBody:
    def __init__(self, secret_data: str | None) -> None:
        self.secret_data = secret_data


class _FakeResponse:
    def __init__(self, secret_data: str | None, use_none_body: bool = False) -> None:
        self.body = None if use_none_body else _FakeResponseBody(secret_data)


class _FakeClient:
    def __init__(
        self,
        values: dict[str, str],
        missing: set[str] | None = None,
        none_body: set[str] | None = None,
    ) -> None:
        self._values = values
        self._missing = missing or set()
        self._none_body = none_body or set()

    def get_secret_value(self, request: Any) -> _FakeResponse:
        name = request.secret_name
        if name in self._missing or name not in self._values:
            raise RuntimeError(f"secret {name} not found")
        return _FakeResponse(self._values[name], use_none_body=name in self._none_body)


class _FakeFactory:
    def __init__(self, client: _FakeClient) -> None:
        self._client = client

    def get_client(self) -> _FakeClient:
        return self._client


@pytest.fixture
def values() -> dict[str, str]:
    return {
        "baas/app/plain": "plain-value",
        "baas/app/kv": "user1:pass1",
        "baas/chain/sm4": "cmF3c20120120-ZmFrZQ==",
        "baas/proxypass": "proxypass-secret",
    }


def test_get_secret_returns_value(values: dict[str, str]) -> None:
    plugin = AliyunKmsSecretStorePlugin(
        _base_config(), client_factory=_FakeFactory(_FakeClient(values))
    )
    assert plugin.get_secret("baas/app/plain") == "plain-value"


def test_get_secret_missing_raises() -> None:
    plugin = AliyunKmsSecretStorePlugin(
        _base_config(), client_factory=_FakeFactory(_FakeClient({}))
    )
    with pytest.raises(RuntimeError, match="not found"):
        plugin.get_secret("baas/app/plain")


def test_get_kv_secret_returns_tuple(values: dict[str, str]) -> None:
    plugin = AliyunKmsSecretStorePlugin(
        _base_config(), client_factory=_FakeFactory(_FakeClient(values))
    )
    assert plugin.get_kv_secret("baas/app/kv") == ("user1", "pass1")


def test_get_kv_secret_malformed_raises(values: dict[str, str]) -> None:
    bad = dict(values)
    bad["baas/app/kv"] = "no-colon-here"
    plugin = AliyunKmsSecretStorePlugin(
        _base_config(), client_factory=_FakeFactory(_FakeClient(bad))
    )
    with pytest.raises(RuntimeError, match="malformed"):
        plugin.get_kv_secret("baas/app/kv")


def test_resolve_secret_resolves_at_prefix(values: dict[str, str]) -> None:
    plugin = AliyunKmsSecretStorePlugin(
        _base_config(), client_factory=_FakeFactory(_FakeClient(values))
    )
    assert plugin.resolve_secret("@baas/app/plain") == "plain-value"


def test_resolve_secret_pass_through_non_at(values: dict[str, str]) -> None:
    plugin = AliyunKmsSecretStorePlugin(
        _base_config(), client_factory=_FakeFactory(_FakeClient(values))
    )
    assert plugin.resolve_secret("plain_value") == "plain_value"


def test_resolve_secret_empty_string(values: dict[str, str]) -> None:
    plugin = AliyunKmsSecretStorePlugin(
        _base_config(), client_factory=_FakeFactory(_FakeClient(values))
    )
    assert plugin.resolve_secret("") == ""


def test_resolve_common_sm4_key(values: dict[str, str]) -> None:
    plugin = AliyunKmsSecretStorePlugin(
        _base_config(), client_factory=_FakeFactory(_FakeClient(values))
    )
    assert plugin.resolve_common_sm4_key() == values["baas/chain/sm4"]


def test_resolve_common_sm4_key_unconfigured_raises() -> None:
    plugin = AliyunKmsSecretStorePlugin(
        _base_config(sm4_key_secret_name=""),
        client_factory=_FakeFactory(_FakeClient({})),
    )
    with pytest.raises(RuntimeError, match="sm4_key_secret_name"):
        plugin.resolve_common_sm4_key()


def test_generate_proxy_token_non_empty(values: dict[str, str]) -> None:
    plugin = AliyunKmsSecretStorePlugin(
        _base_config(), client_factory=_FakeFactory(_FakeClient(values))
    )
    token = plugin.generate_proxy_token("ARCA_sandbox-123")
    assert isinstance(token, str)
    assert len(token.split(".")) == 3


def test_generate_proxy_token_missing_proxy_secret_raises() -> None:
    plugin = AliyunKmsSecretStorePlugin(
        _base_config(proxypass_secret_name=""),
        client_factory=_FakeFactory(_FakeClient({})),
    )
    with pytest.raises(RuntimeError, match="proxypass_secret_name"):
        plugin.generate_proxy_token("ARCA")


def test_invalid_config_missing_endpoint_raises() -> None:
    with pytest.raises(ValueError, match="endpoint"):
        AliyunKmsSecretStorePlugin(_base_config(endpoint=""))


def test_invalid_config_missing_credentials_raises() -> None:
    with pytest.raises(ValueError, match="access_key_id"):
        AliyunKmsSecretStorePlugin(_base_config(access_key_id=""))


def test_invalid_config_missing_region_raises() -> None:
    with pytest.raises(ValueError, match="region_id"):
        AliyunKmsSecretStorePlugin(_base_config(region_id=""))


def test_accepts_dict_config(values: dict[str, str]) -> None:
    plugin = AliyunKmsSecretStorePlugin(
        _base_config().model_dump(), client_factory=_FakeFactory(_FakeClient(values))
    )
    assert plugin.get_secret("baas/app/plain") == "plain-value"


def test_plugin_config_accepts_kms_option() -> None:
    ok = PluginConfig(secret="aliyun_kms")
    assert ok.secret == "aliyun_kms"


def test_plugin_config_rejects_unknown_option() -> None:
    with pytest.raises(Exception):
        PluginConfig(secret="nonexistent")


def test_plugin_config_defaults_to_stub() -> None:
    assert PluginConfig().secret == "stub"


# ── Additional branch coverage ─────────────────────────────────────────────


def test_secret_name_prefix_used(values: dict[str, str]) -> None:
    prefixed = {f"env/{k}": v for k, v in values.items()}
    plugin = AliyunKmsSecretStorePlugin(
        _base_config(secret_name_prefix="env/"),
        client_factory=_FakeFactory(_FakeClient(prefixed)),
    )
    assert plugin.get_secret("baas/app/plain") == prefixed["env/baas/app/plain"]


def test_get_kv_secret_with_prefix(values: dict[str, str]) -> None:
    prefixed = {f"env/{k}": v for k, v in values.items()}
    plugin = AliyunKmsSecretStorePlugin(
        _base_config(secret_name_prefix="env/"),
        client_factory=_FakeFactory(_FakeClient(prefixed)),
    )
    assert plugin.get_kv_secret("baas/app/kv") == ("user1", "pass1")


def test_get_secret_none_body_raises_not_found(values: dict[str, str]) -> None:
    plugin = AliyunKmsSecretStorePlugin(
        _base_config(),
        client_factory=_FakeFactory(_FakeClient(values, none_body={"baas/app/plain"})),
    )
    with pytest.raises(RuntimeError, match="not found"):
        plugin.get_secret("baas/app/plain")


def test_get_secret_empty_value_returns_empty() -> None:
    plugin = AliyunKmsSecretStorePlugin(
        _base_config(),
        client_factory=_FakeFactory(_FakeClient({"baas/app/plain": ""})),
    )
    assert plugin.get_secret("baas/app/plain") == ""


def test_generate_proxy_token_custom_ttl(values: dict[str, str]) -> None:
    plugin = AliyunKmsSecretStorePlugin(
        _base_config(), client_factory=_FakeFactory(_FakeClient(values))
    )
    token = plugin.generate_proxy_token("ARCA_test", ttl_seconds=60)
    parts = token.split(".")
    assert len(parts) == 3


def test_close_is_noop(values: dict[str, str]) -> None:
    plugin = AliyunKmsSecretStorePlugin(
        _base_config(), client_factory=_FakeFactory(_FakeClient(values))
    )
    plugin.close()


def test_default_factory_built_from_config() -> None:
    plugin = AliyunKmsSecretStorePlugin(_base_config())
    assert isinstance(plugin._client_factory, AliyunKmsClientFactory)


# ── Client factory coverage ────────────────────────────────────────────────


def test_kms_config_defaults() -> None:
    cfg = KmsSecretStoreConfig()
    assert cfg.endpoint == ""
    assert cfg.region_id == ""
    assert cfg.access_key_id == ""
    assert cfg.access_key_secret == ""
    assert cfg.secret_name_prefix == ""


def test_factory_missing_sdk_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(
        name: str,
        globals: dict[str, Any] | None = None,  # noqa: A002
        locals: dict[str, Any] | None = None,  # noqa: A002
        fromlist: tuple[str, ...] | None = None,
        level: int = 0,
    ) -> Any:
        if name.startswith("alibabacloud_kms20160120") or name.startswith(
            "alibabacloud_tea_openapi"
        ):
            raise ImportError(f"No module named {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    factory = AliyunKmsClientFactory(_base_config())
    with pytest.raises(RuntimeError, match="SDK"):
        factory.get_client()


def test_factory_caches_client() -> None:
    factory = AliyunKmsClientFactory(_base_config())
    factory._client = "cached-client"
    assert factory.get_client() == "cached-client"


def test_factory_builds_real_sdk_client() -> None:
    factory = AliyunKmsClientFactory(_base_config())
    client = factory.get_client()
    from alibabacloud_kms20160120.client import Client

    assert isinstance(client, Client)
    assert factory.get_client() is client
