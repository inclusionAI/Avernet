"""Unit tests for AliyunKmsSecretResolver — Aliyun KMS-backed SecretResolver SPI.

Covers the SPI contract: existing secret → object with ``.secret_user`` /
``.secret_value``, absent secret → ``None``, transport errors propagate (never
swallowed), and missing/unresolvable credentials fail fast at construction.
"""

from __future__ import annotations

from typing import Any

import pytest

from gateway.community.plugins.secret_resolver.kms import (
    AliyunKmsClientFactory,
    AliyunKmsSecretResolver,
    KmsError,
    KmsGetSecretValueRequest,
    KmsSecretNotFoundError,
    KmsSecretResolverConfig,
)


def _base_config(**overrides: object) -> KmsSecretResolverConfig:
    defaults: dict[str, object] = {
        "endpoint": "kms.cn-hangzhou.aliyuncs.com",
        "region_id": "cn-hangzhou",
        "access_key_id": "LTAI-test",
        "access_key_secret": "secret-test",
    }
    defaults.update(overrides)
    return KmsSecretResolverConfig(**defaults)


class _FakeResponseBody:
    def __init__(self, secret_data: str | None) -> None:
        self.secret_data = secret_data


class _FakeResponse:
    def __init__(self, secret_data: str | None, use_none_body: bool = False) -> None:
        self.body = None if use_none_body else _FakeResponseBody(secret_data)


