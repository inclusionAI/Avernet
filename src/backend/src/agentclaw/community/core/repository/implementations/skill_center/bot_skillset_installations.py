"""Bot-wide resolution from SkillSet membership to Installation rows.

Separate from ``capability_desired_state``, which mutates one named Set: these
range over every Set a Bot has and answer what its Installation table should say.
"""

from __future__ import annotations

from sqlalchemy import and_, func

from agentclaw.community.core.models.mcp import SkillSetMCPServer
from agentclaw.community.core.models.skill import (
    Skill,
    SkillSet,
    SkillSetSkill,
)
from agentclaw.community.core.repository.implementations.skill_center.default_skillset_projection import (
    global_default_scope,
)
from agentclaw.community.core.repository.implementations.skill_center.tables import (
    default_exclusions,
    mcp_installations,
    skill_installations,
)
from agentclaw.community.core.repository.capability_desired_state_types import (
    InstallationFlushPlan,
)
from agentclaw.community.utils.env_utils import get_current_env


def set_member_skill_ids(scope, session, *, skill_set_id: int) -> set[int]:
    """The Skill ids one SkillSet provides.

    Must agree with ``SkillSetRepository.get_skills_in_set_for_env``: a
    ``center://`` membership names its Skill by ``skill_uuid`` and resolves to
    the highest PUBLISHED version, everything else joins by ``skill_id``.
    """
    env = get_current_env()

    def ids(query) -> set[int]:
        return {int(row[0]) for row in query.all()}

    non_center = ids(
        scope(session.query(Skill.id), Skill)
        .join(SkillSetSkill, SkillSetSkill.skill_id == Skill.id)
        .filter(
            SkillSetSkill.skill_set_id == skill_set_id,
            SkillSetSkill.env == env,
            ~Skill.git_path.like("center://%"),
        )
    )
    latest = (
        scope(
            session.query(Skill.skill_uuid, func.max(Skill.version).label("mv")),
            Skill,
        )
        .filter(Skill.skill_uuid.isnot(None), Skill.status == "PUBLISHED")
        .group_by(Skill.skill_uuid)
        .subquery()
    )
    center = ids(
        scope(session.query(Skill.id), Skill)
        .join(SkillSetSkill, SkillSetSkill.skill_uuid == Skill.skill_uuid)
        .join(
            latest,
            and_(
                Skill.skill_uuid == latest.c.skill_uuid,
                Skill.version == latest.c.mv,
            ),
        )
        .filter(
            SkillSetSkill.skill_set_id == skill_set_id,
            SkillSetSkill.env == env,
            Skill.git_path.like("center://%"),
            Skill.status == "PUBLISHED",
        )
    )
    return non_center | center


def set_member_mcp_codes(scope, session, *, skill_set_id: int) -> set[str]:
    """The MCP server codes one SkillSet provides (its membership rows)."""
    return {
        str(row[0])
        for row in scope(
            session.query(SkillSetMCPServer.server_code), SkillSetMCPServer
        )
        .filter(SkillSetMCPServer.skill_set_id == skill_set_id)
        .all()
    }


