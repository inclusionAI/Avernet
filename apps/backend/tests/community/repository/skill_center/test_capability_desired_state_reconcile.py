"""Installation repair convergence and concurrent writes."""

from __future__ import annotations
from contextlib import contextmanager
from agentclaw.community.core.models.skill import (
    BotSkillInstallation,
    Skill,
    SkillSet,
    SkillSetSkill,
)
from agentclaw.community.core.skill_center.orm import (
    DefaultSkillsetSkillExclusion,
)
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)

from .capability_desired_state_support import (
    _Database,
    _bridge_fixture,
    _bridge,
)


def test_repair_reads_every_set_that_reaches_the_bot_and_no_other():
    """Own ordinary, own Default, and the platform Default — nobody else's."""
    db = _Database()
    _bridge_fixture(db)

    bridge = _bridge(db)

    # 1/2/3 are the members of own-active, own-default and platform-default;
    # 4 and 5 belong to another owner's Bot and another Bot, so neither the
    # listing nor the repair may see them.
    assert bridge.member_skill_ids == frozenset({1, 2, 3})
    # own-default carries ``is_active=False`` and is still active here.
    assert bridge.skills_to_install == frozenset({1, 2, 3})
    assert bridge.skills_to_uninstall == frozenset()


def test_repair_marks_an_inactive_ordinary_sets_members_for_removal():
    """An inactive Set's members must not hold an Installation row."""
    db = _Database()
    with db.transactional_orm_session() as session:
        skill_set = SkillSet(
            name="inactive",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=False,
            env="dev",
        )
        skill = Skill(name="member", git_path="git://member", env="dev")
        session.add_all([skill_set, skill])
        session.flush()
        session.add(
            SkillSetSkill(skill_set_id=skill_set.id, skill_id=skill.id, env="dev")
        )

    bridge = _bridge(db)

    # Still listed — an inactive Skill is a Skill the Bot has.
    assert bridge.member_skill_ids == frozenset({1})
    assert bridge.skills_to_install == frozenset()
    assert bridge.skills_to_uninstall == frozenset({1})


def test_repair_lets_an_active_claim_win_over_an_inactive_one():
    """A stale inactive membership must not uninstall a live member."""
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
        skill = Skill(name="shared", git_path="git://shared", env="dev")
        session.add_all([active, inactive, skill])
        session.flush()
        session.add_all(
            [
                SkillSetSkill(skill_set_id=active.id, skill_id=skill.id, env="dev"),
                SkillSetSkill(skill_set_id=inactive.id, skill_id=skill.id, env="dev"),
            ]
        )

    bridge = _bridge(db)

    assert bridge.skills_to_install == frozenset({1})
    assert bridge.skills_to_uninstall == frozenset()


def test_repair_drops_only_the_owners_own_default_exclusions():
    """The exclusion table is the one thing that removes a Skill."""
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
            name="ordinary",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=True,
            env="dev",
        )
        kept = Skill(name="kept", git_path="git://kept", env="dev")
        excluded = Skill(name="excluded", git_path="git://excluded", env="dev")
        ordinary_member = Skill(name="ordinary", git_path="git://ordinary", env="dev")
        session.add_all(
            [platform_default, ordinary, kept, excluded, ordinary_member]
        )
        session.flush()
        session.add_all(
            [
                SkillSetSkill(
                    skill_set_id=platform_default.id, skill_id=kept.id, env="dev"
                ),
                SkillSetSkill(
                    skill_set_id=platform_default.id, skill_id=excluded.id, env="dev"
                ),
                SkillSetSkill(
                    skill_set_id=ordinary.id, skill_id=ordinary_member.id, env="dev"
                ),
                DefaultSkillsetSkillExclusion(
                    user_id="owner",
                    bot_id="bot",
                    skill_set_id=platform_default.id,
                    skill_id=excluded.id,
                ),
                # Another Bot's exclusion of the same shared Skill.
                DefaultSkillsetSkillExclusion(
                    user_id="owner",
                    bot_id="another-bot",
                    skill_set_id=platform_default.id,
                    skill_id=kept.id,
                ),
                # An exclusion row naming an ordinary Set. That table is only
                # for Default Sets, so this must not remove anything.
                DefaultSkillsetSkillExclusion(
                    user_id="owner",
                    bot_id="bot",
                    skill_set_id=ordinary.id,
                    skill_id=ordinary_member.id,
                ),
            ]
        )

    bridge = _bridge(db)

    # 1 kept, 2 excluded by this Bot's own row, 3 kept because the exclusion
    # naming an ordinary Set has no effect.
    assert bridge.member_skill_ids == frozenset({1, 3})
    assert bridge.skills_to_install == frozenset({1, 3})
    # Exclusion is the Default Set's per-Bot deactivation: the excluded member
    # is absent from the listing AND must not hold an Installation row
    # (spec 2026-08-24-installation-single-source-of-truth, Key domain rules).
    assert bridge.skills_to_uninstall == frozenset({2})


