"""Bot-wide resolution from SkillSet membership to Installation rows.

Separate from ``capability_desired_state``, which mutates one named Set: these
range over every Set a Bot has and answer what its Installation table should say.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import and_, func, or_

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
from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneConflictError,
)
from agentclaw.community.utils.env_utils import get_current_env


CENTER_MEMBERSHIP_IDENTITY_MISSING = "CENTER_MEMBERSHIP_IDENTITY_MISSING"


def center_membership_skill_uuid(skill: Skill) -> str | None:
    """Return the stable Membership identity required by a Center Skill."""
    if not str(skill.git_path or "").startswith("center://"):
        return None
    skill_uuid = str(skill.skill_uuid or "").strip()
    if not skill_uuid:
        raise SkillSetControlPlaneConflictError(
            CENTER_MEMBERSHIP_IDENTITY_MISSING
        )
    return skill_uuid


def set_member_skill_ids(scope, session, *, skill_set_id: int) -> set[int]:
    """The Skill ids one SkillSet provides.

    Must agree with ``SkillSetRepository.get_skills_in_set_for_env``: a
    ``center://`` membership names its Skill by ``skill_uuid`` and resolves to
    the highest PUBLISHED version, everything else joins by ``skill_id``.
    """
    env = get_current_env()

    malformed_center = (
        scope(session.query(SkillSetSkill.id), SkillSetSkill)
        .join(Skill, SkillSetSkill.skill_id == Skill.id)
        .filter(
            SkillSetSkill.skill_set_id == skill_set_id,
            SkillSetSkill.env == env,
            Skill.git_path.like("center://%"),
            or_(
                SkillSetSkill.skill_uuid.is_(None),
                func.trim(SkillSetSkill.skill_uuid) == "",
            ),
        )
        .first()
    )
    if malformed_center is not None:
        raise SkillSetControlPlaneConflictError(
            CENTER_MEMBERSHIP_IDENTITY_MISSING
        )

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
        return self._reconcile_installations(
            bot_id=bot_id,
            owner_id=owner_id,
            env=env,
            engine_type=engine_type,
            default_engine_types=default_engine_types,
            plan_resolver=self._resolve_flush_plan,
            allow_uninstall=True,
        )

    def sync_default_installations(
        self,
        *,
        bot_id: str,
        owner_id: str,
        env: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> InstallationFlushPlan:
        """Materialize the current Default Set without repairing ordinary Sets.

        The mode is safe only after the operator has backfilled ordinary
        SkillSet history.  A Default membership removal is deliberately not
        inferred from an Installation row: that row may be Direct, and Default
        removal is an explicit operational cleanup.  Per-Bot exclusions are
        the one supported Default removal signal.  An active ordinary claim
        still wins over that signal for malformed historical overlap.
        """
        return self._reconcile_installations(
            bot_id=bot_id,
            owner_id=owner_id,
            env=env,
            engine_type=engine_type,
            default_engine_types=default_engine_types,
            plan_resolver=self._resolve_default_sync_plan,
            allow_uninstall=True,
        )

    def initialize_installations(
        self,
        *,
        bot_id: str,
        owner_id: str,
        env: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> InstallationFlushPlan:
        """Insert missing active Set facts without deleting existing capabilities.

        This is the new-Bot and retry boundary.  A partially persisted Bot can
        be retried safely, while a full backfill remains the explicit repair
        operation allowed to remove stale Set-derived rows.
        """
        return self._reconcile_installations(
            bot_id=bot_id,
            owner_id=owner_id,
            env=env,
            engine_type=engine_type,
            default_engine_types=default_engine_types,
            plan_resolver=self._resolve_flush_plan,
            allow_uninstall=False,
        )

    def _reconcile_installations(
        self,
        *,
        bot_id: str,
        owner_id: str,
        env: str,
        engine_type: str | None,
        default_engine_types: tuple[str, ...] | None,
        plan_resolver: Callable[..., InstallationFlushPlan],
        allow_uninstall: bool,
    ) -> InstallationFlushPlan:
        """Apply one resolver's plan with the shared lock and write protocol."""
        with self._db.orm_session() as session:
            plan = plan_resolver(
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
            and (not allow_uninstall or not (plan.skills_to_uninstall & installed))
            and not (plan.mcps_to_install - installed_mcps)
            and (
                not allow_uninstall
                or not (plan.mcps_to_uninstall & installed_mcps)
            )
        ):
            return plan

        with self._db.transactional_orm_session() as session:
            plan = plan_resolver(
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
            if allow_uninstall:
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
            if allow_uninstall:
                mcp_installations.uninstall(
                    session, bot_id=bot_id, owner_id=owner_id, env=env,
                    server_codes=plan.mcps_to_uninstall & installed_mcps,
                )
            return plan

    def list_member_skill_ids(
        self,
        *,
        bot_id: str,
        owner_id: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> frozenset[int]:
        """Read Set reachability for list/manifest consumers without a flush."""
        with self._db.orm_session() as session:
            members: set[int] = set()
            for row in self._bot_sets(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
            ):
                ids = set_member_skill_ids(self._scope, session, skill_set_id=int(row.id))
                if row.is_default:
                    ids -= set(
                        default_exclusions.excluded_skill_ids(
                            session,
                            bot_id=bot_id,
                            owner_id=owner_id,
                            set_id=int(row.id),
                        )
                    )
                members |= ids
            return frozenset(members)

    def _resolve_default_sync_plan(
        self,
        session,
        *,
        bot_id: str,
        owner_id: str,
        engine_type: str | None,
        default_engine_types: tuple[str, ...] | None,
        locked: bool = False,
    ) -> InstallationFlushPlan:
        """Resolve only Default materialization plus targeted active claims."""
        bot_sets = self._bot_sets(
            session,
            bot_id=bot_id,
            owner_id=owner_id,
            engine_type=engine_type,
            default_engine_types=default_engine_types,
            locked=locked,
        )
        default_skills: set[int] = set()
        excluded_skills: set[int] = set()
        default_mcps: set[str] = set()
        excluded_mcps: set[str] = set()
        ordinary_active_skills: set[int] = set()
        ordinary_active_mcps: set[str] = set()
        for row in bot_sets:
            skill_ids = set_member_skill_ids(
                self._scope, session, skill_set_id=int(row.id)
            )
            mcp_codes = set_member_mcp_codes(
                self._scope, session, skill_set_id=int(row.id)
            )
            if not row.is_default:
                if row.is_active:
                    ordinary_active_skills |= skill_ids
                    ordinary_active_mcps |= mcp_codes
                continue
            excluded_skill_ids = set(
                default_exclusions.excluded_skill_ids(
                    session, bot_id=bot_id, owner_id=owner_id, set_id=int(row.id)
                )
            )
            excluded_mcp_codes = set(
                default_exclusions.excluded_mcp_codes(
                    session, bot_id=bot_id, owner_id=owner_id, set_id=int(row.id)
                )
            )
            default_skills |= skill_ids - excluded_skill_ids
            excluded_skills |= skill_ids & excluded_skill_ids
            default_mcps |= mcp_codes - excluded_mcp_codes
            excluded_mcps |= mcp_codes & excluded_mcp_codes
        return InstallationFlushPlan(
            member_skill_ids=frozenset(default_skills),
            skills_to_install=frozenset(default_skills),
            skills_to_uninstall=frozenset(excluded_skills - ordinary_active_skills),
            mcps_to_install=frozenset(default_mcps),
            mcps_to_uninstall=frozenset(excluded_mcps - ordinary_active_mcps),
        )

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
