"""Unit tests for target resolvers (prefix dispatch + per-prefix classes)."""

from __future__ import annotations

import pytest

from sandboxproxy.community.config import UserConfig
from sandboxproxy.community.plugins.resolver.arca import ArcaTargetResolver
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
    def test_default_port(self, resolver: PrefixTargetResolver) -> None:
        result = resolver.resolve("ARCA_12345")
        assert result["sandbox_id"] == "12345"
        assert result["sandbox_port"] == "8080"
        assert "arca_host" in result

    def test_explicit_port(self, resolver: PrefixTargetResolver) -> None:
        result = resolver.resolve("ARCA_12345:9090")
        assert result["sandbox_id"] == "12345"
        assert result["sandbox_port"] == "9090"

    def test_no_sandbox_id(self, resolver: PrefixTargetResolver) -> None:
        with pytest.raises(ValueError):
            resolver.resolve("ARCA_")

    def test_no_api_key_injected(self, resolver: PrefixTargetResolver) -> None:
        result = resolver.resolve("ARCA_12345")
        assert "x-agent-sandbox-api-key" not in result

    def test_missing_host(self) -> None:
        cfg = UserConfig.model_validate({"aliyun_ack_cluster": {}})
        resolver = PrefixTargetResolver(cfg)
        with pytest.raises(RuntimeError):
            resolver.resolve("ARCA_12345")


class TestTeclawResolution:
    def test_bot_id_extracted(self, resolver: PrefixTargetResolver) -> None:
        result = resolver.resolve("TECLAW_bot123@tmpl456:8080")
        assert result["x-target-bot-id"] == "bot123"
        assert result["teclaw_host"] == "http://teclaw.internal.example"

    def test_empty_remainder(self, resolver: PrefixTargetResolver) -> None:
        with pytest.raises(ValueError):
            resolver.resolve("TECLAW_")

    def test_missing_host(self) -> None:
        cfg = UserConfig.model_validate({"teclaw": {}})
        resolver = PrefixTargetResolver(cfg)
        with pytest.raises(RuntimeError):
            resolver.resolve("TECLAW_bot1@tmpl:8080")


class TestLocalResolution:
    def test_http_target(self, resolver: PrefixTargetResolver) -> None:
        result = resolver.resolve("LOCAL_dev1@42:20003")
        assert result["device_id"] == "dev1"
        assert result["template_id"] == "42"
        assert result["port"] == "20003"
        assert result["local_path_prefix"] == (
            "/api/v1/paas/devices/dev1@42/invoke-http/20003"
        )

    def test_ws_target_strips_session(self, resolver: PrefixTargetResolver) -> None:
        result = resolver.resolve("LOCAL_dev1@42:20003:sess-abc")
        assert result["port"] == "20003"
        assert result["local_path_prefix"] == (
            "/api/v1/paas/devices/dev1@42/invoke-http/20003"
        )

    def test_no_at_separator(self, resolver: PrefixTargetResolver) -> None:
        with pytest.raises(ValueError):
            resolver.resolve("LOCAL_dev1")

    def test_multiple_at(self, resolver: PrefixTargetResolver) -> None:
        with pytest.raises(ValueError):
            resolver.resolve("LOCAL_dev1@42@20003")

    def test_missing_host(self) -> None:
        cfg = UserConfig.model_validate({"baas": {}})
        resolver = PrefixTargetResolver(cfg)
        with pytest.raises(RuntimeError):
            resolver.resolve("LOCAL_dev1@42:20003")


class TestUnsupportedTarget:
    def test_unknown_prefix(self, resolver: PrefixTargetResolver) -> None:
        with pytest.raises(ValueError):
            resolver.resolve("FOO_123")


class TestIndividualResolvers:
    def test_arca_rejects_non_arca(self, config: UserConfig) -> None:
        with pytest.raises(ValueError):
            ArcaTargetResolver(config).resolve("TECLAW_bot@t:1")

    def test_teclaw_rejects_non_teclaw(self, config: UserConfig) -> None:
        with pytest.raises(ValueError):
            TeclawTargetResolver(config).resolve("ARCA_1")

    def test_local_rejects_non_local(self, config: UserConfig) -> None:
        with pytest.raises(ValueError):
            LocalTargetResolver(config).resolve("ARCA_1")

    def test_prefix_attributes(self) -> None:
        assert ArcaTargetResolver.prefix == "arca"
        assert TeclawTargetResolver.prefix == "teclaw"
        assert LocalTargetResolver.prefix == "local"
        assert PrefixTargetResolver.prefix == "prefix"


class TestStubResolver:
    def test_fixed_destination(self) -> None:
        resolver = StubTargetResolver()
        result = resolver.resolve("ARCA_anything")
        assert result["sandbox_id"] == "stub"
        assert "arca_host" in result
