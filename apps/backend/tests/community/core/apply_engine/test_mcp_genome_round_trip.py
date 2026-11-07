"""The same MCP state survives Manifest, capture and Genome restoration."""

import pytest

from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
from agentclaw.community.core.bot_genome.capture import capture_spec
from agentclaw.community.core.bot_genome.models import Provenance, ProvenanceKind, RefMover, RefUpdate
from agentclaw.community.core.models.mcp import SkillSetMCPServer
from agentclaw.community.core.models.skill import SkillSet
from agentclaw.community.core.repository.implementations.bot.scoped_mcp_config import ScopedMCPConfigRepository
from tests.community.core.bot_genome.service_fixtures import World as GenomeWorld

from ._fakes import real_validator
from ._mcp_config_support import McpWorld


@pytest.mark.asyncio
@pytest.mark.parametrize("bot_type", ["personal", "service"])
async def test_capture_reads_the_direct_override_from_the_bots_stable_resource_identity(bot_type):
    world = McpWorld(bot_type=bot_type)
    report = await world.apply_manifest([{"server_code": "mcp.test", "config": {"endpoint_env": "PRE"}}])
    assert report.status is ApplyStatus.SUCCEEDED
    assert world.saved() == {"mcp.test": {"endpoint_env": "PRE"}}
    captured = await capture_spec(world.state_reader, world.ref, validator=real_validator())
    assert captured.spec.tools.mcp[0].to_json() == {"server_code": "mcp.test", "config": {"endpoint_env": "PRE"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", [None, {"endpoint_env": "PRE"}])
async def test_manifest_capture_drift_and_genome_restore_keep_the_cleared_override(tmp_path, initial):
    world = McpWorld()
    await world.apply_manifest([{"server_code": "mcp.test", "config": initial or {}}])
    report = await world.apply_manifest([{"server_code": "mcp.test"}])
    assert report.status is ApplyStatus.SUCCEEDED
    registry_world = GenomeWorld(tmp_path)
    registry_world.reader = world.state_reader
    registry = registry_world.registry
    actor = Provenance(ProvenanceKind.USER, "owner")
    recorded = await registry.capture(world.ref, created_by=actor)
    revision = recorded.revision.revision.id
    assert recorded.revision.spec.tools.mcp[0].to_json() == {
        "server_code": "mcp.test",
    }
    registry.move_ref(world.ref, RefUpdate("active", revision, None), actor=actor, mover=RefMover.PROMOTION)
    assert await registry.drift(world.ref) is None

    await world.apply_manifest([{"server_code": "mcp.test", "config": {"headers": {"X-Changed": "yes"}}}])
    assert await registry.drift(world.ref) is not None
    report = await world.apply_state(registry.compile(world.ref, revision))
    assert report.status is ApplyStatus.SUCCEEDED
    assert world.saved() == {}
    assert await registry.drift(world.ref) is None


@pytest.mark.asyncio
async def test_dormant_set_configuration_is_not_a_genome_entry_or_drift_but_full_apply_can_clear_it(tmp_path):
    world = McpWorld()
    await world.apply_manifest([{"server_code": "mcp.live", "config": {"endpoint_env": "PRE"}}])
    with world.db.transactional_orm_session() as session:
        dormant = SkillSet(
            name="dormant", user_id="entity-1", bolt_id="bot-1", engine_type="moltis", is_active=False, env="dev",
        )
        session.add(dormant)
        session.flush()
        session.add(SkillSetMCPServer(skill_set_id=dormant.id, server_code="mcp.dormant", name="Dormant", env="dev"))
    for value in ("before", "after"):
        ScopedMCPConfigRepository(world.db).replace(
            user_id="owner", server_code="mcp.dormant", headers={},
            bot_headers={"bot-1": {"X-Saved": value}}, owned_bot_ids={"bot-1"},
            endpoint_env="PROD", transport_protocol=None,
        )
        if value == "before":
            registry_world = GenomeWorld(tmp_path)
            registry_world.reader = world.state_reader
            registry = registry_world.registry
            actor = Provenance(ProvenanceKind.USER, "owner")
            recorded = await registry.capture(world.ref, created_by=actor)
            revision = recorded.revision.revision.id
            assert [entry.server_code for entry in recorded.revision.spec.tools.mcp] == ["mcp.live"]
            registry.move_ref(world.ref, RefUpdate("active", revision, None), actor=actor, mover=RefMover.PROMOTION)
        assert await registry.drift(world.ref) is None
    report = await world.apply_state(registry.compile(world.ref, revision))
    assert report.status is ApplyStatus.SUCCEEDED
    assert world.saved() == {"mcp.live": {"endpoint_env": "PRE"}}
    with world.db.orm_session() as session:
        assert session.query(SkillSetMCPServer).filter_by(server_code="mcp.dormant").count() == 1


async def assert_mcp_compilers_restore_the_same_result(from_manifest, from_revision):
    """Fixture oracle: compare execution results, preserving distinct configuration actions."""
    world = McpWorld()
    assert (await world.apply_state(from_manifest)).status is ApplyStatus.SUCCEEDED
    expected = await capture_spec(world.state_reader, world.ref, validator=real_validator())
    entries = [
        {"server_code": entry.server_code, "config": {"headers": {"X-Changed": "changed"}}}
        for entry in expected.spec.tools.mcp
    ]
    await world.apply_manifest([*entries, {"server_code": "mcp.temporary", "config": {"endpoint_env": "PRE"}}])
    assert (await world.apply_state(from_revision)).status is ApplyStatus.SUCCEEDED
    actual = await capture_spec(world.state_reader, world.ref, validator=real_validator())
    assert actual.spec.tools == expected.spec.tools
