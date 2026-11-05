"""Center membership and installation repair compatibility."""

from __future__ import annotations
from contextlib import contextmanager
import pytest
from sqlalchemy.exc import IntegrityError
from agentclaw.community.core.models.skill import (
    BotSkillInstallation,
    Skill,
    SkillSet,
    SkillSetSkill,
)
from agentclaw.community.core.models.mcp import (
    BotMCPInstallation,
    SkillSetMCPServer,
)
from agentclaw.community.core.skill_center.orm import (
    DefaultSkillsetMcpExclusion,
    DefaultSkillsetSkillExclusion,
)
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)
from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneConflictError,
)

from .capability_desired_state_support import (
    _Database,
    _bridge,
    _FailingInsertSession,
    _stale_center_membership,
    _offlined_center_member,
)


def test_flush_preserves_exclusion_plus_skill_installation_as_direct():
    """Exclusion deactivates the member — the flush takes its row away.

    An excluded Default-Set member still belongs to the Set (it is NOT handed
    back to direct control); re-activating it means removing the exclusion
    row. Supersedes the 2026-08-23 "left alone in both directions" decision,
    per spec 2026-08-24-installation-single-source-of-truth Key domain rules.
    """
    db = _Database()
    with db.transactional_orm_session() as session:
        platform_default = SkillSet(
            name="platform-default",
            bolt_id="",
            user_id="",
            engine_type="openclaw",
            is_default=True,
            env="dev",
        )
        session.add_all(
            [
                platform_default,
                Skill(name="excluded", git_path="git://excluded", env="dev"),
                Skill(name="kept", git_path="git://kept", env="dev"),
            ]
        )
        session.flush()
        session.add_all(
            [
                SkillSetSkill(
                    skill_set_id=platform_default.id, skill_id=1, env="dev"
                ),
                SkillSetSkill(
                    skill_set_id=platform_default.id, skill_id=2, env="dev"
                ),
                DefaultSkillsetSkillExclusion(
                    user_id="owner",
                    bot_id="bot",
                    skill_set_id=platform_default.id,
                    skill_id=1,
                ),
                # The row a racing repair inserted before the exclusion landed.
                BotSkillInstallation(
                    bot_id="bot", owner_id="owner", skill_id=1, env="dev"
                ),
            ]
        )

    bridge = _bridge(db)

    # Absent from the listing, claimed inactive, and its stray Installation
    # row removed; the kept member gains the row it was missing.
    assert bridge.member_skill_ids == frozenset({2})
    assert bridge.skills_to_uninstall == frozenset()
    with db.orm_session() as session:
        assert {
            row.skill_id for row in session.query(BotSkillInstallation).all()
        } == {1, 2}


def test_an_active_ordinary_set_outranks_a_default_exclusion():
    """Err-safe on malformed two-Set data: an active claim keeps the row.

    R3 keeps a capability in at most one Set, so this state should not arise;
    when historical data presents it anyway, the flush must not uninstall a
    member a live Set accounts for.
    """
    db = _Database()
    with db.transactional_orm_session() as session:
        platform_default = SkillSet(
            name="platform-default",
            bolt_id="",
            user_id="",
            engine_type="openclaw",
            is_default=True,
            env="dev",
        )
        ordinary = SkillSet(
            name="mine",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=True,
            env="dev",
        )
        session.add_all(
            [
                platform_default,
                ordinary,
                Skill(name="shared", git_path="git://shared", env="dev"),
            ]
        )
        session.flush()
        session.add_all(
            [
                SkillSetSkill(
                    skill_set_id=platform_default.id, skill_id=1, env="dev"
                ),
                SkillSetSkill(skill_set_id=ordinary.id, skill_id=1, env="dev"),
                DefaultSkillsetSkillExclusion(
                    user_id="owner",
                    bot_id="bot",
                    skill_set_id=platform_default.id,
                    skill_id=1,
                ),
            ]
        )

    bridge = _bridge(db)

    assert bridge.skills_to_install == frozenset({1})
    assert bridge.skills_to_uninstall == frozenset()
    assert bridge.member_skill_ids == frozenset({1})


