"""CommunityAgentComputePlatformModule wiring tests — config fail-clear at
binding time (the boot-time guarantee), per the task-runner-integration
wiring-test precedent: providers exercised directly, no full app container.
"""
from __future__ import annotations

import pytest

import agentclaw.community.di.modules.infrastructure.community.agentcompute_platform as wiring
from agentclaw.community.plugins.community.agentcompute_platform import (
    AgentComputePlatformConfig,
)
from agentclaw.community.plugins.community.agentcompute_platform import (
    AvernetBotPlatform,
)

VALID_BLOCK = {
    "engine_type": "openclaw",
    "cluster_name": "ACRA",
    "owner_work_no": "swarm-owner",
}


def _patch_block(monkeypatch, block):
    monkeypatch.setattr(wiring, "_block", lambda _name: block)


class TestConfigProvider:

    def test_missing_block_names_every_missing_field(self, monkeypatch):
        _patch_block(monkeypatch, None)
        with pytest.raises(ValueError) as err:
            wiring.CommunityAgentComputePlatformModule(
            ).agentcompute_platform_config()
        assert "engine_type" in str(err.value)
        assert "cluster_name" in str(err.value)
        assert "owner_work_no" in str(err.value)

    def test_partial_block_names_the_gaps(self, monkeypatch):
        _patch_block(monkeypatch, {"engine_type": "openclaw"})
        with pytest.raises(ValueError) as err:
            wiring.CommunityAgentComputePlatformModule(
            ).agentcompute_platform_config()
        assert "owner_work_no" in str(err.value)
        assert "engine_type" not in str(err.value)

    def test_valid_block_yields_the_config(self, monkeypatch):
        _patch_block(monkeypatch, dict(VALID_BLOCK, identity_file="RULES.md"))
        config = wiring.CommunityAgentComputePlatformModule(
        ).agentcompute_platform_config()
        assert isinstance(config, AgentComputePlatformConfig)
        assert config.engine_type == "openclaw"
        assert config.identity_file == "RULES.md"

    def test_public_surface_is_the_binding_contract(self):
        own = vars(wiring.CommunityAgentComputePlatformModule)
        providers = {name for name, value in own.items()
                     if callable(value) and not name.startswith("_")}
        assert providers == {
            "agentcompute_platform_config", "bot_platform",
        }
        # the adapter surface is the seam, not a new parallel protocol
        import agentclaw.community.core.agentcompute.spi.bot as spi
        assert issubclass(AvernetBotPlatform, spi.BotPlatform)
