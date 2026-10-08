"""Tests for ``BotDeployConfig.teclaw_bot_config`` — the agentclaw side of the
teclaw config-artifact contract.

The composed ``BotConfigArtifact`` rides inside ``deploy_config`` (NOT a
create_bot top-level field, which secbaas would drop via ``extra="ignore"``).
secbaas reads it back as ``DeployConfig.teclaw_bot_config`` (wire field name
confirmed with the BaaS owner 2026-06-08) and forwards it to the external teclaw
container (non-mount delivery). ARCA/baas bots leave it ``None``.
"""
from __future__ import annotations

from agentclaw.community.core.service_bot.services.baas_service import (
    BotConfig,
    BotDeployConfig,
)


def test_teclaw_bot_config_absent_by_default() -> None:
    """Non-teclaw bots: no ``teclaw_bot_config`` key in the payload at all."""
    assert "teclaw_bot_config" not in BotDeployConfig().to_dict()


def test_teclaw_bot_config_emitted_when_set() -> None:
    """When set, the artifact is emitted verbatim under the wire JSON key."""
    artifact = {"schema_version": 2, "version": 7, "skills": [], "mcp": {}}
    result = BotDeployConfig(teclaw_bot_config=artifact).to_dict()
    assert result["teclaw_bot_config"] == artifact


def test_teclaw_bot_config_nests_under_deploy_config() -> None:
    """Via ``BotConfig.to_dict`` the artifact lands at
    ``deploy_config.teclaw_bot_config`` — exactly where secbaas reads it, not at
    the create_bot top level."""
    artifact = {"schema_version": 2, "identity_files": []}
    bot_config = BotConfig(
        entity_id="staff-1",
        entity_type="staff",
        deploy_config=BotDeployConfig(teclaw_bot_config=artifact),
    )
    payload = bot_config.to_dict()

    assert payload["deploy_config"]["teclaw_bot_config"] == artifact
    # Must NOT leak to the top level (secbaas CreateBotRequest drops unknowns).
    assert "teclaw_bot_config" not in payload


def test_teclaw_bot_config_coexists_with_other_deploy_fields() -> None:
    """Adding the artifact does not disturb the existing deploy_config keys."""
    artifact = {"schema_version": 2}
    result = BotDeployConfig(
        after_create_cmd_hook="echo hi",
        ttl_in_minutes=60,
        teclaw_bot_config=artifact,
    ).to_dict()

    assert result["teclaw_bot_config"] == artifact
    assert result["after_create_cmd_hook"] == "echo hi"
    assert result["ttl_in_minutes"] == 60