def test_repair_does_not_branch_on_a_skills_source_prefix():
    """No prefix rule anywhere: a member is a member.

    A Default SkillSet is a legacy internal-API construct carrying ``git://``
    Repo Skills, so a Local member of one does not arise in practice. This
    pins that no source-prefix special case is reintroduced on either kind of
    Set, which would silently change which Skills the repair writes.
    """
    db = _Database()
    with db.transactional_orm_session() as session:
        default = SkillSet(
            name="platform-default",
            bolt_id="",
            user_id="",
            engine_type="openclaw",
            is_default=True,
            env="dev",
        )
        ordinary = SkillSet(
            name="ordinary",
            bolt_id="bot",
            user_id="owner",
            engine_type="openclaw",
            is_active=True,
            env="dev",
        )
        session.add_all(
            [
                default,
                ordinary,
                Skill(name="local-in-default", git_path="local://a", env="dev"),
                Skill(name="local-in-ordinary", git_path="local://b", env="dev"),
                # A real Center member: identified by uuid, resolved to its
                # highest PUBLISHED version.
                Skill(
                    name="center-in-ordinary",
                    git_path="center://c",
                    skill_uuid="uuid-c",
                    version=1,
                    status="PUBLISHED",
                    env="dev",
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                SkillSetSkill(skill_set_id=default.id, skill_id=1, env="dev"),
                SkillSetSkill(skill_set_id=ordinary.id, skill_id=2, env="dev"),
                SkillSetSkill(
                    skill_set_id=ordinary.id,
                    skill_id=3,
                    skill_uuid="uuid-c",
                    env="dev",
                ),
            ]
        )

    bridge = _bridge(db)

    assert bridge.member_skill_ids == frozenset({1, 2, 3})
    assert bridge.skills_to_install == frozenset({1, 2, 3})


def test_repair_ignores_a_membership_pointing_outside_the_env():
    """The repair must never install a Skill the listing would refuse to show."""
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
                Skill(name="elsewhere", git_path="git://elsewhere", env="prod"),
            ]
        )
        session.flush()
        session.add_all(
            [
                SkillSetSkill(skill_set_id=skill_set.id, skill_id=1, env="dev"),
                SkillSetSkill(skill_set_id=skill_set.id, skill_id=2, env="dev"),
            ]
        )

    bridge = _bridge(db)

    assert bridge.member_skill_ids == frozenset({1})
    assert bridge.skills_to_install == frozenset({1})


def test_repair_writes_exactly_the_difference_membership_implies():
    """Install what an active Set claims, remove what an inactive one lost."""
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
        session.add_all(
            [
                active,
                inactive,
                Skill(name="missing", git_path="git://missing", env="dev"),
                Skill(name="already", git_path="git://already", env="dev"),
                Skill(name="stale", git_path="git://stale", env="dev"),
            ]
        )
        session.flush()
        session.add_all(
            [
                SkillSetSkill(skill_set_id=active.id, skill_id=1, env="dev"),
                SkillSetSkill(skill_set_id=active.id, skill_id=2, env="dev"),
                SkillSetSkill(skill_set_id=inactive.id, skill_id=3, env="dev"),
                # 2 is already installed and 3 should not be; 4 belongs to no
                # Set at all, so the repair must leave it exactly as it is.
                BotSkillInstallation(
                    bot_id="bot", owner_id="owner", skill_id=2, env="dev"
                ),
                BotSkillInstallation(
                    bot_id="bot", owner_id="owner", skill_id=3, env="dev"
                ),
                BotSkillInstallation(
                    bot_id="bot", owner_id="owner", skill_id=4, env="dev"
                ),
            ]
        )

    repository = CapabilityDesiredStateRepository(db)
    bridge = repository.flush_installations(
        bot_id="bot", owner_id="owner", env="dev", engine_type="openclaw"
    )

    assert bridge.skills_to_install == frozenset({1, 2})
    assert bridge.skills_to_uninstall == frozenset({3})
    with db.orm_session() as session:
        assert {
            row.skill_id for row in session.query(BotSkillInstallation).all()
        } == {1, 2, 4}


def test_repair_is_convergent_and_leaves_another_bots_rows_alone():
    """A second call writes nothing, and the scope never widens past this Bot."""
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
        session.add_all(
            [
                SkillSetSkill(skill_set_id=skill_set.id, skill_id=1, env="dev"),
                # Another owner's Bot holds a row for the very same Skill.
                BotSkillInstallation(
                    bot_id="another-bot", owner_id="someone-else", skill_id=1, env="dev"
                ),
            ]
        )

    repository = CapabilityDesiredStateRepository(db)
    repository.flush_installations(
        bot_id="bot", owner_id="owner", env="dev", engine_type="openclaw"
    )
    with db.orm_session() as session:
        after_first = {
            (row.bot_id, row.owner_id, row.skill_id)
            for row in session.query(BotSkillInstallation).all()
        }

    repository.flush_installations(
        bot_id="bot", owner_id="owner", env="dev", engine_type="openclaw"
    )
    with db.orm_session() as session:
        after_second = {
            (row.bot_id, row.owner_id, row.skill_id)
            for row in session.query(BotSkillInstallation).all()
        }

    assert after_first == {("bot", "owner", 1), ("another-bot", "someone-else", 1)}
    assert after_second == after_first


