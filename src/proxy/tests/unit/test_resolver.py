"""Unit tests for target resolvers (prefix dispatch + per-prefix classes)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from sandboxproxy.community.config import UserConfig
from sandboxproxy.community.plugins.resolver.arca import ArcaTargetResolver
from sandboxproxy.community.plugins.resolver.arca._resolver import _parse_rest
from sandboxproxy.community.plugins.resolver.local import LocalTargetResolver
from sandboxproxy.community.plugins.resolver.prefix import PrefixTargetResolver
from sandboxproxy.community.plugins.resolver.stub import StubTargetResolver
from sandboxproxy.community.plugins.resolver.teclaw import TeclawTargetResolver


@pytest.fixture
def config() -> UserConfig:
    return UserConfig.model_validate(
        {
            "aliyun_ack_cluster": {
                "api_server": "https://ack.internal.example",
                "token": "tok",
                "namespace": "default",
                "api_keys": {"0": "key-zero", "1": "key-one"},
            },
            "teclaw": {"host": "http://teclaw.internal.example"},
            "baas": {"host": "http://baas.internal.example"},
            "plugins": {"resolver": "prefix", "relay_client": "baas"},
        }
    )


@pytest.fixture
def resolver(config: UserConfig) -> PrefixTargetResolver:
    return PrefixTargetResolver(config)


class TestArcaResolution:
    def _resolver(self, config: UserConfig | None = None) -> PrefixTargetResolver:
        cfg = config or UserConfig.model_validate(
            {"baas": {"host": "http://baas.internal.example"}}
        )
        return PrefixTargetResolver(cfg)

    def _patch_lookup(
        self, monkeypatch: pytest.MonkeyPatch, ip_addr: str = "10.0.0.7"
    ) -> None:
        async def _lookup(
            self: ArcaTargetResolver,
            baas_host: str,
            props_path: str,
            provider_device_id: str,
        ) -> str:
            return ip_addr

        monkeypatch.setattr(ArcaTargetResolver, "_lookup_ip", _lookup)

    async def test_default_port(
        self, resolver: PrefixTargetResolver, monkeypatch
    ) -> None:
        self._patch_lookup(monkeypatch)
        result = await resolver.resolve("ARCA_ALIYUN_ACK_DEFAULT-abc@0")
        assert result["provider_device_id"] == "ALIYUN_ACK_DEFAULT-abc@0"
        assert result["pod_port"] == "8080"
        assert result["pod_ip"] == "10.0.0.7"

    async def test_explicit_port(
        self, resolver: PrefixTargetResolver, monkeypatch
    ) -> None:
        self._patch_lookup(monkeypatch)
        result = await resolver.resolve("ARCA_ALIYUN_ACK_DEFAULT-abc@0:9090")
        assert result["provider_device_id"] == "ALIYUN_ACK_DEFAULT-abc@0"
        assert result["pod_port"] == "9090"

    async def test_no_provider_device_id(self, resolver: PrefixTargetResolver) -> None:
        with pytest.raises(ValueError):
            await resolver.resolve("ARCA_")

    async def test_empty_port(self, resolver: PrefixTargetResolver) -> None:
        with pytest.raises(ValueError):
            await resolver.resolve("ARCA_ALIYUN_ACK_DEFAULT-abc@0:")

    def test_parse_rest_empty(self) -> None:
        with pytest.raises(ValueError):
            _parse_rest("")

    async def test_no_ip_addr(
        self, resolver: PrefixTargetResolver, monkeypatch
    ) -> None:
        self._patch_lookup(monkeypatch, ip_addr="")
        with pytest.raises(RuntimeError, match="no ip_addr"):
            await resolver.resolve("ARCA_ALIYUN_ACK_DEFAULT-abc@0")

    async def test_missing_baas_host(self, monkeypatch) -> None:
        cfg = UserConfig.model_validate({})
        resolver = PrefixTargetResolver(cfg)
        with pytest.raises(RuntimeError):
            await resolver.resolve("ARCA_12345")


class TestArcaLookupIp:
    """Exercise the real ``_lookup_ip`` HTTP path via ``httpx.MockTransport``."""

    _PROPS_PATH = "/api/v1/devices/provider-device/{provider_device_id}/props"

    def _make_resolver(self, handler: Any) -> ArcaTargetResolver:
        import httpx

        transport = httpx.MockTransport(handler)
        config = UserConfig.model_validate(
            {"baas": {"host": "http://baas.internal.example"}}
        )
        resolver = ArcaTargetResolver(config)
        resolver._client = httpx.AsyncClient(transport=transport)  # noqa: SLF001
        return resolver

    async def _lookup(self, body: Any = None, status_code: int = 200) -> str:
        import httpx

        def handler(request):
            return httpx.Response(status_code, json=body)

        resolver = self._make_resolver(handler)
        try:
            return await resolver._lookup_ip(  # noqa: SLF001
                "http://baas.internal.example",
                self._PROPS_PATH,
                "ALIYUN_ACK_DEFAULT-abc@1",
            )
        finally:
            await resolver.shutdown()

    async def test_lookup_success(self) -> None:
        ip_addr = await self._lookup(
            {"data": {"provider_device_props": {"metadata": {"ip_addr": "10.1.2.3"}}}}
        )
        assert ip_addr == "10.1.2.3"

    async def test_lookup_no_ip_addr(self) -> None:
        assert (
            await self._lookup({"data": {"provider_device_props": {"metadata": {}}}})
            == ""
        )

    async def test_lookup_missing_metadata(self) -> None:
        assert await self._lookup({"data": {"provider_device_props": {}}}) == ""

    async def test_lookup_404(self) -> None:
        assert await self._lookup({}, status_code=404) == ""

    async def test_lookup_500(self) -> None:
        assert await self._lookup({}, status_code=500) == ""

    async def test_lookup_malformed_response(self) -> None:
        import httpx

        def handler(request):
            return httpx.Response(200, content=b"not-json")

        resolver = self._make_resolver(handler)
        try:
            ip_addr = await resolver._lookup_ip(  # noqa: SLF001
                "http://baas.internal.example",
                self._PROPS_PATH,
                "ALIYUN_ACK_DEFAULT-abc@1",
            )
        finally:
            await resolver.shutdown()
        assert ip_addr == ""

    async def test_lookup_non_string_ip_addr(self) -> None:
        assert (
            await self._lookup(
                {"data": {"provider_device_props": {"metadata": {"ip_addr": 123}}}}
            )
            == ""
        )

    async def test_lookup_network_error(self) -> None:
        import httpx

        def handler(request):
            raise httpx.ConnectError("boom")

        resolver = self._make_resolver(handler)
        try:
            with pytest.raises(RuntimeError, match="lookup failed"):
                await resolver._lookup_ip(  # noqa: SLF001
                    "http://baas.internal.example",
                    self._PROPS_PATH,
                    "ALIYUN_ACK_DEFAULT-abc@1",
                )
        finally:
            await resolver.shutdown()

    async def test_default_props_path_used(self) -> None:
        import httpx

        seen_urls: dict[str, object] = {}

        def handler(request):
            seen_urls["url"] = str(request.url)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "provider_device_props": {"metadata": {"ip_addr": "10.9.9.9"}}
                    }
                },
            )

        resolver = self._make_resolver(handler)
        try:
            result = await resolver.resolve("ARCA_ALIYUN_ACK_DEFAULT-abc@1")
        finally:
            await resolver.shutdown()
        assert result["pod_ip"] == "10.9.9.9"
        assert seen_urls["url"] == (
            "http://baas.internal.example/api/v1/devices/"
            "provider-device/ALIYUN_ACK_DEFAULT-abc@1/props"
        )


class TestArcaResolveViaTransport:
    """Drive ``resolve()`` end to end with a mocked ``httpx.AsyncClient``."""

    def _resolver(
        self, body: Any = None, status_code: int = 200
    ) -> PrefixTargetResolver:
        import httpx

        def handler(request):
            return httpx.Response(status_code, json=body)

        transport = httpx.MockTransport(handler)
        config = UserConfig.model_validate(
            {"baas": {"host": "http://baas.internal.example"}}
        )
        resolver = PrefixTargetResolver(config)
        arca = resolver._resolvers["ARCA_"]  # noqa: SLF001
        arca._client = httpx.AsyncClient(transport=transport)  # noqa: SLF001
        return resolver

    async def test_resolve_returns_ip_and_port(self) -> None:
        resolver = self._resolver(
            {"data": {"provider_device_props": {"metadata": {"ip_addr": "10.2.3.4"}}}},
        )
        result = await resolver.resolve("ARCA_ALIYUN_ACK_DEFAULT-abc@1:20003")
        assert result["pod_ip"] == "10.2.3.4"
        assert result["pod_port"] == "20003"
        assert result["provider_device_id"] == "ALIYUN_ACK_DEFAULT-abc@1"
        assert "sandbox_id" not in result

    async def test_resolve_404_raises(self) -> None:
        resolver = self._resolver({}, status_code=404)
        with pytest.raises(RuntimeError, match="no ip_addr"):
            await resolver.resolve("ARCA_ALIYUN_ACK_DEFAULT-abc@1:20003")

    async def test_resolve_caches_ip(self) -> None:
        import httpx

        calls: list[str] = []

        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(
                200,
                json={
                    "data": {
                        "provider_device_props": {"metadata": {"ip_addr": "10.3.3.3"}}
                    }
                },
            )

        transport = httpx.MockTransport(handler)
        config = UserConfig.model_validate(
            {"baas": {"host": "http://baas.internal.example"}}
        )
        resolver = PrefixTargetResolver(config)
        arca = resolver._resolvers["ARCA_"]  # noqa: SLF001
        arca._client = httpx.AsyncClient(transport=transport)  # noqa: SLF001

        await resolver.resolve("ARCA_ALIYUN_ACK_DEFAULT-abc@1:20003")
        await resolver.resolve("ARCA_ALIYUN_ACK_DEFAULT-abc@1:20003")
        assert len(calls) == 1

    async def test_resolve_refetches_after_ttl_expiry(self) -> None:
        import httpx

        calls: list[str] = []

        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(
                200,
                json={
                    "data": {
                        "provider_device_props": {"metadata": {"ip_addr": "10.4.4.4"}}
                    }
                },
            )

        transport = httpx.MockTransport(handler)
        config = UserConfig.model_validate(
            {"baas": {"host": "http://baas.internal.example"}}
        )
        resolver = PrefixTargetResolver(config)
        arca = resolver._resolvers["ARCA_"]  # noqa: SLF001
        arca._cache_ttl = -1.0
        arca._client = httpx.AsyncClient(transport=transport)  # noqa: SLF001

        await resolver.resolve("ARCA_ALIYUN_ACK_DEFAULT-abc@1:20003")
        await resolver.resolve("ARCA_ALIYUN_ACK_DEFAULT-abc@1:20003")
        assert len(calls) == 2

    async def test_resolve_lazily_starts_client(self, monkeypatch) -> None:
        import httpx

        def handler(request):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "provider_device_props": {"metadata": {"ip_addr": "10.5.5.5"}}
                    }
                },
            )

        transport = httpx.MockTransport(handler)
        config = UserConfig.model_validate(
            {"baas": {"host": "http://baas.internal.example"}}
        )
        resolver = PrefixTargetResolver(config)
        arca = resolver._resolvers["ARCA_"]  # noqa: SLF001

        async def fake_start(self):
            self._client = httpx.AsyncClient(transport=transport)  # noqa: SLF001

        monkeypatch.setattr(ArcaTargetResolver, "start", fake_start)
        try:
            result = await resolver.resolve("ARCA_ALIYUN_ACK_DEFAULT-abc@1:20003")
        finally:
            await arca.shutdown()

        assert result["pod_ip"] == "10.5.5.5"


class TestTeclawResolution:
    def test_bot_id_extracted(self, resolver: PrefixTargetResolver) -> None:
        result = asyncio.run(resolver.resolve("TECLAW_bot123@tmpl456:8080"))
        assert result["x-target-bot-id"] == "bot123"
        assert result["teclaw_host"] == "http://teclaw.internal.example"

    def test_empty_remainder(self, resolver: PrefixTargetResolver) -> None:
        with pytest.raises(ValueError):
            asyncio.run(resolver.resolve("TECLAW_"))

    def test_missing_host(self) -> None:
        cfg = UserConfig.model_validate({"teclaw": {}})
        resolver = PrefixTargetResolver(cfg)
        with pytest.raises(RuntimeError):
            asyncio.run(resolver.resolve("TECLAW_bot1@tmpl:8080"))


class TestLocalResolution:
    def test_http_target(self, resolver: PrefixTargetResolver) -> None:
        result = asyncio.run(resolver.resolve("LOCAL_dev1@42:20003"))
        assert result["device_id"] == "dev1"
        assert result["template_id"] == "42"
        assert result["port"] == "20003"
        assert result["local_path_prefix"] == (
            "/api/v1/paas/devices/dev1@42/invoke-http/20003"
        )

    def test_ws_target_strips_session(self, resolver: PrefixTargetResolver) -> None:
        result = asyncio.run(resolver.resolve("LOCAL_dev1@42:20003:sess-abc"))
        assert result["port"] == "20003"
        assert result["local_path_prefix"] == (
            "/api/v1/paas/devices/dev1@42/invoke-http/20003"
        )

    def test_no_at_separator(self, resolver: PrefixTargetResolver) -> None:
        with pytest.raises(ValueError):
            asyncio.run(resolver.resolve("LOCAL_dev1"))

    def test_multiple_at(self, resolver: PrefixTargetResolver) -> None:
        with pytest.raises(ValueError):
            asyncio.run(resolver.resolve("LOCAL_dev1@42@20003"))

    def test_missing_host(self) -> None:
        cfg = UserConfig.model_validate({"baas": {}})
        resolver = PrefixTargetResolver(cfg)
        with pytest.raises(RuntimeError):
            asyncio.run(resolver.resolve("LOCAL_dev1@42:20003"))


class TestUnsupportedTarget:
    def test_unknown_prefix(self, resolver: PrefixTargetResolver) -> None:
        with pytest.raises(ValueError):
            asyncio.run(resolver.resolve("FOO_123"))


class TestIndividualResolvers:
    def test_arca_rejects_non_arca(self, config: UserConfig) -> None:
        with pytest.raises(ValueError):
            asyncio.run(ArcaTargetResolver(config).resolve("TECLAW_bot@t:1"))

    def test_teclaw_rejects_non_teclaw(self, config: UserConfig) -> None:
        with pytest.raises(ValueError):
            asyncio.run(TeclawTargetResolver(config).resolve("ARCA_1"))

    def test_local_rejects_non_local(self, config: UserConfig) -> None:
        with pytest.raises(ValueError):
            asyncio.run(LocalTargetResolver(config).resolve("ARCA_1"))

    def test_prefix_attributes(self) -> None:
        assert ArcaTargetResolver.prefix == "arca"
        assert TeclawTargetResolver.prefix == "teclaw"
        assert LocalTargetResolver.prefix == "local"
        assert PrefixTargetResolver.prefix == "prefix"


class TestStubResolver:
    def test_fixed_destination(self) -> None:
        resolver = StubTargetResolver()
        result = asyncio.run(resolver.resolve("ARCA_anything"))
        assert result["sandbox_id"] == "stub"
        assert "arca_host" in result
