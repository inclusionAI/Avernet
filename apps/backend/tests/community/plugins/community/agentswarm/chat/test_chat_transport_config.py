"""The ``agentswarm.chat`` configuration block and its transport selection.

Two things are pinned here, and they are the whole point of the switch:

1. **The default is the engine channel.** An unconfigured deployment — or one
   that ships the block without a value — must behave exactly as before the
   seam existed. A silent switch to the new channel would move every bot's chat
   onto a different transport without anyone asking for it.

2. **The default allocates nothing.** Selecting the engine leg must not resolve
   a signing secret, build a transport, or touch the BaaS configuration: the
   provider returns ``None`` and the platform stays its own transport.
"""

from __future__ import annotations

import pytest

from agentclaw.community.core.agentswarm.config.settings import (
    AgentSwarmChatConfig,
    AgentSwarmConfig,
)
from agentclaw.community.di.modules.agentswarm import _build_chat_transport
from agentclaw.community.plugins.community.agentswarm.chat.base import (
    ChatTransport,
)


class _RecordingInjector:
    """Fails loudly if the engine branch resolves anything at all."""

    def __init__(self) -> None:
        self.requested: list[object] = []

    def get(self, key):
        self.requested.append(key)
        raise AssertionError(f"engine branch must not resolve {key!r}")


class TestDefaultIsTheEngineChannel:
    def test_bare_config_uses_engine_ws(self):
        assert AgentSwarmConfig.from_block({}).chat.transport == "engine_ws"

    def test_block_without_transport_uses_engine_ws(self):
        config = AgentSwarmConfig.from_block({"chat": {}})

        assert config.chat.transport == "engine_ws"

    def test_explicit_engine_ws_is_accepted(self):
        config = AgentSwarmConfig.from_block({"chat": {"transport": "engine_ws"}})

        assert config.chat.transport == "engine_ws"

    def test_transport_is_case_and_space_insensitive(self):
        config = AgentSwarmConfig.from_block(
            {"chat": {"transport": "  BAAS_GATEWAY  "}}
        )

        assert config.chat.transport == "baas_gateway"

    def test_unknown_transport_is_refused(self):
        with pytest.raises(ValueError, match="engine_ws"):
            AgentSwarmConfig.from_block({"chat": {"transport": "carrier_pigeon"}})


class TestEngineBranchAllocatesNothing:
    def test_default_builds_no_transport(self):
        injector = _RecordingInjector()

        assert _build_chat_transport(injector, AgentSwarmConfig.from_block({})) is None

    def test_engine_branch_resolves_no_dependency(self):
        injector = _RecordingInjector()

        _build_chat_transport(
            injector, AgentSwarmConfig.from_block({"chat": {"transport": "engine_ws"}})
        )

        assert injector.requested == []


class TestBaasBranch:
    def test_builds_a_chat_transport(self):
        from agentclaw.community.core.repository.protocols.bot import BotRepository
        from agentclaw.community.di.config import BaasConfig, SecretNamesConfig
        from agentclaw.community.plugin_api.secret_resolver import SecretResolver

        class _Secret:
            secret_value = "k" * 64

        class _Resolver:
            def get_secret(self, name):
                return _Secret()

        class _Injector:
            def __init__(self):
                self._bindings = {
                    SecretResolver: _Resolver(),
                    SecretNamesConfig: SecretNamesConfig(),
                    BaasConfig: BaasConfig(),
                    BotRepository: object(),
                }

            def get(self, key):
                return self._bindings[key]

        config = AgentSwarmConfig.from_block(
            {"chat": {"transport": "baas_gateway"}}
        )

        transport = _build_chat_transport(_Injector(), config)

        assert isinstance(transport, ChatTransport)
        assert (
            transport.chat_timeout_seconds
            == config.chat.baas_gateway.default_timeout_seconds
        )

    def test_custom_timeout_reaches_the_transport(self):
        from agentclaw.community.core.repository.protocols.bot import BotRepository
        from agentclaw.community.di.config import BaasConfig, SecretNamesConfig
        from agentclaw.community.plugin_api.secret_resolver import SecretResolver

        class _Secret:
            secret_value = "k" * 64

        class _Resolver:
            def get_secret(self, name):
                return _Secret()

        class _Injector:
            def __init__(self):
                self._bindings = {
                    SecretResolver: _Resolver(),
                    SecretNamesConfig: SecretNamesConfig(),
                    BaasConfig: BaasConfig(),
                    BotRepository: object(),
                }

            def get(self, key):
                return self._bindings[key]

        config = AgentSwarmConfig.from_block(
            {"chat": {"transport": "baas_gateway",
                      "baas_gateway": {"default_timeout_seconds": 7.5}}}
        )

        assert _build_chat_transport(_Injector(), config).chat_timeout_seconds == 7.5


class TestBaasBlockParsing:
    def test_defaults(self):
        block = AgentSwarmChatConfig.from_block({})
        baas = block.baas_gateway

        assert baas.resource_key_prefix == "agent_swarm_agent"
        assert baas.app_name == "agent_swarm"
        assert baas.issuer == "backend"
        assert baas.audience == "baas"
        assert baas.key_id == "bare"
        assert baas.default_timeout_seconds == 1800.0

    def test_overrides_are_read(self):
        block = AgentSwarmChatConfig.from_block(
            {
                "transport": "baas_gateway",
                "baas_gateway": {
                    "resource_key_prefix": "rk",
                    "app_name": "app-x",
                    "issuer": "iss",
                    "audience": "aud",
                    "key_id": "kid",
                    "default_timeout_seconds": 42.5,
                },
            }
        )

        baas = block.baas_gateway
        assert baas.resource_key_prefix == "rk"
        assert (baas.app_name, baas.issuer, baas.audience) == ("app-x", "iss", "aud")
        assert (baas.key_id, baas.default_timeout_seconds) == ("kid", 42.5)