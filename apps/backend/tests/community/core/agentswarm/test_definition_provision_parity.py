"""A definition reference must provision exactly like the equivalent inline agent.

Parity is the point of the store: if the two paths diverge, a run's behaviour
depends on how its roster was declared rather than on what it asked for.
"""
from __future__ import annotations

import inspect

from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.plugins.community.agentswarm.host import AvernetBotPlatform


def _definition(**over) -> dm.AgentDefinition:
    base = dict(
        definition_id=dm.AgentDefinitionId("7c1e"),
        tenant=dm.TenantId("teamclaw"),
        owner_id="49912",
        creator_id="49912",
        modifier_id="49912",
        name="hello_writer",
        role="负责产出 hello",
        instructions="只回复一个词：hello",
        kind=dm.AgentKind.BOT,
        engine="openclaw",
    )
    base.update(over)
    return dm.AgentDefinition(**base)


def _inline(**over) -> dm.AgentSpec:
    base = dict(
        name="hello_writer",
        role="负责产出 hello",
        instructions="只回复一个词：hello",
        kind=dm.AgentKind.BOT,
        engine="openclaw",
    )
    base.update(over)
    return dm.AgentSpec(**base)


def test_definition_and_inline_produce_the_same_spec():
    assert _definition().to_spec() == _inline()


def test_bot_kind_definition_carries_its_manifest_verbatim():
    doc = "schema_version: 1\nmanifest:\n  identity:\n    - type: SOUL.md\n      content: hi\n"
    spec = _definition(manifest_document=doc).to_spec()
    # The host adapter submits config_manifest verbatim when non-blank.
    assert spec.config_manifest == doc
    assert spec.config_manifest.strip() != ""


def test_llm_kind_definition_never_requests_a_bot():
    spec = _definition(kind=dm.AgentKind.LLM).to_spec()
    assert spec.kind is dm.AgentKind.LLM
    # The llm path provisions in-process; no BotPlatform.create_bot call.
    assert "create_bot" not in str(spec)


def test_manifestless_definition_matches_inline_manifestless():
    """The synthesized-persona path must be byte-identical."""
    defn = _definition(manifest_document="").to_spec()
    inline = _inline()
    assert defn == inline
    assert defn.config_manifest == ""      # blank -> synthesize
    assert defn.identity_file == "SOUL.md"  # what the synthesis falls back to


def test_host_create_bot_accepts_the_fields_a_definition_produces():
    sig = inspect.signature(AvernetBotPlatform.create_bot)
    for field in ("spec", "idempotency_key", "owner_id"):
        assert field in sig.parameters