def test_repair_resolves_a_center_member_by_uuid_not_by_stored_skill_id():
    """A Center membership names a versioned identity, not a row.

    ``get_skills_in_set_for_env`` — which the runtime projection uses — joins a
    ``center://`` membership by ``skill_uuid``, because the association's
    ``skill_id`` goes stale when the row behind that identity is replaced.
    ``uk_skill_uuid`` keeps one row per identity per env, so resolving by id
    finds nothing rather than the wrong version — the Skill would vanish from
    the listing while the runtime kept running it.
    """
    db = _Database()
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(
            name="active",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=True,
            env="dev",
        )
        session.add_all(
            [
                skill_set,
                Skill(
                    name="center-current",
                    git_path="center://uuid-a",
                    skill_uuid="uuid-a",
                    version=2,
                    status="PUBLISHED",
                    env="dev",
                ),
            ]
        )
        session.flush()
        # The association still carries the id of the row this identity had
        # before it was replaced.
        session.add(
            SkillSetSkill(
                skill_set_id=skill_set.id,
                skill_id=999,
                skill_uuid="uuid-a",
                env="dev",
            )
        )

    bridge = _bridge(db)

    assert bridge.member_skill_ids == frozenset({1})
    assert bridge.skills_to_install == frozenset({1})
    with db.orm_session() as session:
        assert {
            row.skill_id for row in session.query(BotSkillInstallation).all()
        } == {1}


def test_repair_rejects_a_center_membership_without_its_stable_uuid():
    """A malformed Center membership must not disappear from desired state.

    Runtime projection selects mapping-v3 only after Installation exposes a
    Center asset. Silently resolving this row to no Skill would let an active
    SkillSet report success while publishing only its Local/Repo mappings.
    """
    db = _Database()
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(
            name="active",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=True,
            env="dev",
        )
        skill = Skill(
            name="center",
            git_path="center://public-center",
            skill_uuid="stable-center-uuid",
            version=1,
            status="PUBLISHED",
            env="dev",
        )
        session.add_all([skill_set, skill])
        session.flush()
        session.add(
            SkillSetSkill(
                skill_set_id=skill_set.id,
                skill_id=skill.id,
                skill_uuid=None,
                env="dev",
            )
        )

    repository = CapabilityDesiredStateRepository(db)

    with pytest.raises(
        SkillSetControlPlaneConflictError,
        match="CENTER_MEMBERSHIP_IDENTITY_MISSING",
    ):
        repository.flush_installations(
            bot_id="bot",
            owner_id="owner",
            env="dev",
            engine_type="openclaw",
        )

    with db.orm_session() as session:
        assert session.query(BotSkillInstallation).count() == 0


def test_repair_re_raises_an_integrity_error_that_is_not_a_lost_race():
    """Only a concurrent winner is recoverable; anything else must surface.

    Swallowing every ``IntegrityError`` would let the repair report success for
    a row it never wrote — the transaction commits its other changes, the GET
    answers normally, and the ``active`` it returns contradicts the bridge it
    just returned.
    """
    db = _Database()
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(
            name="active",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=True,
            env="dev",
        )
        session.add_all(
            [skill_set, Skill(name="member", git_path="git://member", env="dev")]
        )
        session.flush()
        session.add(SkillSetSkill(skill_set_id=skill_set.id, skill_id=1, env="dev"))

    repository = CapabilityDesiredStateRepository(db)
    original = db.transactional_orm_session

    @contextmanager
    def _failing_write():
        with original() as session:
            yield _FailingInsertSession(session)

    db.transactional_orm_session = _failing_write
    try:
        with pytest.raises(IntegrityError):
            repository.flush_installations(
                bot_id="bot", owner_id="owner", env="dev", engine_type="openclaw"
            )
    finally:
        db.transactional_orm_session = original

    with db.orm_session() as session:
        assert session.query(BotSkillInstallation).count() == 0


