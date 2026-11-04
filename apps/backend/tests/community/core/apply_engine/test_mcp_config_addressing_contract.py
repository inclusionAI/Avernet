"""One Bot resource address across scoped config, capture and Apply."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentclaw.community.core.mcp.scoped_config_contract import HeaderGroup
from agentclaw.community.core.mcp.scoped_config_flow import MCPScopedConfigService
from agentclaw.community.core.repository.implementations.bot.scoped_mcp_config import (
    ScopedMCPConfigRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.mcp_default_exclusion import (
    MCPDefaultExclusionReader,
)
from agentclaw.community.core.skill_center.services.effective_mcp_state_reader import (
    EffectiveMCPStateReader,
)

from ._mcp_config_support import McpWorld


def scoped_service(world):
    effective = EffectiveMCPStateReader(
        world.capabilities,
        MCPDefaultExclusionReader(world.db),
        world.bots,
        world.policy,
    )
    return MCPScopedConfigService(
        world.config,
        world.overrides,
        world.bots,
        ScopedMCPConfigRepository(world.db),
        world.center,
        SimpleNamespace(
            sync_mcp_detail_to_all_bots=AsyncMock(
                return_value={"success": True, "sync_results": []}
            )
        ),
        effective,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("bot_type", ["personal", "service"])
async def test_scoped_write_is_captured_at_the_bots_stable_address(bot_type):
    world = McpWorld(bot_type=bot_type)
    await world.apply_manifest([{"server_code": "mcp.test"}])
    service = scoped_service(world)
    await service.replace(
        user_id="owner",
        server_code="mcp.test",
        endpoint_env="PROD",
        transport_protocol=None,
        params=(HeaderGroup(key="X-Saved", value="scoped", bots=("bot-1",)),),
    )
    captured = await world.state_reader.mcp_servers(world.ref)
    assert captured[0].override == {"headers": {"X-Saved": "scoped"}}
    saved = service.read(user_id="owner", server_code="mcp.test")
    assert saved.params == (
        HeaderGroup(key="X-Saved", value="scoped", bots=("bot-1",)),
    )


def seed_override(world, *, owner_id, config, server_code="mcp.test"):
    import json
    from agentclaw.community.core.models.mcp import BotMCPConfig
    from agentclaw.community.utils.env_utils import get_current_env

    with world.db.transactional_orm_session() as session:
        session.add(
            BotMCPConfig(
                bot_id="bot-1",
                owner_id=owner_id,
                server_code=server_code,
                env=get_current_env(),
                config=json.dumps(config),
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("canonical", [None, {"headers": {"X-New": "current"}}, {}])
async def test_legacy_read_is_pure_and_canonical_value_wins_whole(canonical):
    from sqlalchemy import event

    world = McpWorld()
    await world.apply_manifest([{"server_code": "mcp.test"}])
    legacy = {"headers": {"X-Old": "legacy"}, "url": "https://legacy.example/mcp"}
    seed_override(world, owner_id="owner", config=legacy)
    if canonical is not None:
        seed_override(world, owner_id="entity-1", config=canonical)

    def forbid_config_writes(_conn, _cursor, statement, _params, _context, _many):
        if (
            "ac_bot_mcp_config" in statement.lower()
            and statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        ):
            raise AssertionError("Configuration reads must not migrate persisted rows")

    event.listen(world.db.engine, "before_cursor_execute", forbid_config_writes)
    try:
        actual = world.config.get_bot_override(
            bot_id="bot-1", owner_id="entity-1", server_code="mcp.test"
        )
        assert actual == (legacy if canonical is None else canonical)
        captured = await world.state_reader.mcp_servers(world.ref)
        assert captured[0].override == (
            legacy if canonical is None else canonical or None
        )
        scoped_service(world).read(user_id="owner", server_code="mcp.test")
    finally:
        event.remove(world.db.engine, "before_cursor_execute", forbid_config_writes)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action", ["clear", "parent_replace", "direct_uninstall", "bot_purge"]
)
async def test_explicit_removal_cannot_resurrect_legacy_configuration(action):
    world = McpWorld()
    await world.apply_manifest([{"server_code": "mcp.test"}])
    seed_override(world, owner_id="owner", config={"headers": {"X-Old": "legacy"}})
    seed_override(world, owner_id="entity-1", config={"headers": {"X-New": "current"}})
    if action == "clear":
        await world.apply_manifest([{"server_code": "mcp.test", "config": {}}])
    elif action == "parent_replace":
        await world.apply_manifest([])
    elif action == "direct_uninstall":
        await world.direct.deactivate_mcp(
            server_code="mcp.test", bot_id="bot-1", owner_id="owner", actor_id="owner"
        )
    else:
        world.bots.soft_delete_by_owner("bot-1", "owner")
        world.desired.purge_bot_installations(
            bot_id="bot-1", owner_id="entity-1", env="dev"
        )
    assert (
        world.config.get_bot_override(
            bot_id="bot-1", owner_id="entity-1", server_code="mcp.test"
        )
        is None
    )
    if action != "bot_purge":
        await world.apply_manifest([{"server_code": "mcp.test"}])
        assert (await world.state_reader.mcp_servers(world.ref))[0].override is None
        assert (
            scoped_service(world).read(user_id="owner", server_code="mcp.test").params
            == ()
        )


@pytest.mark.asyncio
async def test_scoped_header_replacement_keeps_legacy_url_and_other_fields():
    world = McpWorld()
    await world.apply_manifest([{"server_code": "mcp.test"}])
    seed_override(
        world,
        owner_id="owner",
        config={
            "headers": {"X-Old": "legacy"},
            "url": "https://legacy.example/mcp",
            "endpoint_env": "PRE",
        },
    )
    service = scoped_service(world)
    await service.replace(
        user_id="owner",
        server_code="mcp.test",
        endpoint_env="PROD",
        transport_protocol=None,
        params=(HeaderGroup(key="X-New", value="current", bots=("bot-1",)),),
    )
    assert (await world.state_reader.mcp_servers(world.ref))[0].override == {
        "headers": {"X-New": "current"},
        "url": "https://legacy.example/mcp",
        "endpoint_env": "PRE",
    }
    await service.replace(
        user_id="owner",
        server_code="mcp.test",
        endpoint_env="PROD",
        transport_protocol=None,
        params=(),
        url_rules=(),
    )
    assert (await world.state_reader.mcp_servers(world.ref))[0].override == {
        "endpoint_env": "PRE"
    }


@pytest.mark.asyncio
async def test_explicit_entity_remains_usable_when_another_bots_owner_has_the_same_name():
    world = McpWorld()
    await world.apply_manifest([{"server_code": "mcp.test"}])
    seed_override(
        world, owner_id="entity-1", config={"headers": {"X-Canonical": "one"}}
    )
    world.bots.insert(
        {
            "bot_id": "bot-1",
            "bot_name": "Second",
            "owner_id": "entity-1",
            "creator_id": "entity-1",
            "entity_id": "entity-2",
            "entity_type": "staff",
            "status": "ACTIVE",
            "active_engine": "moltis",
        }
    )
    seed_override(
        world, owner_id="entity-2", config={"headers": {"X-Canonical": "two"}}
    )
    assert (await world.state_reader.mcp_servers(world.ref))[0].override == {
        "headers": {"X-Canonical": "one"}
    }
    assert scoped_service(world).read(
        user_id="entity-1", server_code="mcp.test"
    ).params == (HeaderGroup(key="X-Canonical", value="two", bots=("bot-1",)),)
    await scoped_service(world).replace(
        user_id="entity-1", server_code="mcp.test", endpoint_env="PROD", transport_protocol=None,
        params=(HeaderGroup(key="X-Canonical", value="changed", bots=("bot-1",)),),
    )
    assert (await world.state_reader.mcp_servers(world.ref))[0].override == {"headers": {"X-Canonical": "one"}}
    assert world.config.get_bot_override(
        bot_id="bot-1", owner_id="entity-2", server_code="mcp.test"
    ) == {
        "headers": {"X-Canonical": "changed"},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("is_delete", [0, 1])
async def test_legacy_alias_that_can_name_another_bot_is_not_borrowed_or_cleared(is_delete):
    world = McpWorld()
    await world.apply_manifest([{"server_code": "mcp.test"}])
    world.bots.insert(
        {
            "bot_id": "bot-1",
            "bot_name": "Other",
            "is_delete": is_delete,
            "owner_id": "other",
            "creator_id": "other",
            "entity_id": "owner",
            "entity_type": "staff",
            "status": "ACTIVE",
            "active_engine": "moltis",
        }
    )
    seed_override(world, owner_id="owner", config={"headers": {"X-Other": "keep"}})
    assert world.config.get_bot_override(
        bot_id="bot-1", owner_id="entity-1", server_code="mcp.test"
    ) is None
    await scoped_service(world).replace(
        user_id="owner",
        server_code="mcp.test",
        endpoint_env="PROD",
        transport_protocol=None,
        params=(),
    )
    assert world.config.get_bot_override(
        bot_id="bot-1", owner_id="owner", server_code="mcp.test"
    ) == {
        "headers": {"X-Other": "keep"},
    }


@pytest.mark.asyncio
async def test_clear_isolated_from_other_tenant_and_environment(monkeypatch):
    from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

    world = McpWorld()
    await world.apply_manifest([{"server_code": "mcp.test"}])
    seed_override(world, owner_id="owner", config={"headers": {"X-Current": "clear"}})
    with avernet_tenant_scope("another-tenant"):
        seed_override(
            world, owner_id="entity-1", config={"headers": {"X-Tenant": "keep"}}
        )
    with monkeypatch.context() as patch:
        patch.setenv("SERVER_ENV", "pre")
        seed_override(
            world, owner_id="entity-1", config={"headers": {"X-Environment": "keep"}}
        )
    await world.apply_manifest([{"server_code": "mcp.test", "config": {}}])
    assert (
        world.config.get_bot_override(
            bot_id="bot-1", owner_id="entity-1", server_code="mcp.test"
        )
        is None
    )
    with avernet_tenant_scope("another-tenant"):
        assert world.config.get_bot_override(
            bot_id="bot-1", owner_id="entity-1", server_code="mcp.test"
        ) == {
            "headers": {"X-Tenant": "keep"},
        }
    with monkeypatch.context() as patch:
        patch.setenv("SERVER_ENV", "pre")
        assert world.config.get_bot_override(
            bot_id="bot-1", owner_id="entity-1", server_code="mcp.test"
        ) == {
            "headers": {"X-Environment": "keep"},
        }


@pytest.mark.asyncio
async def test_scoped_write_and_legacy_values_reach_whole_artifact(tmp_path):
    from agentclaw.community.core.config_compose.models import ComposeRequest
    from .test_mcp_artifact_contract import _artifact_composer

    world = McpWorld(platform=True)
    await world.apply_manifest([{"server_code": "mcp.test"}])
    seed_override(world, owner_id="owner", config={"url": "https://legacy.example/mcp"})
    await scoped_service(world).replace(
        user_id="owner",
        server_code="mcp.test",
        endpoint_env="PROD",
        transport_protocol=None,
        params=(HeaderGroup(key="X-Artifact", value="current", bots=("bot-1",)),),
    )
    artifact = _artifact_composer(world, tmp_path).compose(
        ComposeRequest(
            entity_id="entity-1",
            user_id="owner",
            bot_id="bot-1",
            engine_type="teclaw",
        )
    )
    server = next(
        entry for entry in artifact.mcp.servers if entry.server_code == "mcp.test"
    )
    assert server.endpoint == "https://legacy.example/mcp"
    assert server.headers["X-Artifact"] == "current"


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit", [{}, {"headers": {"X-Old": "legacy"}}])
async def test_explicit_config_consumes_alias_even_when_effective_value_is_unchanged(
    explicit,
):
    from agentclaw.community.core.models.mcp import BotMCPConfig
    from agentclaw.community.core.apply_engine.outcomes import EntryOutcome

    world = McpWorld()
    await world.apply_manifest([{"server_code": "mcp.test"}])
    seed_override(world, owner_id="owner", config={"headers": {"X-Old": "legacy"}})
    if not explicit:
        seed_override(world, owner_id="entity-1", config={})
    report = await world.apply_manifest(
        [{"server_code": "mcp.test", "config": explicit}]
    )
    assert report.categories[0].entries[0].outcome is EntryOutcome.UNCHANGED
    with world.db.orm_session() as session:
        rows = (
            session.query(BotMCPConfig)
            .filter_by(bot_id="bot-1", server_code="mcp.test")
            .all()
        )
        assert {row.owner_id for row in rows} == ({"entity-1"} if explicit else set())


@pytest.mark.asyncio
async def test_same_canonical_value_and_header_case_do_not_write_configuration():
    from sqlalchemy import event
    from agentclaw.community.core.apply_engine.outcomes import EntryOutcome

    from .test_capability_full_replace_contract import _Runtime

    runtime = _Runtime()
    world = McpWorld(runtime=runtime)
    await world.apply_manifest(
        [{"server_code": "mcp.test", "config": {"headers": {"X-Saved": "same"}}}]
    )
    delivered_before = len(runtime.scopes)

    def forbid_config_writes(_conn, _cursor, statement, _params, _context, _many):
        if (
            "ac_bot_mcp_config" in statement.lower()
            and statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        ):
            raise AssertionError(
                "Equivalent canonical configuration must not be rewritten"
            )

    event.listen(world.db.engine, "before_cursor_execute", forbid_config_writes)
    try:
        for config in (
            {"headers": {"X-Saved": "same"}},
            {"headers": {"x-saved": "same"}},
        ):
            report = await world.apply_manifest(
                [{"server_code": "mcp.test", "config": config}]
            )
            assert report.categories[0].entries[0].outcome is EntryOutcome.UNCHANGED
        assert (await world.state_reader.mcp_servers(world.ref))[0].override == {
            "headers": {"X-Saved": "same"}
        }
        assert len(runtime.scopes) == delivered_before
    finally:
        event.remove(world.db.engine, "before_cursor_execute", forbid_config_writes)


@pytest.mark.asyncio
async def test_failed_scoped_normalization_keeps_legacy_and_user_defaults_unchanged():
    from sqlalchemy import event
    from agentclaw.community.core.models.mcp import BotMCPConfig

    world = McpWorld()
    await world.apply_manifest([{"server_code": "mcp.test"}])
    legacy = {"headers": {"X-Old": "legacy"}, "url": "https://legacy.example/mcp"}
    seed_override(world, owner_id="owner", config=legacy)

    def reject_config_insert(_conn, _cursor, statement, _params, _context, _many):
        if statement.lower().lstrip().startswith("insert into ac_bot_mcp_config"):
            raise RuntimeError("Canonical config storage unavailable")

    event.listen(world.db.engine, "before_cursor_execute", reject_config_insert)
    try:
        with pytest.raises(RuntimeError, match="Canonical config storage unavailable"):
            await scoped_service(world).replace(
                user_id="owner",
                server_code="mcp.test",
                endpoint_env="PROD",
                transport_protocol=None,
                params=(
                    HeaderGroup(key="X-Global", value="new", bots=()),
                    HeaderGroup(key="X-New", value="new", bots=("bot-1",)),
                ),
            )
    finally:
        event.remove(world.db.engine, "before_cursor_execute", reject_config_insert)
    assert world.config.get_user_unified_config("owner", "mcp.test") is None
    assert (await world.state_reader.mcp_servers(world.ref))[0].override == legacy
    with world.db.orm_session() as session:
        assert {
            row.owner_id
            for row in session.query(BotMCPConfig).filter_by(bot_id="bot-1").all()
        } == {"owner"}


@pytest.mark.asyncio
async def test_unknown_past_owner_row_is_not_guessed_as_current_bot_configuration():
    world = McpWorld()
    await world.apply_manifest([{"server_code": "mcp.test"}])
    seed_override(world, owner_id="unknown-past-owner", config={"headers": {"X-Orphan": "not-current"}})
    assert (await world.state_reader.mcp_servers(world.ref))[0].override is None
    assert scoped_service(world).read(user_id="owner", server_code="mcp.test").params == ()
    assert scoped_service(world).read(user_id="unknown-past-owner", server_code="mcp.test").params == ()


@pytest.mark.parametrize("stale", ["handoff", "deleted", "missing"])
def test_scoped_transaction_rechecks_owned_targets_and_rolls_back_user_defaults(stale):
    from agentclaw.community.core.mcp.errors import McpConfigValueError
    from agentclaw.community.plugin_api.models import BotModel

    world = McpWorld()
    seed_override(world, owner_id="entity-1", config={"headers": {"X-Canonical": "keep"}})
    with world.db.transactional_orm_session() as session:
        bot = session.get(BotModel, world.bot["id"])
        if stale == "handoff":
            bot.owner_id = "next"
        elif stale == "deleted":
            bot.is_delete = 1
    target = "missing" if stale == "missing" else "bot-1"
    with pytest.raises(McpConfigValueError, match="no longer owned"):
        ScopedMCPConfigRepository(world.db).replace(
            user_id="owner", server_code="mcp.test", headers={"X-Global": "not-committed"},
            bot_headers={target: {"X-Canonical": "not-committed"}}, owned_bot_ids={target},
            endpoint_env="PROD", transport_protocol=None,
        )
    assert world.config.get_user_unified_config("owner", "mcp.test") is None
    assert world.config.get_bot_override(bot_id="bot-1", owner_id="entity-1", server_code="mcp.test") == {
        "headers": {"X-Canonical": "keep"},
    }


@pytest.mark.asyncio
async def test_scoped_same_account_duplicate_bot_id_is_refused_without_changing_either_bot():
    from agentclaw.community.core.bot_management.errors import BotLookupAmbiguousError

    world = McpWorld()
    seed_override(world, owner_id="entity-1", config={"headers": {"X-One": "keep"}})
    world.bots.insert({
        "bot_id": "bot-1", "bot_name": "Second", "owner_id": "owner", "creator_id": "owner",
        "entity_id": "entity-2", "entity_type": "staff", "status": "ACTIVE", "active_engine": "moltis",
    })
    seed_override(world, owner_id="entity-2", config={"headers": {"X-Two": "keep"}})
    with pytest.raises(BotLookupAmbiguousError):
        await scoped_service(world).replace(
            user_id="owner", server_code="mcp.test", endpoint_env="PROD", transport_protocol=None,
            params=(HeaderGroup(key="X-One", value="not-committed", bots=("bot-1",)),),
        )
    assert world.config.get_bot_override(bot_id="bot-1", owner_id="entity-1", server_code="mcp.test") == {
        "headers": {"X-One": "keep"},
    }
    assert world.config.get_bot_override(bot_id="bot-1", owner_id="entity-2", server_code="mcp.test") == {
        "headers": {"X-Two": "keep"},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("copy_kind", ["deepcopy", "pickle"])
async def test_copied_clear_declaration_clears_saved_override(copy_kind):
    import copy
    import pickle

    from agentclaw.community.core.apply_engine.desired_state import DesiredState
    from agentclaw.community.core.bot_config_manifest.compiler.entries.mcp import compile_mcp

    world = McpWorld()
    saved = {"headers": {"X-Saved": "retained"}}
    await world.apply_manifest([{"server_code": "mcp.test", "config": saved}])
    declaration = compile_mcp([{"server_code": "mcp.test"}])
    copied = (
        copy.deepcopy(declaration) if copy_kind == "deepcopy"
        else pickle.loads(pickle.dumps(declaration))
    )
    await world.apply_state(DesiredState(mcp=copied))
    assert (await world.state_reader.mcp_servers(world.ref))[0].override is None