class BotSkillSetInstallations:
    """Mixed into the control-plane repository; uses its ``_db``, ``_scope``
    and ``_owned_set_scope``."""

    def flush_installations(
        self,
        *,
        bot_id: str,
        owner_id: str,
        env: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> InstallationFlushPlan:
        """Make Installation say what SkillSet membership implies — the flush.

        Skills and MCPs, one algorithm. Returns the plan it applied, so the
        caller need not resolve twice.
        """
        # Resolve unlocked first: a Bot that already agrees — the common case —
        # answers here without a row lock or a write transaction.
        with self._db.orm_session() as session:
            plan = self._resolve_flush_plan(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
            )
            installed = skill_installations.installed_ids(
                session, bot_id=bot_id, owner_id=owner_id, env=env
            )
            installed_mcps = mcp_installations.installed_codes(
                session, bot_id=bot_id, owner_id=owner_id, env=env
            )
        if (
            not (plan.skills_to_install - installed)
            and not (plan.skills_to_uninstall & installed)
            and not (plan.mcps_to_install - installed_mcps)
            and not (plan.mcps_to_uninstall & installed_mcps)
        ):
            return plan

        # There is something to write, so resolve again holding the Set rows —
        # the same locks every SkillSet mutation takes.
        with self._db.transactional_orm_session() as session:
            plan = self._resolve_flush_plan(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
                locked=True,
            )
            installed = skill_installations.installed_ids(
                session, bot_id=bot_id, owner_id=owner_id, env=env, locked=True
            )
            for skill_id in sorted(plan.skills_to_install - installed):
                skill_installations.install(
                    session, bot_id=bot_id, owner_id=owner_id, env=env,
                    skill_id=skill_id,
                )
            skill_installations.uninstall(
                session, bot_id=bot_id, owner_id=owner_id, env=env,
                skill_ids=plan.skills_to_uninstall & installed,
            )
            installed_mcps = mcp_installations.installed_codes(
                session, bot_id=bot_id, owner_id=owner_id, env=env, locked=True
            )
            for server_code in sorted(plan.mcps_to_install - installed_mcps):
                mcp_installations.install(
                    session, bot_id=bot_id, owner_id=owner_id, env=env,
                    server_code=server_code,
                )
            mcp_installations.uninstall(
                session, bot_id=bot_id, owner_id=owner_id, env=env,
                server_codes=plan.mcps_to_uninstall & installed_mcps,
            )
            return plan

    def _resolve_flush_plan(
        self,
        session,
        *,
        bot_id: str,
        owner_id: str,
        engine_type: str | None,
        default_engine_types: tuple[str, ...] | None,
        locked: bool = False,
    ) -> InstallationFlushPlan:
        """What Installation should say, given the Sets this Bot has.

        Runs in the caller's session so the flush can resolve and write in one
        transaction; ``locked`` holds the Set rows for its duration.
        """
        members: set[int] = set()
        active_skills: set[int] = set()
        inactive_skills: set[int] = set()
        active_mcps: set[str] = set()
        inactive_mcps: set[str] = set()
        for row in self._bot_sets(
            session,
            bot_id=bot_id,
            owner_id=owner_id,
            engine_type=engine_type,
            default_engine_types=default_engine_types,
            locked=locked,
        ):
            # A Default Set counts as active whatever its column says, and is
            # the only Set whose members are removed by an exclusion row rather
            # than by deleting the membership.
            is_default = bool(row.is_default)
            set_is_active = is_default or bool(row.is_active)
            excluded = (
                default_exclusions.excluded_skill_ids(
                    session, bot_id=bot_id, owner_id=owner_id, set_id=int(row.id)
                )
                if is_default
                else frozenset()
            )
            for member_id in set_member_skill_ids(
                self._scope, session, skill_set_id=int(row.id)
            ):
                # Exclusion is the Default Set's per-Bot deactivation: an
                # excluded member stays the Set's, is absent from the listing,
                # and must not hold an Installation row.
                if member_id in excluded:
                    inactive_skills.add(member_id)
                    continue
                members.add(member_id)
                (active_skills if set_is_active else inactive_skills).add(member_id)
            excluded_mcps = (
                default_exclusions.excluded_mcp_codes(
                    session, bot_id=bot_id, owner_id=owner_id, set_id=int(row.id)
                )
                if is_default
                else frozenset()
            )
            for server_code in set_member_mcp_codes(
                self._scope, session, skill_set_id=int(row.id)
            ):
                if server_code in excluded_mcps:
                    inactive_mcps.add(server_code)
                    continue
                (active_mcps if set_is_active else inactive_mcps).add(server_code)
        return InstallationFlushPlan(
            member_skill_ids=frozenset(members),
            skills_to_install=frozenset(active_skills),
            # An active claim wins: R3 keeps a capability in one Set, so on
            # historical malformed two-Set data the flush errs safe and never
            # uninstalls a member a live Set accounts for.
            skills_to_uninstall=frozenset(inactive_skills - active_skills),
            mcps_to_install=frozenset(active_mcps),
            mcps_to_uninstall=frozenset(inactive_mcps - active_mcps),
        )

    def _has_active_skill_set_claim(
        self,
        session,
        *,
        bot_sets: list[SkillSet],
        membership_set_ids: set[int],
        skill_id: int,
        bot_id: str,
        owner_id: str,
    ) -> bool:
        """Whether an existing membership, rather than Direct, explains a Skill Installation."""
        for row in bot_sets:
            if int(row.id) not in membership_set_ids:
                continue
            if row.is_default:
                excluded = default_exclusions.excluded_skill_ids(
                    session,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    set_id=int(row.id),
                )
                if skill_id not in excluded:
                    return True
            elif row.is_active:
                return True
        return False

    def _has_active_mcp_set_claim(
        self,
        session,
        *,
        bot_sets: list[SkillSet],
        membership_set_ids: set[int],
        server_code: str,
        bot_id: str,
        owner_id: str,
    ) -> bool:
        """Whether an existing membership, rather than Direct, explains an MCP Installation."""
        for row in bot_sets:
            if int(row.id) not in membership_set_ids:
                continue
            if row.is_default:
                excluded = default_exclusions.excluded_mcp_codes(
                    session,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    set_id=int(row.id),
                )
                if server_code not in excluded:
                    return True
            elif row.is_active:
                return True
        return False

    def _bot_sets(
        self,
        session,
        *,
        bot_id: str,
        owner_id: str,
        engine_type: str | None,
        default_engine_types: tuple[str, ...] | None,
        locked: bool = False,
    ) -> list[SkillSet]:
        """The Sets a Bot has: its own — including its legacy Default — plus
        the platform Default it inherits."""

        def rows(query):
            # Ordered by id. The queries below run owned then defaults, and
            # every caller reaches them through here, so callers agree with
            # each other — but not with `_set`/`_snapshot`. That is the open
            # lock-ordering gap, tracked separately.
            query = query.order_by(SkillSet.id)
            return (query.with_for_update() if locked else query).all()

        owned = rows(
            self._scope(session.query(SkillSet), SkillSet).filter(
                self._owned_set_scope(
                    bot_id=bot_id, owner_id=owner_id, engine_type=engine_type
                )
            )
        )
        candidates = default_engine_types or ((engine_type,) if engine_type else ())
        defaults: list[SkillSet] = []
        for candidate in candidates:
            defaults = rows(
                self._scope(session.query(SkillSet), SkillSet).filter(
                    global_default_scope((candidate,))
                )
            )
            if defaults:
                break
        return [*defaults, *owned]
