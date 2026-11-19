"""Resolve SkillSet members and reconcile their Bot Installation rows."""

from __future__ import annotations

from agentclaw.community.core.repository.implementations.skill_center.bot_mutation_fence import lock_bot_capabilities

from collections.abc import Callable

from sqlalchemy import and_, func, or_

from agentclaw.community.core.models.mcp import SkillSetMCPServer
from agentclaw.community.core.models.skill import (
    Skill,
    SkillSet,
    SkillSetSkill,
)
from agentclaw.community.core.models.space_skill import SkillVersion
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
from agentclaw.community.core.skill_center.default_members import effective_default_members
from agentclaw.community.core.repository.implementations.skill_center.skill_mcp_dependencies import skill_mcp_dependency_codes
from agentclaw.community.core.skill_center.mcp_dependency_scope import mcp_dependency_codes_from_version_metadata
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.core.models.skill_installation_source import SkillInstallationSourceKind


CENTER_MEMBERSHIP_IDENTITY_MISSING = "CENTER_MEMBERSHIP_IDENTITY_MISSING"
logger = get_logger()


def _latest_published_versions(scope, session, skill_ids, *, metadata=False, locked=False):
    latest = scope(session.query(
        SkillVersion.skill_id.label("skill_id"),
        func.max(SkillVersion.version_ordinal).label("ordinal"),
    ), SkillVersion).filter(
        SkillVersion.skill_id.in_(skill_ids), SkillVersion.status == "PUBLISHED"
    ).group_by(SkillVersion.skill_id).subquery()
    columns = [SkillVersion.skill_id, SkillVersion.sc_version_number, SkillVersion.name]
    if metadata:
        columns.append(SkillVersion.metadata_json)
    query = scope(session.query(*columns), SkillVersion).join(
        latest, and_(SkillVersion.skill_id == latest.c.skill_id,
                     SkillVersion.version_ordinal == latest.c.ordinal),
    ).filter(SkillVersion.status == "PUBLISHED")
    return (query.with_for_update() if locked else query).all()


def available_published_version_skill_ids(scope, session, skill_ids: set[int]) -> set[int]:
    """Find Center identities whose latest published Version has an address."""
    rows = _latest_published_versions(scope, session, skill_ids)
    return {int(skill_id) for skill_id, number, name in rows if number and name}


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


