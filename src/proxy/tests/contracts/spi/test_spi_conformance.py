"""SPI conformance tests — plugin impls satisfy their protocols."""

from __future__ import annotations

from sandboxproxy.community.config import UserConfig
from sandboxproxy.community.plugins.relay_client.baas import BaasRelayClient
from sandboxproxy.community.plugins.relay_client.stub import StubRelayClient
from sandboxproxy.community.plugins.resolver.arca import ArcaTargetResolver
from sandboxproxy.community.plugins.resolver.local import LocalTargetResolver
from sandboxproxy.community.plugins.resolver.prefix import PrefixTargetResolver
from sandboxproxy.community.plugins.resolver.stub import StubTargetResolver
from sandboxproxy.community.plugins.resolver.teclaw import TeclawTargetResolver
from sandboxproxy.community.spi import RelayApiClient, TargetResolver


class TestResolverSatisfiesProtocol:
    def test_prefix_resolver(self) -> None:
        assert isinstance(PrefixTargetResolver(UserConfig()), TargetResolver)

    def test_arca_resolver(self) -> None:
        assert isinstance(ArcaTargetResolver(UserConfig()), TargetResolver)

    def test_teclaw_resolver(self) -> None:
        assert isinstance(TeclawTargetResolver(UserConfig()), TargetResolver)

    def test_local_resolver(self) -> None:
        assert isinstance(LocalTargetResolver(UserConfig()), TargetResolver)

    def test_stub_resolver(self) -> None:
        assert isinstance(StubTargetResolver(), TargetResolver)


class TestRelayClientSatisfiesProtocol:
    def test_baas_client(self) -> None:
        assert isinstance(BaasRelayClient("http://x", instance="i"), RelayApiClient)

    def test_stub_client(self) -> None:
        assert isinstance(StubRelayClient(), RelayApiClient)