def test_repair_ignores_a_membership_row_from_another_environment():
    """The association carries its own env, and the runtime honours it.

    ``get_skills_in_set_for_env`` filters ``SkillSetSkill.env``; bridging a row
    it ignores would materialize an Installation the runtime's Set projection
    does not recognise, which the direct-install half would then act on.
    """
    db = _Database()
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(
            name="active",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=True,
            env="dev",
        )
        session.add_all(
            [
                skill_set,
                Skill(name="here", git_path="git://here", env="dev"),
                Skill(name="also-here", git_path="git://also-here", env="dev"),
            ]
        )
        session.flush()
        session.add_all(
            [
                SkillSetSkill(skill_set_id=skill_set.id, skill_id=1, env="dev"),
                # Same Set, same-env Skill, but the association is another
                # environment's row.
                SkillSetSkill(skill_set_id=skill_set.id, skill_id=2, env="prod"),
            ]
        )

    bridge = _bridge(db)

    assert bridge.member_skill_ids == frozenset({1})
    with db.orm_session() as session:
        assert {
            row.skill_id for row in session.query(BotSkillInstallation).all()
        } == {1}


def test_deactivating_a_set_removes_the_center_version_the_repair_installed():
    """Otherwise a successful deactivation leaves the Skill running.

    The repair installs the row ``skill_uuid`` resolves to. Reading the id off
    the association instead would delete the replaced version's id — a row that
    does not exist — and leave the current one installed, which the runtime's
    direct-install half then keeps active.
    """
    db = _Database()
    _stale_center_membership(db)
    repository = CapabilityDesiredStateRepository(db)
    repository.flush_installations(
        bot_id="bot", owner_id="owner", env="dev", engine_type="openclaw"
    )
    with db.orm_session() as session:
        assert {
            row.skill_id for row in session.query(BotSkillInstallation).all()
        } == {1}

    repository.set_skill_set_active(
        bot_id="bot", owner_id="owner", set_id=1, active=False, engine_type="openclaw"
    )

    with db.orm_session() as session:
        assert session.query(BotSkillInstallation).count() == 0


def test_activating_a_set_installs_the_center_version_the_repair_would():
    """The mutation and the repair must name the same row, in both directions."""
    db = _Database()
    _stale_center_membership(db)
    with db.transactional_orm_session() as session:
        session.query(SkillSet).filter(SkillSet.id == 1).one().is_active = False
    repository = CapabilityDesiredStateRepository(db)

    repository.set_skill_set_active(
        bot_id="bot", owner_id="owner", set_id=1, active=True, engine_type="openclaw"
    )

    with db.orm_session() as session:
        assert {
            row.skill_id for row in session.query(BotSkillInstallation).all()
        } == {1}


def test_deactivating_a_set_retires_a_member_that_no_longer_resolves():
    """Otherwise the Skill is stuck on, with nothing able to turn it off.

    An `OFFLINE` Center member resolves to nothing, so a teardown driven by the
    resolver alone skips its Installation row — and the runtime keeps
    projecting that row as a direct install. The membership still stands, so
    the guards refuse to let its owner deactivate it by hand either.
    """
    db = _Database()
    _offlined_center_member(db)

    CapabilityDesiredStateRepository(db).set_skill_set_active(
        bot_id="bot",
        owner_id="owner",
        set_id="1",
        active=False,
        engine_type="openclaw",
    )

    with db.orm_session() as session:
        assert session.query(BotSkillInstallation).count() == 0


def test_activating_a_set_still_installs_only_what_resolves():
    """The other half of that asymmetry: never install an unpublished Skill."""
    db = _Database()
    _offlined_center_member(db)
    with db.transactional_orm_session() as session:
        session.query(SkillSet).filter(SkillSet.id == 1).one().is_active = False
        session.query(BotSkillInstallation).delete()

    CapabilityDesiredStateRepository(db).set_skill_set_active(
        bot_id="bot",
        owner_id="owner",
        set_id="1",
        active=True,
        engine_type="openclaw",
    )

    with db.orm_session() as session:
        assert session.query(BotSkillInstallation).count() == 0