class _FakeSpy:
    """Records requested names while serving deterministic values."""

    def __init__(
        self,
        values: dict[str, str],
        not_found: set[str] | None = None,
        none_body: set[str] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._values = values
        self._not_found = not_found or set()
        self._none_body = none_body or set()
        self._error = error
        self.requests: list[str] = []

    def get_secret_value(self, request: Any) -> _FakeResponse:
        name = request.secret_name
        self.requests.append(name)
        if self._error is not None:
            raise self._error
        if name in self._not_found or name not in self._values:
            raise KmsSecretNotFoundError(f"secret {name} not found")
        if name in self._none_body:
            # A body whose ``secret_data`` is None (present but no secret data).
            return _FakeResponse(None, use_none_body=False)
        return _FakeResponse(self._values[name])


class _FakeFactory:
    def __init__(self, client: _FakeSpy) -> None:
        self._client = client

    def get_client(self) -> _FakeSpy:
        return self._client


@pytest.fixture
def values() -> dict[str, str]:
    return {"gateway/app/plain": "plain-value", "gateway/app/token": "tok-value"}


def test_get_secret_existing_returns_object(values: dict[str, str]) -> None:
    resolver = AliyunKmsSecretResolver(
        _base_config(), client_factory=_FakeFactory(_FakeSpy(values))
    )
    secret = resolver.get_secret("gateway/app/plain")
    assert secret is not None
    assert secret.secret_value == "plain-value"
    assert secret.secret_user == ""


def test_get_secret_missing_returns_none(values: dict[str, str]) -> None:
    resolver = AliyunKmsSecretResolver(
        _base_config(),
        client_factory=_FakeFactory(_FakeSpy(values, not_found={"nope"})),
    )
    assert resolver.get_secret("nope") is None


def test_get_secret_absent_value_returns_none() -> None:
    resolver = AliyunKmsSecretResolver(
        _base_config(),
        client_factory=_FakeFactory(_FakeSpy({}, not_found={"nope"})),
    )
    assert resolver.get_secret("nope") is None


def test_transport_error_propagates(values: dict[str, str]) -> None:
    resolver = AliyunKmsSecretResolver(
        _base_config(),
        client_factory=_FakeFactory(
            _FakeSpy(values, error=KmsError("upstream unavailable"))
        ),
    )
    with pytest.raises(KmsError, match="upstream unavailable"):
        resolver.get_secret("gateway/app/plain")


def test_http_error_propagates(values: dict[str, str]) -> None:
    import httpx

    resolver = AliyunKmsSecretResolver(
        _base_config(),
        client_factory=_FakeFactory(_FakeSpy(values, error=httpx.ConnectError("boom"))),
    )
    with pytest.raises(httpx.ConnectError):
        resolver.get_secret("gateway/app/plain")


def test_secret_name_prefix_used(values: dict[str, str]) -> None:
    prefixed = {f"env/{k}": v for k, v in values.items()}
    resolver = AliyunKmsSecretResolver(
        _base_config(secret_name_prefix="env/"),
        client_factory=_FakeFactory(_FakeSpy(prefixed)),
    )
    assert resolver.get_secret("gateway/app/plain") is not None  # resolves prefixed


class TestKmsConfigValidation:
    def test_missing_endpoint_raises(self) -> None:
        with pytest.raises(ValueError, match="endpoint"):
            AliyunKmsSecretResolver(_base_config(endpoint=""))

    def test_missing_region_raises(self) -> None:
        with pytest.raises(ValueError, match="region_id"):
            AliyunKmsSecretResolver(_base_config(region_id=""))

    def test_missing_access_key_id_raises(self) -> None:
        with pytest.raises(ValueError, match="access_key_id"):
            AliyunKmsSecretResolver(_base_config(access_key_id=""))

    def test_missing_access_key_secret_raises(self) -> None:
        with pytest.raises(ValueError, match="access_key_secret"):
            AliyunKmsSecretResolver(_base_config(access_key_secret=""))

    def test_accepts_dict_config(self, values: dict[str, str]) -> None:
        resolver = AliyunKmsSecretResolver(
            _base_config().model_dump(), client_factory=_FakeFactory(_FakeSpy(values))
        )
        assert resolver.get_secret("gateway/app/plain") is not None  # noqa


class TestKmsClientFactory:
    def test_builds_http_client(self) -> None:
        from gateway.community.plugins.secret_resolver.kms._client import KmsClient

        factory = AliyunKmsClientFactory(_base_config())
        client = factory.get_client()
        assert isinstance(client, KmsClient)
        assert factory.get_client() is client

    def test_factory_caches_client(self) -> None:
        factory = AliyunKmsClientFactory(_base_config())
        factory._client = "cached-client"
        assert factory.get_client() == "cached-client"


class TestKmsResolverDefaultFactory:
    def test_default_factory_built_from_config(self) -> None:
        from gateway.community.plugins.secret_resolver.kms import AliyunKmsClientFactory

        resolver = AliyunKmsSecretResolver(_base_config())
        assert isinstance(resolver._client_factory, AliyunKmsClientFactory)

    def test_none_body_secret_data_returns_none(self) -> None:
        # A response whose ``secret_data`` is None maps to the SPI "absent⇒None"
        # contract, distinct from a transport/not-found error.
        resolver = AliyunKmsSecretResolver(
            _base_config(),
            client_factory=_FakeFactory(
                _FakeSpy({"gateway/app/plain": "v"}, none_body={"gateway/app/plain"})
            ),
        )
        assert resolver.get_secret("gateway/app/plain") is None


class TestKmsClientTransport:
    """Covers the dependency-free HTTP Aliyun KMS client (task 3.2)."""

    def _client(self, **overrides: object):
        from gateway.community.plugins.secret_resolver.kms._client import KmsClient

        cfg = _base_config(**overrides)
        return KmsClient(
            access_key_id=cfg.access_key_id,
            access_key_secret=cfg.access_key_secret,
            endpoint=cfg.endpoint,
            region_id=cfg.region_id,
        )

    def test_factory_derives_endpoint_from_region(self) -> None:
        factory = AliyunKmsClientFactory(
            _base_config(endpoint="", region_id="cn-shanghai")
        )
        client = factory.get_client()
        assert client._endpoint == "kms.cn-shanghai.aliyuncs.com"

    def test_client_signs_rpc_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import httpx

        client = self._client()
        captured: dict[str, str] = {}

        def fake_get(url: str, *, params: dict[str, str], timeout: float) -> object:
            captured.update(params)
            return httpx.Response(200, json={"SecretData": "v"})

        monkeypatch.setattr("httpx.get", fake_get)
        client.get_secret_value(KmsGetSecretValueRequest(secret_name="s"))
        assert captured["Action"] == "GetSecretValue"
        assert captured["Format"] == "JSON"
        assert captured["SignatureMethod"] == "HMAC-SHA1"
        assert captured["SecretName"] == "s"
        assert captured["Version"] == "2016-01-20"
        assert captured["Signature"]

    def test_client_surfaces_error_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import httpx

        client = self._client()

        def fake_get(url: str, **kw: object) -> object:
            return httpx.Response(200, json={"Code": "Forbidden", "Message": "no"})

        monkeypatch.setattr("httpx.get", fake_get)
        with pytest.raises(KmsError, match="Forbidden"):
            client.get_secret_value(KmsGetSecretValueRequest(secret_name="s"))

    def test_client_not_found_code_raises_not_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import httpx

        client = self._client()

        def fake_get(url: str, **kw: object) -> object:
            return httpx.Response(
                200, json={"Code": "Forbidden.ResourceNotFound", "Message": "nope"}
            )

        monkeypatch.setattr("httpx.get", fake_get)
        with pytest.raises(KmsSecretNotFoundError):
            client.get_secret_value(KmsGetSecretValueRequest(secret_name="s"))

    def test_client_request_error_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import httpx

        client = self._client()

        def fake_get(url: str, **kw: object) -> object:
            raise httpx.ConnectError("boom")

        monkeypatch.setattr("httpx.get", fake_get)
        with pytest.raises(KmsError, match="request error"):
            client.get_secret_value(KmsGetSecretValueRequest(secret_name="s"))

    def test_client_non_200_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import httpx

        client = self._client()

        monkeypatch.setattr(
            "httpx.get", lambda url, **kw: httpx.Response(500, text="internal")
        )
        with pytest.raises(KmsError, match="500"):
            client.get_secret_value(KmsGetSecretValueRequest(secret_name="s"))

    def test_client_invalid_json_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import httpx

        client = self._client()

        monkeypatch.setattr(
            "httpx.get", lambda url, **kw: httpx.Response(200, text="<html>")
        )
        with pytest.raises(KmsError, match="invalid json"):
            client.get_secret_value(KmsGetSecretValueRequest(secret_name="s"))

    def test_client_non_object_response_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import httpx

        client = self._client()

        monkeypatch.setattr(
            "httpx.get", lambda url, **kw: httpx.Response(200, json=["a", "b"])
        )
        with pytest.raises(KmsError, match="not an object"):
            client.get_secret_value(KmsGetSecretValueRequest(secret_name="s"))

    def test_client_missing_secret_data_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import httpx

        client = self._client()

        monkeypatch.setattr(
            "httpx.get", lambda url, **kw: httpx.Response(200, json={"Code": "OK"})
        )
        with pytest.raises(KmsSecretNotFoundError):
            client.get_secret_value(KmsGetSecretValueRequest(secret_name="nope"))


class TestKmsResolverWithPrincipalSigner:
    """Task 3.9 — the principal-signer path resolves signing keys through the
    selected SecretResolver, including the Aliyun KMS flavor."""

    def _kms_resolver(self, value: str) -> AliyunKmsSecretResolver:
        client = _FakeSpy({"principal_signing_key": value})
        return AliyunKmsSecretResolver(
            _base_config(), client_factory=_FakeFactory(client)
        )

    def test_load_signer_config_reads_key_from_kms_resolver(self) -> None:
        from gateway.community.config import PrincipalSignerPluginConfig
        from gateway.community.plugins.principal_signer.bare import load_signer_config

        resolver = self._kms_resolver("a-shared-secret-of-at-least-32-bytes!!")
        cfg = load_signer_config(
            PrincipalSignerPluginConfig(secret_name="principal_signing_key"),
            resolver,
            strict=False,
        )
        assert cfg.signing_key == "a-shared-secret-of-at-least-32-bytes!!"

    def test_missing_kms_signing_key_yields_no_key(self) -> None:
        from gateway.community.config import PrincipalSignerPluginConfig
        from gateway.community.plugins.principal_signer.bare import load_signer_config

        resolver = AliyunKmsSecretResolver(
            _base_config(),
            client_factory=_FakeFactory(
                _FakeSpy({}, not_found={"principal_signing_key"})
            ),
        )
        cfg = load_signer_config(
            PrincipalSignerPluginConfig(secret_name="principal_signing_key"),
            resolver,
            strict=False,
        )
        assert cfg.signing_key == ""
