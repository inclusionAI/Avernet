"""MCP membership, Direct control and override persistence."""

from __future__ import annotations
from pathlib import Path
import pytest
from agentclaw.community.core.models.skill import (
    SkillSet,
)
from agentclaw.community.core.models.mcp import (
    BotMCPConfig,
    BotMCPInstallation,
    SkillSetMCPServer,
)
from agentclaw.community.core.skill_center.orm import (
    DefaultSkillsetMcpExclusion,
)
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)
from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneConflictError,
)

from .capability_desired_state_support import (
    _Database,
)


@pytest.mark.parametrize("excluded", [False, True])
@pytest.mark.parametrize("active", [False, True])
def test_code_policy_mcp_requires_default_exclusion_to_join_ordinary_set(active, excluded):
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        ordinary = SkillSet(name="ordinary", user_id="owner", bolt_id="bot", is_active=active, env="dev", engine_type="openclaw")
        default = SkillSet(
            name="default", user_id="", bolt_id="", is_default=True,
            engine_type="openclaw", env="dev",
        )
        session.add_all([ordinary, default])
        session.flush()
        if excluded:
            session.add(DefaultSkillsetMcpExclusion(
                user_id="owner", bot_id="bot", skill_set_id=default.id, server_code="mcp.policy"
            ))
    if excluded:
        added = repository.add_mcp(
            bot_id="bot", owner_id="owner", set_id=str(ordinary.id), server_code="mcp.policy",
            name="Policy", description=None, icon=None,
            platform_default_codes=frozenset({"mcp.policy"}), engine_type="openclaw",
        )
        assert added.changed is True
    else:
        with pytest.raises(SkillSetControlPlaneConflictError, match="RESOURCE_MANAGED_BY_PLATFORM_POLICY"):
            repository.add_mcp(
                bot_id="bot", owner_id="owner", set_id=str(ordinary.id), server_code="mcp.policy",
                name="Policy", description=None, icon=None,
                platform_default_codes=frozenset({"mcp.policy"}), engine_type="openclaw",
            )
    with db.orm_session() as session:
        assert session.query(SkillSetMCPServer).count() == int(excluded)
        assert session.query(BotMCPInstallation).count() == int(excluded and active)
        assert session.query(DefaultSkillsetMcpExclusion).count() == int(excluded)


def test_active_skill_set_mutates_mcp_membership_and_installation_atomically():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(name="set", user_id="owner", bolt_id="bot", is_active=True, env="dev")
        session.add(skill_set)
        session.flush()

    added = repository.add_mcp(
        platform_default_codes=frozenset(),
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id), server_code="mcp.weather",
        name="Weather MCP", description="Weather tools",
        icon="https://example.test/weather.png",
    )
    assert added.changed is True
    assert added.mcp_codes == frozenset({"mcp.weather"})
    unchanged_add = repository.add_mcp(
        platform_default_codes=frozenset(),
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id),
        server_code="mcp.weather", name="Weather MCP",
        description="Weather tools", icon="https://example.test/weather.png",
    )
    assert unchanged_add.changed is False
    assert unchanged_add.mcp_codes == frozenset()
    with db.orm_session() as session:
        membership = session.query(SkillSetMCPServer).one()
        assert (membership.name, membership.description, membership.icon) == (
            "Weather MCP", "Weather tools", "https://example.test/weather.png",
        )
        assert {
            row.server_code for row in session.query(BotMCPInstallation).all()
        } == {"mcp.weather"}

    removed = repository.remove_mcp(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id), server_code="mcp.weather"
    )
    assert removed.changed is True
    assert removed.mcp_codes == frozenset({"mcp.weather"})
    unchanged_remove = repository.remove_mcp(
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id),
        server_code="mcp.weather",
    )
    assert unchanged_remove.changed is False
    assert unchanged_remove.mcp_codes == frozenset()
    with db.orm_session() as session:
        assert session.query(SkillSetMCPServer).count() == 0
        assert session.query(BotMCPInstallation).count() == 0


def test_mcp_direct_and_skill_set_ownership_conflicts_are_enforced():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(name="set", user_id="owner", bolt_id="bot", is_active=False, env="dev")
        session.add(skill_set)
        session.flush()

    assert repository.install_mcp(
        bot_id="bot", owner_id="owner", server_code="mcp.weather",
        platform_default_codes=frozenset(),
    ).changed
    with pytest.raises(
        SkillSetControlPlaneConflictError, match="RESOURCE_DIRECT_ACTIVE"
    ):
        repository.add_mcp(
            platform_default_codes=frozenset(),
            bot_id="bot", owner_id="owner", set_id=str(skill_set.id), server_code="mcp.weather",
            name="Weather", description=None, icon=None,
        )
    assert repository.uninstall_mcp(
        bot_id="bot", owner_id="owner", server_code="mcp.weather",
        platform_default_codes=frozenset(),
    ).changed
    assert repository.add_mcp(
        platform_default_codes=frozenset(),
        bot_id="bot", owner_id="owner", set_id=str(skill_set.id), server_code="mcp.weather",
        name="Weather", description=None, icon=None,
    ).changed
    with pytest.raises(
        SkillSetControlPlaneConflictError, match="RESOURCE_MANAGED_BY_SKILL_SET"
    ):
        repository.install_mcp(
            bot_id="bot", owner_id="owner", server_code="mcp.weather",
            platform_default_codes=frozenset(),
        )