def test_repair_takes_no_lock_and_opens_no_transaction_when_converged():
    """The common case is a Bot whose desired state already agrees.

    Phase two exists to serialize against SkillSet mutations, which lock the
    Set row they edit. Paying that on every page of a hot listing would make
    two concurrent reads contend for no reason, so a converged Bot must answer
    from the unlocked read alone.
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
        session.add_all(
            [
                SkillSetSkill(skill_set_id=skill_set.id, skill_id=1, env="dev"),
                BotSkillInstallation(
                    bot_id="bot", owner_id="owner", skill_id=1, env="dev"
                ),
            ]
        )

    writes: list[str] = []
    repository = CapabilityDesiredStateRepository(db)
    original = db.transactional_orm_session

    @contextmanager
    def _read_only():
        # This double's orm_session delegates to transactional_orm_session, so
        # pin it to the real one first or the probe below counts reads too.
        with original() as session:
            yield session

    @contextmanager
    def _recording():
        writes.append("transaction")
        with original() as session:
            yield session

    db.orm_session = _read_only
    db.transactional_orm_session = _recording
    try:
        bridge = repository.flush_installations(
            bot_id="bot", owner_id="owner", env="dev", engine_type="openclaw"
        )
    finally:
        db.transactional_orm_session = original
        del db.orm_session

    assert bridge.skills_to_install == frozenset({1})
    assert writes == []


def test_repair_resolves_again_under_lock_before_it_writes():
    """A Set deactivated after the unlocked read must not be repaired back.

    Phase one can resolve a stale answer. Phase two re-resolves holding the
    Set rows, so what gets written is the membership as it stands at write
    time, not as it stood at read time.
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
    def _deactivate_first():
        # Stands in for a concurrent set_active(False) committing between the
        # unlocked resolution and the locked one.
        with original() as session:
            session.query(SkillSet).filter(SkillSet.id == 1).update(
                {"is_active": False}
            )
        db.transactional_orm_session = original
        with original() as session:
            yield session


    db.transactional_orm_session = _deactivate_first
    try:
        bridge = repository.flush_installations(
            bot_id="bot", owner_id="owner", env="dev", engine_type="openclaw"
        )
    finally:
        db.transactional_orm_session = original

    # Re-resolved under lock: the Set is inactive now, so nothing is installed.
    assert bridge.skills_to_install == frozenset()
    with db.orm_session() as session:
        assert session.query(BotSkillInstallation).count() == 0


def test_repair_survives_a_concurrent_listing_winning_the_same_insert():
    """A lost race is the state the repair wanted, not a 500.

    The row lock serializes two repairs on InnoDB, whose ``FOR UPDATE`` scan
    gap-locks a prefix of ``uk_bot_skill_installation``. Postgres is supported
    too, and its ``READ COMMITTED`` ``FOR UPDATE`` locks only rows that already
    exist — so two listings really can both decide to insert the same missing
    row. ``IntegrityError`` is not in ``ENVELOPE_ERRORS``, so letting it
    propagate would answer an idempotent read with a 500.
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
                Skill(name="raced", git_path="git://raced", env="dev"),
                Skill(name="mine", git_path="git://mine", env="dev"),
            ]
        )
        session.flush()
        session.add_all(
            [
                SkillSetSkill(skill_set_id=skill_set.id, skill_id=1, env="dev"),
                SkillSetSkill(skill_set_id=skill_set.id, skill_id=2, env="dev"),
            ]
        )

    repository = CapabilityDesiredStateRepository(db)
    original = db.transactional_orm_session

    @contextmanager
    def _rival_inserts_first():
        # Stands in for a concurrent listing that committed the row for Skill
        # 1 after this repair read the table and before it writes.
        with original() as session:
            session.add(
                BotSkillInstallation(
                    bot_id="bot", owner_id="owner", skill_id=1, env="dev"
                )
            )
        db.transactional_orm_session = original
        with original() as session:
            yield session

    db.transactional_orm_session = _rival_inserts_first
    try:
        bridge = repository.flush_installations(
            bot_id="bot", owner_id="owner", env="dev", engine_type="openclaw"
        )
    finally:
        db.transactional_orm_session = original

    assert bridge.skills_to_install == frozenset({1, 2})
    with db.orm_session() as session:
        # The lost insert is skipped, and Skill 2's still lands.
        assert {
            row.skill_id for row in session.query(BotSkillInstallation).all()
        } == {1, 2}