def set_member_skill_ids(
    scope, session, *, skill_set_id: int, excluded_raw_ids: frozenset[int] = frozenset()
) -> set[int]:
    """The Skill ids one SkillSet provides.

    Must agree with ``SkillSetRepository.get_skills_in_set_for_env``: a
    ``center://`` membership names its Skill by ``skill_uuid`` and resolves to
    the highest PUBLISHED version, everything else joins by ``skill_id``.
    """
    env = get_current_env()
    default = scope(session.query(SkillSet), SkillSet).filter(SkillSet.id == skill_set_id).one_or_none()
    if default is not None and default.is_default:
        members = scope(session.query(SkillSetSkill), SkillSetSkill).filter(
            SkillSetSkill.skill_set_id == skill_set_id,
            SkillSetSkill.skill_id.notin_(excluded_raw_ids),
        ).all()
        ids = {int(row.skill_id) for row in members}
        uuids = {row.skill_uuid for row in members if row.skill_uuid}
        candidates = scope(session.query(Skill), Skill).filter(
            or_(Skill.id.in_(ids), Skill.skill_uuid.in_(uuids))
        ).all()
        published_ids = available_published_version_skill_ids(
            scope, session, {int(row.id) for row in candidates}
        )
        catalog = [{key: getattr(row, key) for key in (
            "id", "skill_uuid", "git_path", "status", "version", "offline_at"
        )} | {"has_published_version": int(row.id) in published_ids} for row in candidates]
        resolved, skipped = effective_default_members(
            ((int(row.skill_id), row.skill_uuid) for row in members), catalog
        )
        if "missing_center_identity" in skipped.values():
            raise SkillSetControlPlaneConflictError(CENTER_MEMBERSHIP_IDENTITY_MISSING)
        for raw_id, reason in skipped.items():
            logger.info("default_member_skipped set_id=%s raw_skill_id=%s reason=%s", skill_set_id, raw_id, reason)
        return {int(row["id"]) for row in resolved.values()}

    malformed_center = (
        scope(session.query(SkillSetSkill.id), SkillSetSkill)
        .join(Skill, SkillSetSkill.skill_id == Skill.id)
        .filter(
            SkillSetSkill.skill_set_id == skill_set_id,
            SkillSetSkill.skill_id.notin_(excluded_raw_ids),
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
            SkillSetSkill.skill_id.notin_(excluded_raw_ids),
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
            SkillSetSkill.skill_id.notin_(excluded_raw_ids),
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

    def _require_unique_runtime_names(
        self,
        session,
        *,
        bot_id: str,
        owner_id: str,
        candidate_ids: set[int],
        retired_ids: set[int] | None = None,
        extra_ids: set[int] | None = None,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> None:
        from agentclaw.community.core.repository.implementations.skill_center.runtime_name_admission import (
            require_unique_runtime_names,
        )

        effective: set[int] = set()
        for row in self._bot_sets(
            session, bot_id=bot_id, owner_id=owner_id,
            engine_type=engine_type, default_engine_types=default_engine_types,
        ):
            if not (row.is_default or row.is_active):
                continue
            excluded = default_exclusions.excluded_skill_ids(
                session, bot_id=bot_id, owner_id=owner_id, set_id=int(row.id)
            ) if row.is_default else set()
            effective.update(set_member_skill_ids(
                self._scope, session, skill_set_id=int(row.id),
                excluded_raw_ids=frozenset(excluded),
            ))
        others = ((self._installations(session, bot_id, owner_id) | effective)
                  - (retired_ids or set())) | (extra_ids or set())
        require_unique_runtime_names(
            session, self._scope, candidates=candidate_ids, others=others
        )


    def _member_ids(self, session, set_ids: set[int]) -> set[int]:
        """Resolve the same Skill identities used by installation repair."""
        ids: set[int] = set()
        for set_id in sorted(set_ids):
            ids |= set_member_skill_ids(self._scope, session, skill_set_id=set_id)
        return ids

    def _teardown_ids(self, session, set_ids: set[int]) -> set[int]:
        """Include unresolved historical members when removing Installations."""
        ids = self._member_ids(session, set_ids)
        if not set_ids:
            return ids
        return ids | {
            int(value[0])
            for value in self._scope(
                session.query(SkillSetSkill.skill_id), SkillSetSkill
            )
            .filter(SkillSetSkill.skill_set_id.in_(sorted(set_ids)))
            .all()
        }

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

        Removed Default members release only recorded Default installations.
        Direct intent and historical unknown provenance are never inferred
        from current membership. Active ordinary claims continue to win.
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
                env=env,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
            )
            self._validate_default_dependencies(
                session, bot_id=bot_id, owner_id=owner_id, engine_type=engine_type,
                default_engine_types=default_engine_types, locked=False,
                retired_ids=plan.skills_to_uninstall,
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
            lock_bot_capabilities(session, bot_id=bot_id, entity_id=owner_id, env=env)
            # Lock every existing Default candidate, including currently offline
            # or unpublished assets, before taking the transactional snapshot.
            # Publication and offlining take the same Skill row lock.
            rows = self._bot_sets(
                session, bot_id=bot_id, owner_id=owner_id, engine_type=engine_type,
                default_engine_types=default_engine_types, locked=True,
            )
            self._lock_default_candidates(session, rows)
            plan = plan_resolver(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                env=env,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
                locked=True,
            )
            self._validate_default_dependencies(
                session, bot_id=bot_id, owner_id=owner_id, engine_type=engine_type,
                default_engine_types=default_engine_types, locked=True,
                retired_ids=plan.skills_to_uninstall,
            )
            verified = plan_resolver(
                session, bot_id=bot_id, owner_id=owner_id, env=env,
                engine_type=engine_type, default_engine_types=default_engine_types,
                locked=True,
            )
            if verified != plan:
                raise SkillSetControlPlaneConflictError("DEFAULT_SUPPLY_CHANGED")
            installed = skill_installations.installed_ids(
                session, bot_id=bot_id, owner_id=owner_id, env=env, locked=True
            )
            for skill_id in sorted(plan.skills_to_install - installed):
                kind, set_id = self._skill_installation_source(
                    session, bot_id=bot_id, owner_id=owner_id, skill_id=skill_id,
                    engine_type=engine_type, default_engine_types=default_engine_types,
                )
                skill_installations.install(
                    session, bot_id=bot_id, owner_id=owner_id, env=env,
                    skill_id=skill_id, source_kind=kind, source_set_id=set_id,
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

    def _lock_default_candidates(self, session, rows):
        default_set_ids = {int(row.id) for row in rows if row.is_default}
        members = self._scope(session.query(SkillSetSkill), SkillSetSkill).filter(
            SkillSetSkill.skill_set_id.in_(default_set_ids)
        ).order_by(SkillSetSkill.skill_set_id, SkillSetSkill.skill_id).with_for_update().all()
        raw_ids = {int(member.skill_id) for member in members}
        uuids = {member.skill_uuid for member in members if member.skill_uuid}
        self._scope(session.query(Skill), Skill).filter(
            or_(Skill.id.in_(raw_ids), Skill.skill_uuid.in_(uuids))
        ).order_by(Skill.id).with_for_update().all()

    def _validate_default_dependencies(
        self, session, *, bot_id, owner_id, engine_type, default_engine_types, locked, retired_ids
    ):
        """Validate effective Default dependencies before reconciling installations."""
        default_ids: set[int] = set()
        bot_sets = self._bot_sets(
            session, bot_id=bot_id, owner_id=owner_id, engine_type=engine_type,
            default_engine_types=default_engine_types,
        )
        for row in bot_sets:
            if not row.is_default:
                continue
            excluded = default_exclusions.excluded_skill_ids(
                session, bot_id=bot_id, owner_id=owner_id, set_id=int(row.id)
            )
            default_ids.update(set_member_skill_ids(
                self._scope, session, skill_set_id=int(row.id),
                excluded_raw_ids=frozenset(excluded),
            ))
        if not default_ids:
            return
        query = self._scope(session.query(Skill), Skill).filter(
            Skill.id.in_(default_ids)
        ).order_by(Skill.id)
        skills = (query.with_for_update() if locked else query).all()
        if {int(skill.id) for skill in skills} != default_ids or any(
            skill.offline_at is not None or str(skill.status or "").upper() in {"OFFLINE", "DELETED"}
            for skill in skills
        ):
            raise SkillSetControlPlaneConflictError("DEFAULT_SUPPLY_CHANGED")
        direct_ids = {skill_id for skill_id, (kind, _) in skill_installations.sources(
            session, bot_id=bot_id, owner_id=owner_id, env=get_current_env(), locked=locked
        ).items() if kind == SkillInstallationSourceKind.DIRECT}
        center_uuids = {skill.skill_uuid for skill in skills
                        if str(skill.git_path or "").startswith("center://") and skill.skill_uuid}
        other_direct_ids = direct_ids - default_ids
        if center_uuids and other_direct_ids and self._scope(
            session.query(Skill.id), Skill
        ).filter(Skill.id.in_(other_direct_ids), Skill.git_path.like("center://%"),
                 Skill.skill_uuid.in_(center_uuids)).first():
            raise SkillSetControlPlaneConflictError("RESOURCE_DIRECT_ACTIVE")
        ordinary_facts = self._apply_facts(session, [row for row in bot_sets if not row.is_default])
        if any(self._apply_set_owns_skill(fact, skill, frozenset())
               for fact in ordinary_facts for skill in skills):
            raise SkillSetControlPlaneConflictError("RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET")
        self._require_unique_runtime_names(
            session, bot_id=bot_id, owner_id=owner_id, candidate_ids=default_ids,
            retired_ids=set(retired_ids), engine_type=engine_type,
            default_engine_types=default_engine_types,
        )
        center_ids = set()
        for skill in skills:
            if str(skill.git_path or "").startswith("center://"):
                center_ids.add(int(skill.id))
            else:
                skill_mcp_dependency_codes(skill)
        versions = _latest_published_versions(
            self._scope, session, center_ids, metadata=True, locked=locked
        )
        if {int(row[0]) for row in versions} != center_ids:
            raise ValueError("Center Skill has no PUBLISHED Version")
        for _, number, name, metadata in versions:
            if not number or not name or not metadata:
                raise ValueError("Center Skill has no available PUBLISHED Version")
            codes = mcp_dependency_codes_from_version_metadata(metadata)
            if any(not code for code in codes):
                raise ValueError("Skill Version has an empty MCP dependency code")

    def _skill_installation_source(
        self, session, *, bot_id: str, owner_id: str, skill_id: int,
        engine_type: str | None, default_engine_types: tuple[str, ...] | None,
    ) -> tuple[SkillInstallationSourceKind, int]:
        for row in self._bot_sets(
            session, bot_id=bot_id, owner_id=owner_id, engine_type=engine_type,
            default_engine_types=default_engine_types,
        ):
            if not (row.is_default or row.is_active):
                continue
            excluded_ids = default_exclusions.excluded_skill_ids(
                session, bot_id=bot_id, owner_id=owner_id, set_id=int(row.id)
            ) if row.is_default else set()
            if skill_id in set_member_skill_ids(self._scope, session,
                skill_set_id=int(row.id), excluded_raw_ids=frozenset(excluded_ids)):
                return (
                    SkillInstallationSourceKind.DEFAULT if row.is_default
                    else SkillInstallationSourceKind.SKILL_SET,
                    int(row.id),
                )
        raise ValueError("installation plan has no active source")

    @staticmethod
    def _default_withdrawals(session, *, bot_id, owner_id, active_ids, env: str, locked: bool) -> set[int]:
        return {
            skill_id for skill_id, (kind, _) in skill_installations.sources(
                session, bot_id=bot_id, owner_id=owner_id, env=env, locked=locked,
            ).items()
            if kind == SkillInstallationSourceKind.DEFAULT and skill_id not in active_ids
        }

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
                excluded = default_exclusions.excluded_skill_ids(
                    session, bot_id=bot_id, owner_id=owner_id, set_id=int(row.id)
                ) if row.is_default else set()
                ids = set_member_skill_ids(self._scope, session,
                    skill_set_id=int(row.id), excluded_raw_ids=frozenset(excluded))
                members |= ids
            return frozenset(members)

    def _resolve_default_sync_plan(
        self,
        session,
        *,
        bot_id: str,
        owner_id: str,
        env: str,
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
        installed_skills = skill_installations.installed_ids(
            session, bot_id=bot_id, owner_id=owner_id, env=env
        )
        installed_mcps = mcp_installations.installed_codes(
            session, bot_id=bot_id, owner_id=owner_id, env=env
        )
        for row in bot_sets:
            raw_excluded = default_exclusions.excluded_skill_ids(
                session, bot_id=bot_id, owner_id=owner_id, set_id=int(row.id)
            ) if row.is_default else set()
            skill_ids = set_member_skill_ids(
                self._scope, session, skill_set_id=int(row.id),
                excluded_raw_ids=frozenset(raw_excluded),
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
            raw_excluded_members = {int(value[0]) for value in self._scope(session.query(SkillSetSkill.skill_id), SkillSetSkill).filter(SkillSetSkill.skill_set_id == row.id, SkillSetSkill.skill_id.in_(excluded_skill_ids)).all()}
            default_skills |= skill_ids
            excluded_skills |= raw_excluded_members - installed_skills
            default_mcps |= mcp_codes - excluded_mcp_codes
            excluded_mcps |= (mcp_codes & excluded_mcp_codes) - installed_mcps
        return InstallationFlushPlan(
            member_skill_ids=frozenset(default_skills),
            skills_to_install=frozenset(default_skills),
            skills_to_uninstall=frozenset(
                (excluded_skills - ordinary_active_skills)
                | self._default_withdrawals(
                    session, bot_id=bot_id, owner_id=owner_id,
                    active_ids=default_skills | ordinary_active_skills, env=env, locked=locked,
                )
            ),
            mcps_to_install=frozenset(default_mcps),
            mcps_to_uninstall=frozenset(excluded_mcps - ordinary_active_mcps),
        )

    def _resolve_flush_plan(
        self,
        session,
        *,
        bot_id: str,
        owner_id: str,
        env: str,
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
        installed_skills = skill_installations.installed_ids(
            session, bot_id=bot_id, owner_id=owner_id, env=env
        )
        installed_mcps = mcp_installations.installed_codes(
            session, bot_id=bot_id, owner_id=owner_id, env=env
        )
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
            raw_excluded_members = {int(value[0]) for value in self._scope(session.query(SkillSetSkill.skill_id), SkillSetSkill).filter(SkillSetSkill.skill_set_id == row.id, SkillSetSkill.skill_id.in_(excluded)).all()}
            inactive_skills |= raw_excluded_members - installed_skills
            for member_id in set_member_skill_ids(
                self._scope, session, skill_set_id=int(row.id),
                excluded_raw_ids=frozenset(excluded),
            ):
                # Raw exclusions were applied before identity resolution. A
                # different raw key can equal this resolved asset's ID.
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
                    if server_code not in installed_mcps:
                        inactive_mcps.add(server_code)
                    continue
                (active_mcps if set_is_active else inactive_mcps).add(server_code)
        return InstallationFlushPlan(
            member_skill_ids=frozenset(members),
            skills_to_install=frozenset(active_skills),
            # An active claim wins: R3 keeps a capability in one Set, so on
            # historical malformed two-Set data the flush errs safe and never
            # uninstalls a member a live Set accounts for.
            skills_to_uninstall=frozenset(
                (inactive_skills - active_skills)
                | self._default_withdrawals(
                    session, bot_id=bot_id, owner_id=owner_id, active_ids=active_skills, env=env, locked=locked,
                )
            ),
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