def test_platform_default_mcp_refuses_direct_install_and_uninstall():
    repository = CapabilityDesiredStateRepository(_Database())

    for command in (repository.install_mcp, repository.uninstall_mcp):
        with pytest.raises(
            SkillSetControlPlaneConflictError,
            match="RESOURCE_MANAGED_BY_PLATFORM_POLICY",
        ):
            command(
                bot_id="bot",
                owner_id="owner",
                server_code="mcp.policy",
                platform_default_codes=frozenset({"mcp.policy"}),
            )


def test_direct_mcp_installation_isolated_by_owner_for_shared_bot_id():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)

    assert repository.install_mcp(
        bot_id="default", owner_id="owner-a", server_code="mcp.weather",
        platform_default_codes=frozenset(),
    ).changed
    assert repository.install_mcp(
        bot_id="default", owner_id="owner-b", server_code="mcp.weather",
        platform_default_codes=frozenset(),
    ).changed
    assert repository.uninstall_mcp(
        bot_id="default", owner_id="owner-a", server_code="mcp.weather",
        platform_default_codes=frozenset(),
    ).changed

    with db.orm_session() as session:
        rows = session.query(BotMCPInstallation).all()
        assert [(row.owner_id, row.bot_id, row.server_code) for row in rows] == [
            ("owner-b", "default", "mcp.weather")
        ]


def test_direct_mcp_mutations_name_only_the_code_they_changed():
    repository = CapabilityDesiredStateRepository(_Database())

    installed = repository.install_mcp(
        bot_id="bot", owner_id="owner", server_code="mcp.weather",
        platform_default_codes=frozenset(),
    )
    unchanged_install = repository.install_mcp(
        bot_id="bot", owner_id="owner", server_code="mcp.weather",
        platform_default_codes=frozenset(),
    )
    uninstalled = repository.uninstall_mcp(
        bot_id="bot", owner_id="owner", server_code="mcp.weather",
        platform_default_codes=frozenset(),
    )
    unchanged_uninstall = repository.uninstall_mcp(
        bot_id="bot", owner_id="owner", server_code="mcp.weather",
        platform_default_codes=frozenset(),
    )

    assert installed.mcp_codes == frozenset({"mcp.weather"})
    assert unchanged_install.mcp_codes == frozenset()
    assert uninstalled.mcp_codes == frozenset({"mcp.weather"})
    assert unchanged_uninstall.mcp_codes == frozenset()


def test_direct_mcp_override_is_atomic_with_installation_and_can_be_cleared():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    override = {
        "headers": {},
        "endpoint_env": "PRE",
        "transport_protocol": "STREAMABLE_HTTP",
    }

    created = repository.set_mcp_override(
        bot_id="bot",
        owner_id="owner",
        server_code="mcp.weather",
        config=override,
        platform_default_codes=frozenset(),
    )

    assert created.changed is True
    assert created.mcp_codes == frozenset({"mcp.weather"})
    assert created.updated_mcp_codes == frozenset()
    assert repository.get_mcp_overrides(bot_id="bot", owner_id="owner") == {
        "mcp.weather": override
    }

    updated = repository.set_mcp_override(
        bot_id="bot",
        owner_id="owner",
        server_code="mcp.weather",
        config={"url": "https://override.example/mcp"},
        platform_default_codes=frozenset(),
    )
    assert updated.changed is True
    assert updated.mcp_codes == frozenset()
    assert updated.updated_mcp_codes == frozenset({"mcp.weather"})

    cleared = repository.set_mcp_override(
        bot_id="bot",
        owner_id="owner",
        server_code="mcp.weather",
        config=None,
        platform_default_codes=frozenset(),
    )
    assert cleared.updated_mcp_codes == frozenset({"mcp.weather"})
    with db.orm_session() as session:
        assert session.query(BotMCPInstallation).count() == 1
        assert session.query(BotMCPConfig).count() == 0


