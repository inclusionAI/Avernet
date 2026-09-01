"""Transactional persistence commands for canonical Bot SkillSets.

Each mutation deliberately owns one ``transactional_orm_session``.  Calling
the historical per-row repositories here would open independent sessions and
would make a SkillSet only *eventually* atomic.
"""

from __future__ import annotations

from injector import inject
from sqlalchemy import and_, or_
from agentclaw.community.core.models.skill import (
    Skill,
    SkillSet,
    SkillSetSkill,
)
from agentclaw.community.core.models.mcp import (
    SkillSetMCPServer,
)
from agentclaw.community.core.repository.implementations.skill_center.default_skillset_projection import (
    global_default_scope,
)
from agentclaw.community.core.repository.implementations.skill_center.tables import (
    default_exclusions,
    mcp_installations,
    skill_installations,
)
from agentclaw.community.core.repository.implementations.skill_center.skill_set_projection import (
    skill_set_item as _item,
)
from agentclaw.community.core.repository.protocols.capability_desired_state import (
    CapabilityDesiredStateRepositoryProtocol,
)
from agentclaw.community.core.repository.implementations.skill_center.bot_skillset_installations import (
    BotSkillSetInstallations,
    CENTER_MEMBERSHIP_IDENTITY_MISSING,
    center_membership_skill_uuid,
    set_member_skill_ids,
)
from agentclaw.community.core.repository.implementations.skill_center.default_exclusion_commands import (
    DefaultExclusionCommands,
)
from agentclaw.community.core.repository.implementations.skill_center.direct_installation_commands import (
    DirectInstallationCommands,
)
from agentclaw.community.core.repository.implementations.skill_center.mcp_skill_set_control_plane import (
    McpSkillSetControlPlaneCommands,
)
from agentclaw.community.core.repository.implementations.skill_center.skill_mcp_dependencies import (
    skill_projection_mcp_dependency_codes,
)
from agentclaw.community.core.repository.implementations.skill_center.legacy_skill_set_scope import LegacySkillSetScopeQueries
from agentclaw.community.core.repository.capability_desired_state_types import (
    CapabilityDesiredState,
    DesiredStateMutation,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env

from agentclaw.community.core.skill_center.errors import (
    SkillRuntimeNameConflictError,
    SkillSetControlPlaneConflictError,
    SkillSetControlPlaneNotFoundError,
)
from agentclaw.community.core.skill_center.offline_policy import require_skill_online
from agentclaw.community.core.skill_center.policies.capability_ownership import (
    require_can_join_set,
)


class CapabilityDesiredStateRepository(
    BotSkillSetInstallations,
    DefaultExclusionCommands,
    DirectInstallationCommands,
    LegacySkillSetScopeQueries,
    McpSkillSetControlPlaneCommands,
    CapabilityDesiredStateRepositoryProtocol,
):
    """Desired-state UoW for SkillSet Membership and Installations."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    @staticmethod
    def _as_item(row: SkillSet) -> dict:
        return _item(row)

    @staticmethod
    def _scope(query, model):
        return query.filter(
            model.avernet_tenant == get_current_avernet_tenant(),
            model.env == get_current_env(),
        )

    def list_sets(
        self,
        *,
        bot_id: str,
        owner_id: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> list[dict]:
        with self._db.orm_session() as session:
            owned = self._owned_set_scope(
                bot_id=bot_id, owner_id=owner_id, engine_type=engine_type
            )
            ordinary = self._scope(session.query(SkillSet), SkillSet).filter(
                owned, SkillSet.is_default.is_(False)
            )
            defaults: list[SkillSet] = []
            for candidate in default_engine_types or ((engine_type,) if engine_type else ()):
                defaults = (
                    self._scope(session.query(SkillSet), SkillSet)
                    .filter(global_default_scope((candidate,)))
                    .order_by(SkillSet.id)
                    .all()
                )
                if defaults:
                    break
            rows = [*defaults, *ordinary.order_by(SkillSet.id).all()]
            return [_item(row) for row in rows]

    def get_set(
        self, *, bot_id: str, owner_id: str, set_id: str, engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> dict:
        with self._db.orm_session() as session:
            row = self._set(
                session, bot_id=bot_id, owner_id=owner_id, set_id=set_id, engine_type=engine_type,
                default_engine_types=default_engine_types,
            )
            return _item(row)

    def create_set(
        self,
        *,
        bot_id: str,
        owner_id: str,
        name: str,
        description: str | None,
        engine_type: str | None = None,
    ) -> dict:
        with self._db.transactional_orm_session() as session:
            duplicate = (
                self._scope(session.query(SkillSet), SkillSet)
                .filter(
                    SkillSet.bolt_id == bot_id,
                    SkillSet.user_id == owner_id,
                    SkillSet.name == name,
                )
                .first()
            )
            if duplicate is not None:
                raise SkillSetControlPlaneConflictError("SKILL_SET_NAME_CONFLICT")
            row = SkillSet(
                name=name,
                description=description,
                bolt_id=bot_id,
                user_id=owner_id,
                is_default=False,
                is_builtin=False,
                is_active=True,
                env=get_current_env(),
                avernet_tenant=get_current_avernet_tenant(),
                engine_type=engine_type,
            )
            session.add(row)
            session.flush()
            return _item(row)

    def update_set(
        self,
        *,
        bot_id: str,
        owner_id: str,
        set_id: str,
        name: str | None,
        description: str | None,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> dict:
        with self._db.transactional_orm_session() as session:
            row = self._set(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                set_id=set_id,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
                locked=True,
            )
            if row.is_default:
                raise SkillSetControlPlaneConflictError("SYSTEM_DEFAULT_IMMUTABLE")
            if name is not None and name != row.name:
                duplicate = (
                    self._scope(session.query(SkillSet), SkillSet)
                    .filter(
                        SkillSet.bolt_id == bot_id,
                        SkillSet.user_id == owner_id,
                        SkillSet.name == name,
                        SkillSet.id != row.id,
                    )
                    .first()
                )
                if duplicate is not None:
                    raise SkillSetControlPlaneConflictError("SKILL_SET_NAME_CONFLICT")
                row.name = name
            if description is not None:
                row.description = description
            session.flush()
            return _item(row)

    def delete_set(
        self, *, bot_id: str, owner_id: str, set_id: str, engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> None:
        with self._db.transactional_orm_session() as session:
            row = self._set(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                set_id=set_id,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
                locked=True,
            )
            if row.is_default:
                raise SkillSetControlPlaneConflictError("SYSTEM_DEFAULT_IMMUTABLE")
            if row.is_active:
                raise SkillSetControlPlaneConflictError("SKILL_SET_ACTIVE")
            self._scope(session.query(SkillSetSkill), SkillSetSkill).filter(
                SkillSetSkill.skill_set_id == row.id
            ).delete(synchronize_session=False)
            self._scope(session.query(SkillSetMCPServer), SkillSetMCPServer).filter(
                SkillSetMCPServer.skill_set_id == row.id
            ).delete(synchronize_session=False)
            session.delete(row)

    def list_skills(
        self, *, bot_id: str, owner_id: str, set_id: str, engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> list[dict]:
        with self._db.orm_session() as session:
            row = self._set(
                session, bot_id=bot_id, owner_id=owner_id, set_id=set_id, engine_type=engine_type,
                default_engine_types=default_engine_types,
            )
            rows = (
                self._scope(session.query(Skill), Skill)
                .join(SkillSetSkill, SkillSetSkill.skill_id == Skill.id)
                .filter(SkillSetSkill.skill_set_id == row.id)
                .order_by(Skill.id)
                .all()
            )
            if row.is_default:
                excluded = default_exclusions.excluded_skill_ids(
                    session, bot_id=bot_id, owner_id=owner_id, set_id=int(row.id)
                )
                rows = [skill for skill in rows if int(skill.id) not in excluded]
            return [
                {
                    "id": str(skill.id),
                    "name": skill.name,
                    "description": skill.description,
                    "category": skill.category,
                    "git_path": skill.git_path,
                    "tags": skill.tags if isinstance(skill.tags, list) else [],
                    "status": skill.status,
                    "version": skill.version,
                    "skill_uuid": skill.skill_uuid,
                    "source_type": skill.source_type,
                }
                for skill in rows
            ]

    def resolve_legacy_skill_id(self, *, bot_id: str, identifier: str) -> str:
        """Resolve the historical ID/name/git-path batch wire to a stable ID."""
        with self._db.orm_session() as session:
            query = self._scope(session.query(Skill), Skill)
            if identifier.isdigit():
                query = query.filter(Skill.id == int(identifier))
            else:
                query = query.filter(
                    or_(Skill.bolt_id == bot_id, Skill.bolt_id.is_(None)),
                    or_(
                        Skill.name == identifier,
                        Skill.git_path == identifier,
                        Skill.git_path.endswith(identifier),
                    ),
                )
            skill = query.order_by(Skill.gmt_created.desc(), Skill.id.desc()).first()
            if skill is None or (
                str(skill.git_path or "").startswith("local://")
                and skill.bolt_id != bot_id
            ):
                raise SkillSetControlPlaneNotFoundError()
            return str(skill.id)

    def add_skill(
        self,
        *,
        bot_id: str,
        owner_id: str,
        set_id: str,
        skill_id: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation:
        with self._db.transactional_orm_session() as session:
            row = self._set(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                set_id=set_id,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
                locked=True,
            )
            self._ordinary(row)
            skill = (
                self._scope(session.query(Skill), Skill)
                .filter(Skill.id == int(skill_id))
                .with_for_update()
                .one_or_none()
            )
            if skill is None or (
                str(skill.git_path or "").startswith("local://")
                and skill.bolt_id != bot_id
            ):
                raise SkillSetControlPlaneNotFoundError()
            require_skill_online(skill)
            membership_skill_uuid = center_membership_skill_uuid(skill)
            old = self._snapshot(
                session, bot_id, owner_id, engine_type=engine_type
            )
            current = (
                self._scope(session.query(SkillSetSkill), SkillSetSkill)
                .filter(
                    SkillSetSkill.skill_set_id == row.id,
                    SkillSetSkill.skill_id == skill.id,
                )
                .first()
            )
            if current is not None:
                if (
                    membership_skill_uuid is not None
                    and current.skill_uuid != membership_skill_uuid
                ):
                    raise SkillSetControlPlaneConflictError(
                        CENTER_MEMBERSHIP_IDENTITY_MISSING
                    )
                return DesiredStateMutation(_item(row), False, old)
            # R3 covers ANY of the Bot's Sets — the Default included, its
            # members excluded or not.
            reachable_ids = {
                int(candidate.id)
                for candidate in self._bot_sets(
                    session,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    engine_type=engine_type,
                    default_engine_types=default_engine_types,
                )
            } - {int(row.id)}
            identity = [SkillSetSkill.skill_id == skill.id]
            if skill.skill_uuid:
                identity.append(SkillSetSkill.skill_uuid == skill.skill_uuid)
            membership = (
                self._scope(session.query(SkillSetSkill), SkillSetSkill)
                .filter(
                    SkillSetSkill.skill_set_id.in_(reachable_ids),
                    or_(*identity),
                )
                .first()
                if reachable_ids
                else None
            )
            require_can_join_set(
                is_directly_active=skill.id in old.installations,
                is_in_another_set=membership is not None,
            )
            if row.is_active:
                self._require_unique_runtime_names(
                    session,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    candidate_ids={int(skill.id)},
                )
            session.add(
                SkillSetSkill(
                    skill_set_id=row.id,
                    skill_id=skill.id,
                    skill_uuid=membership_skill_uuid,
                    user_id=row.user_id,
                    env=get_current_env(),
                    avernet_tenant=get_current_avernet_tenant(),
                )
            )
            if row.is_active:
                skill_installations.install(
                    session,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    env=get_current_env(),
                    skill_id=int(skill.id),
                )
            session.flush()
            # This Skill's MCP dependencies are the MCPs the projection is
            # about to add to the Bot's set. Read under the lock this
            # transaction already holds, for the same reason the activate
            # command reads its member codes here: a second, unlocked query
            # could disagree with what was actually installed.
            return DesiredStateMutation(
                _item(row),
                True,
                old,
                mcp_codes=skill_projection_mcp_dependency_codes(
                    session,
                    skill,
                    allow_unresolvable_center=not bool(row.is_active),
                ),
            )

    def remove_skill(
        self,
        *,
        bot_id: str,
        owner_id: str,
        set_id: str,
        skill_id: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation:
        with self._db.transactional_orm_session() as session:
            row = self._set(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                set_id=set_id,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
                locked=True,
            )
            self._ordinary(row)
            old = self._snapshot(
                session, bot_id, owner_id, engine_type=engine_type
            )
            membership = (
                self._scope(session.query(SkillSetSkill), SkillSetSkill)
                .filter(
                    SkillSetSkill.skill_set_id == row.id,
                    SkillSetSkill.skill_id == int(skill_id),
                )
                .first()
            )
            if membership is None:
                return DesiredStateMutation(_item(row), False, old)
            # Read before the delete: after it, nothing links this Set to the
            # Skill whose dependencies the projection is about to drop.
            skill = (
                self._scope(session.query(Skill), Skill)
                .filter(Skill.id == int(skill_id))
                .with_for_update()
                .one_or_none()
            )
            # The difference is what this membership was providing.
            before = self._teardown_ids(session, {int(row.id)})
            session.delete(membership)
            session.flush()
            if row.is_active:
                retired = before - self._teardown_ids(session, {int(row.id)})
                skill_installations.uninstall(
                    session,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    env=get_current_env(),
                    skill_ids=retired,
                )
            session.flush()
            # Candidates for release, not a verdict: another Skill or the
            # default policy may still supply the same code, and the projector
            # subtracts the projected set before deleting anything.
            return DesiredStateMutation(
                _item(row),
                True,
                old,
                mcp_codes=skill_projection_mcp_dependency_codes(
                    session, skill, allow_unresolvable_center=True
                ),
            )

    def set_skill_set_active(
        self,
        *,
        bot_id: str,
        owner_id: str,
        set_id: str,
        active: bool,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> DesiredStateMutation:
        with self._db.transactional_orm_session() as session:
            row = self._set(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                set_id=set_id,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
                locked=True,
            )
            if row.is_default:
                if not active:
                    raise SkillSetControlPlaneConflictError("SYSTEM_DEFAULT_IMMUTABLE")
                return DesiredStateMutation(
                    _item(row),
                    False,
                    self._snapshot(
                        session, bot_id, owner_id, engine_type=engine_type
                    ),
                )
            old = self._snapshot(
                session, bot_id, owner_id, engine_type=engine_type
            )
            # Taken for the lock, not the ids.
            self._scope(session.query(SkillSetSkill), SkillSetSkill).filter(
                SkillSetSkill.skill_set_id == row.id
            ).with_for_update().all()
            ids = self._member_ids(session, {int(row.id)})
            retired = self._teardown_ids(session, {int(row.id)})
            mcp_members = (
                self._scope(
                    session.query(SkillSetMCPServer), SkillSetMCPServer
                )
                .filter(SkillSetMCPServer.skill_set_id == row.id)
                .with_for_update()
                .all()
            )
            mcp_codes = {str(member.server_code) for member in mcp_members}
            changed = bool(row.is_active) != active
            row.is_active = active
            if active:
                self._require_unique_runtime_names(
                    session, bot_id=bot_id, owner_id=owner_id, candidate_ids=ids
                )
                for skill_id in sorted(ids):
                    skill_installations.install(
                        session,
                        bot_id=bot_id,
                        owner_id=owner_id,
                        env=get_current_env(),
                        skill_id=skill_id,
                    )
                for server_code in sorted(mcp_codes):
                    mcp_installations.install(
                        session,
                        bot_id=bot_id,
                        owner_id=owner_id,
                        env=get_current_env(),
                        server_code=server_code,
                    )
            else:
                skill_installations.uninstall(
                    session,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    env=get_current_env(),
                    skill_ids=retired,
                )
                mcp_installations.uninstall(
                    session,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    env=get_current_env(),
                    server_codes=mcp_codes,
                )
            session.flush()
            # The projection needs to know which MCPs this Set just claimed
            # or released, and they are only knowable under the row lock this
            # transaction already holds. Returning them here keeps the
            # command from issuing a second, unlocked query that could
            # disagree with what was actually installed.
            return DesiredStateMutation(
                _item(row),
                changed,
                old,
                mcp_codes=frozenset(mcp_codes),
            )

    def restore_desired_state(
        self,
        *,
        bot_id: str,
        owner_id: str,
        state: CapabilityDesiredState,
        engine_type: str | None = None,
    ) -> None:
        """Atomically restore Membership, set-state and Installation facts."""
        with self._db.transactional_orm_session() as session:
            query = self._scope(session.query(SkillSet), SkillSet).filter(
                SkillSet.bolt_id == bot_id,
                SkillSet.user_id == owner_id,
                SkillSet.is_default.is_(False),
            )
            if engine_type is not None:
                query = query.filter(SkillSet.engine_type == engine_type)
            current_sets = query.with_for_update().all()
            restored_skill_ids = set(state.installations)
            restored_skill_ids.update(
                skill_id
                for members in state.memberships.values()
                for skill_id, _user_id, _skill_uuid in members
            )
            # Compensation is another consumption writer: projection failure
            # must not resurrect a Membership/Installation after Offline won.
            # Match every forward writer's lock and take multiple Skills in
            # immutable id order before the first desired-state mutation.
            for skill_id in sorted(restored_skill_ids):
                skill = (
                    self._scope(session.query(Skill), Skill)
                    .filter(Skill.id == skill_id)
                    .with_for_update()
                    .one_or_none()
                )
                if skill is None:
                    raise SkillSetControlPlaneNotFoundError()
                require_skill_online(skill)
            current_ids = {int(row.id) for row in current_sets}
            if current_ids:
                self._scope(session.query(SkillSetSkill), SkillSetSkill).filter(
                    SkillSetSkill.skill_set_id.in_(current_ids)
                ).delete(synchronize_session=False)
                self._scope(
                    session.query(SkillSetMCPServer), SkillSetMCPServer
                ).filter(SkillSetMCPServer.skill_set_id.in_(current_ids)).delete(
                    synchronize_session=False
                )
            for row in current_sets:
                row.is_active = state.set_active.get(int(row.id), False)
            for set_id, members in state.memberships.items():
                for skill_id, user_id, skill_uuid in members:
                    session.add(
                        SkillSetSkill(
                            skill_set_id=set_id,
                            skill_id=skill_id,
                            user_id=user_id,
                            skill_uuid=skill_uuid,
                            env=get_current_env(),
                            avernet_tenant=get_current_avernet_tenant(),
                        )
                    )
            for set_id, members in state.mcp_memberships.items():
                set_row = next((row for row in current_sets if int(row.id) == set_id), None)
                if set_row is None:
                    continue
                for server_code, name, description, icon, member_user_id in members:
                    session.add(
                        SkillSetMCPServer(
                            skill_set_id=set_id,
                            server_code=server_code,
                            name=name,
                            description=description,
                            icon=icon,
                            user_id=member_user_id,
                            env=get_current_env(),
                            avernet_tenant=get_current_avernet_tenant(),
                        )
                    )
            skill_installations.uninstall_all(
                session, bot_id=bot_id, owner_id=owner_id, env=get_current_env()
            )
            session.flush()
            for skill_id in sorted(state.installations):
                skill_installations.install(
                    session,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    env=get_current_env(),
                    skill_id=skill_id,
                )
            mcp_installations.uninstall_all(
                session, bot_id=bot_id, owner_id=owner_id, env=get_current_env()
            )
            for server_code in sorted(state.mcp_installations):
                mcp_installations.install(
                    session,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    env=get_current_env(),
                    server_code=server_code,
                )
            # The exclusion rows are desired state too: the flush treats them
            # as authoritative, so a restore that skipped them would be
            # silently re-applied by the next read.
            default_exclusions.replace_all(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                skill_exclusions=state.skill_exclusions,
                mcp_exclusions=state.mcp_exclusions,
            )
            session.flush()

    def snapshot_desired_state(
        self, *, bot_id: str, owner_id: str, engine_type: str | None = None
    ) -> CapabilityDesiredState:
        with self._db.orm_session() as session:
            return self._snapshot(
                session, bot_id, owner_id, engine_type=engine_type
            )

    def _set(
        self,
        session,
        *,
        bot_id: str,
        owner_id: str,
        set_id: str,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
        locked: bool = False,
    ) -> SkillSet:
        query = self._scope(session.query(SkillSet), SkillSet).filter(
            SkillSet.id == int(set_id),
            self._owned_set_scope(
                bot_id=bot_id, owner_id=owner_id, engine_type=engine_type
            ),
        )
        if locked:
            query = query.with_for_update()
        row = query.one_or_none()
        if row is not None:
            return row

        # A fallback id cannot bypass a higher-priority runtime Default.
        for candidate in default_engine_types or ((engine_type,) if engine_type else ()):
            defaults = self._scope(session.query(SkillSet), SkillSet).filter(
                global_default_scope((candidate,))
            )
            if locked:
                defaults = defaults.with_for_update()
            rows = defaults.order_by(SkillSet.id).all()
            if rows:
                for default in rows:
                    if int(default.id) == int(set_id):
                        return default
                break
        raise SkillSetControlPlaneNotFoundError()

    @staticmethod
    def _owned_set_scope(*, bot_id: str, owner_id: str, engine_type: str | None):
        filters = [SkillSet.bolt_id == bot_id, SkillSet.user_id == owner_id]
        if engine_type is not None:
            filters.append(SkillSet.engine_type == engine_type)
        return and_(*filters)

    @staticmethod
    def _ordinary(row: SkillSet) -> None:
        if row.is_default:
            raise SkillSetControlPlaneConflictError("SYSTEM_DEFAULT_IMMUTABLE")

    def _member_ids(self, session, set_ids: set[int]) -> set[int]:
        """The Skill ids the given Sets provide.

        The same resolver the repair uses, so a mutation writes and removes
        exactly the rows a repair would.
        """
        ids: set[int] = set()
        for set_id in sorted(set_ids):
            ids |= set_member_skill_ids(self._scope, session, skill_set_id=set_id)
        return ids

    def _teardown_ids(self, session, set_ids: set[int]) -> set[int]:
        """Every Skill id these Sets could be holding installed.

        Wider than :meth:`_member_ids` on purpose: a member that no longer
        resolves — an OFFLINE ``center://`` row — still has an Installation row
        to remove, and the guards would not let its owner remove it by hand.
        Install from ``_member_ids``, tear down from here.
        """
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

    @staticmethod
    def _installations(session, bot_id: str, owner_id: str) -> set[int]:
        return skill_installations.installed_ids(
            session, bot_id=bot_id, owner_id=owner_id, env=get_current_env()
        )

    @staticmethod
    def _mcp_installations(session, bot_id: str, owner_id: str) -> set[str]:
        return mcp_installations.installed_codes(
            session, bot_id=bot_id, owner_id=owner_id, env=get_current_env()
        )

    def _require_unique_runtime_names(
        self,
        session,
        *,
        bot_id: str,
        owner_id: str,
        candidate_ids: set[int],
        retired_ids: set[int] | None = None,
    ) -> None:
        """Validate the complete post-command projection before any write."""
        selected_ids = (
            self._installations(session, bot_id, owner_id) - (retired_ids or set())
        ) | candidate_ids
        if not selected_ids:
            return
        rows = (
            self._scope(session.query(Skill.id, Skill.name), Skill)
            .filter(Skill.id.in_(selected_ids))
            .all()
        )
        owner_by_name: dict[str, int] = {}
        for skill_id, name in rows:
            runtime_name = str(name or "")
            existing = owner_by_name.get(runtime_name)
            if existing is not None and existing != int(skill_id):
                raise SkillRuntimeNameConflictError()
            owner_by_name[runtime_name] = int(skill_id)

    def _snapshot(
        self,
        session,
        bot_id: str,
        owner_id: str,
        *,
        engine_type: str | None = None,
    ) -> CapabilityDesiredState:
        """Lock and capture every ordinary-set desired fact for this Bot."""
        query = self._scope(session.query(SkillSet), SkillSet).filter(
            SkillSet.bolt_id == bot_id,
            SkillSet.user_id == owner_id,
            SkillSet.is_default.is_(False),
        )
        if engine_type is not None:
            query = query.filter(SkillSet.engine_type == engine_type)
        sets = query.with_for_update().all()
        set_ids = {int(row.id) for row in sets}
        memberships: dict[int, list[tuple[int, str | None, str | None]]] = {
            set_id: [] for set_id in set_ids
        }
        if set_ids:
            rows = (
                self._scope(session.query(SkillSetSkill), SkillSetSkill)
                .filter(SkillSetSkill.skill_set_id.in_(set_ids))
                .with_for_update()
                .all()
            )
            for member in rows:
                memberships[int(member.skill_set_id)].append(
                    (int(member.skill_id), member.user_id, member.skill_uuid)
                )
        mcp_memberships: dict[
            int, list[tuple[str, str, str | None, str | None, str | None]]
        ] = {set_id: [] for set_id in set_ids}
        if set_ids:
            mcp_rows = (
                self._scope(
                    session.query(SkillSetMCPServer), SkillSetMCPServer
                )
                .filter(SkillSetMCPServer.skill_set_id.in_(set_ids))
                .with_for_update()
                .all()
            )
            for member in mcp_rows:
                mcp_memberships[int(member.skill_set_id)].append(
                    (
                        str(member.server_code),
                        str(member.name),
                        member.description,
                        member.icon,
                        member.user_id,
                    )
                )
        return CapabilityDesiredState(
            installations=self._installations(session, bot_id, owner_id),
            set_active={int(row.id): bool(row.is_active) for row in sets},
            memberships={set_id: tuple(items) for set_id, items in memberships.items()},
            mcp_installations=self._mcp_installations(session, bot_id, owner_id),
            mcp_memberships={
                set_id: tuple(items) for set_id, items in mcp_memberships.items()
            },
            skill_exclusions=frozenset(
                default_exclusions.all_skill_exclusions(
                    session, bot_id=bot_id, owner_id=owner_id
                )
            ),
            mcp_exclusions=frozenset(
                default_exclusions.all_mcp_exclusions(
                    session, bot_id=bot_id, owner_id=owner_id
                )
            ),
        )