def test_removing_a_member_that_no_longer_resolves_retires_its_row():
    """Same asymmetry on the per-Skill path."""
    db = _Database()
    _offlined_center_member(db)

    CapabilityDesiredStateRepository(db).remove_skill(
        bot_id="bot",
        owner_id="owner",
        set_id="1",
        skill_id="1",
        engine_type="openclaw",
    )

    with db.orm_session() as session:
        assert session.query(SkillSetSkill).count() == 0
        assert session.query(BotSkillInstallation).count() == 0


def test_flush_gives_and_takes_mcp_rows_with_set_activation():
    """MCP Installation follows Set membership exactly as skills do.

    An active Set's MCP member gains its row; an inactive Set's MCP member
    loses the stale one; a directly-installed MCP no Set explains is left
    alone in both directions; a second flush writes nothing.
    """
    db = _Database()
    with db.transactional_orm_session() as session:
        active = SkillSet(
            name="active",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=True,
            env="dev",
        )
        inactive = SkillSet(
            name="inactive",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=False,
            env="dev",
        )
        session.add_all([active, inactive])
        session.flush()
        session.add_all(
            [
                SkillSetMCPServer(
                    skill_set_id=active.id,
                    server_code="missing-mcp",
                    name="missing-mcp",
                    env="dev",
                ),
                SkillSetMCPServer(
                    skill_set_id=inactive.id,
                    server_code="stale-mcp",
                    name="stale-mcp",
                    env="dev",
                ),
                BotMCPInstallation(
                    bot_id="bot", owner_id="owner", server_code="stale-mcp", env="dev"
                ),
                # Direct desired state: no membership row anywhere.
                BotMCPInstallation(
                    bot_id="bot", owner_id="owner", server_code="direct-mcp", env="dev"
                ),
            ]
        )

    plan = _bridge(db)

    assert plan.mcps_to_install == frozenset({"missing-mcp"})
    assert plan.mcps_to_uninstall == frozenset({"stale-mcp"})
    with db.orm_session() as session:
        assert {
            row.server_code for row in session.query(BotMCPInstallation).all()
        } == {"missing-mcp", "direct-mcp"}

    _bridge(db)
    with db.orm_session() as session:
        assert {
            row.server_code for row in session.query(BotMCPInstallation).all()
        } == {"missing-mcp", "direct-mcp"}


def test_flush_preserves_exclusion_plus_mcp_installation_as_direct():
    """Default-Set MCP exclusion deactivates the member, like skills."""
    db = _Database()
    with db.transactional_orm_session() as session:
        platform_default = SkillSet(
            name="platform-default",
            bolt_id="",
            user_id="",
            engine_type="openclaw",
            is_default=True,
            env="dev",
        )
        session.add(platform_default)
        session.flush()
        session.add_all(
            [
                SkillSetMCPServer(
                    skill_set_id=platform_default.id,
                    server_code="kept-mcp",
                    name="kept-mcp",
                    env="dev",
                ),
                SkillSetMCPServer(
                    skill_set_id=platform_default.id,
                    server_code="excluded-mcp",
                    name="excluded-mcp",
                    env="dev",
                ),
                DefaultSkillsetMcpExclusion(
                    user_id="owner",
                    bot_id="bot",
                    skill_set_id=platform_default.id,
                    server_code="excluded-mcp",
                ),
                # The row a racing flush inserted before the exclusion landed.
                BotMCPInstallation(
                    bot_id="bot",
                    owner_id="owner",
                    server_code="excluded-mcp",
                    env="dev",
                ),
            ]
        )

    plan = _bridge(db)

    assert plan.mcps_to_install == frozenset({"kept-mcp"})
    assert plan.mcps_to_uninstall == frozenset()
    with db.orm_session() as session:
        assert {
            row.server_code for row in session.query(BotMCPInstallation).all()
        } == {"kept-mcp", "excluded-mcp"}