def test_direct_mcp_uninstall_removes_the_bot_override():
    db = _Database()
    repository = CapabilityDesiredStateRepository(db)
    repository.set_mcp_override(
        bot_id="bot",
        owner_id="owner",
        server_code="mcp.weather",
        config={"headers": {"X-Project": "alpha"}},
        platform_default_codes=frozenset(),
    )

    result = repository.uninstall_mcp(
        bot_id="bot",
        owner_id="owner",
        server_code="mcp.weather",
        platform_default_codes=frozenset(),
    )

    with db.orm_session() as session:
        assert session.query(BotMCPInstallation).count() == 0
        assert session.query(BotMCPConfig).count() == 0
    assert result.updated_mcp_codes == frozenset({"mcp.weather"})


def test_deactivating_a_set_preserves_its_mcp_override_without_installation():
    db = _Database()
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(
            name="tools",
            user_id="owner",
            bolt_id="bot",
            engine_type="openclaw",
            is_active=True,
            env="dev",
        )
        session.add(skill_set)
        session.flush()
        session.add_all(
            [
                SkillSetMCPServer(
                    skill_set_id=skill_set.id,
                    server_code="mcp.owned",
                    name="owned",
                    env="dev",
                ),
                BotMCPInstallation(
                    bot_id="bot",
                    owner_id="owner",
                    server_code="mcp.owned",
                    env="dev",
                ),
                BotMCPConfig(
                    bot_id="bot",
                    owner_id="owner",
                    server_code="mcp.owned",
                    config='{"headers":{"X-Test":"1"}}',
                    env="dev",
                ),
            ]
        )

    CapabilityDesiredStateRepository(db).set_skill_set_active(
        bot_id="bot",
        owner_id="owner",
        set_id=str(skill_set.id),
        active=False,
        engine_type="openclaw",
    )

    with db.orm_session() as session:
        assert session.query(BotMCPInstallation).count() == 0
        assert session.query(BotMCPConfig).count() == 1


    assert CapabilityDesiredStateRepository(db).get_mcp_overrides(
        bot_id="bot", owner_id="owner"
    ) == {"mcp.owned": {"headers": {"X-Test": "1"}}}


def test_flush_preserves_override_for_an_inactive_set_member():
    db = _Database()
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(
            name="tools",
            user_id="owner",
            bolt_id="bot",
            engine_type="openclaw",
            is_active=False,
            env="dev",
        )
        session.add(skill_set)
        session.flush()
        session.add_all(
            [
                SkillSetMCPServer(
                    skill_set_id=skill_set.id,
                    server_code="mcp.stale",
                    name="stale",
                    env="dev",
                ),
                BotMCPInstallation(
                    bot_id="bot",
                    owner_id="owner",
                    server_code="mcp.stale",
                    env="dev",
                ),
                BotMCPConfig(
                    bot_id="bot",
                    owner_id="owner",
                    server_code="mcp.stale",
                    config='{"url":"https://stale.example/mcp"}',
                    env="dev",
                ),
            ]
        )

    CapabilityDesiredStateRepository(db).flush_installations(
        bot_id="bot",
        owner_id="owner",
        env="dev",
        engine_type="openclaw",
    )

    with db.orm_session() as session:
        assert session.query(BotMCPInstallation).count() == 0
        assert session.query(BotMCPConfig).count() == 1


    assert CapabilityDesiredStateRepository(db).get_mcp_overrides(
        bot_id="bot", owner_id="owner"
    ) == {"mcp.stale": {"url": "https://stale.example/mcp"}}


def test_skill_set_control_plane_sql_only_adds_owner_scoped_mcp_installation():
    sql_path = (
        Path(__file__).parents[4]
        / "src"
        / "agentclaw"
        / "community"
        / "core"
        / "skill_center"
        / "sql"
        / "2026_08_20_skill_set_control_plane.sql"
    )
    sql = sql_path.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS ac_bot_mcp_installation" in sql
    assert "owner_id VARCHAR(128) NOT NULL" in sql
    assert "(avernet_tenant, env, owner_id, bot_id, server_code)" in sql
    assert "ac_skill_set_create_idempotency" not in sql
    assert "ALTER TABLE ac_skill_set_skill" not in sql
    assert "ALTER TABLE ac_skill_set_mcp" not in sql


def test_bot_mcp_config_sql_is_owner_bot_tenant_and_env_scoped():
    sql_path = (
        Path(__file__).parents[4]
        / "src"
        / "agentclaw"
        / "community"
        / "core"
        / "skill_center"
        / "sql"
        / "2026_09_21_bot_mcp_config.sql"
    )
    sql = sql_path.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS ac_bot_mcp_config" in sql
    assert "config TEXT NOT NULL" in sql
    assert "(avernet_tenant, env, owner_id, bot_id, server_code)" in sql
