"""Transactional persistence commands for canonical Bot SkillSets.

Each mutation deliberately owns one ``transactional_orm_session``.  Calling
the historical per-row repositories here would open independent sessions and
would make a SkillSet only *eventually* atomic.
"""

from __future__ import annotations

from injector import inject
from sqlalchemy import or_
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
from agentclaw.community.core.repository.protocols.skill_set_control_plane import (
    SkillSetControlPlaneRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.skill_installation import (
    SkillInstallationRepositoryProtocol,
)
from agentclaw.community.core.repository.implementations.skill_center.installation import (
    SkillInstallationRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.mcp_skill_set_control_plane import (
    McpSkillSetControlPlaneCommands,
)
from agentclaw.community.core.repository.skill_set_control_plane_types import (
    SkillSetDesiredState,
    SkillSetMutation,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


from agentclaw.community.core.skill_center.errors import (
    SkillRuntimeNameConflictError,
    SkillSetControlPlaneConflictError,
    SkillSetControlPlaneNotFoundError,
)


def _item(row: SkillSet) -> dict:
    return {
        "id": str(row.id),
        "name": row.name,
        "description": row.description,
        "is_default": bool(row.is_default),
        "is_builtin": bool(row.is_builtin),
        # System Default is a platform projection, not mutable desired state.
        # Historical rows may contain false from the old storage model, but the
        # public contract must always expose Default as active.
        "is_active": True if row.is_default else bool(row.is_active),
        "user_id": row.user_id,
        "bolt_id": row.bolt_id,
        "engine_type": row.engine_type,
        "gmt_created": row.gmt_created.isoformat() if row.gmt_created else "",
        "gmt_modified": row.gmt_modified.isoformat() if row.gmt_modified else "",
        "env": row.env,
        "type": "default" if row.is_default else "custom",
    }


class SkillSetControlPlaneRepository(
    McpSkillSetControlPlaneCommands, SkillSetControlPlaneRepositoryProtocol
):
    """Desired-state UoW for SkillSet Membership and Installations."""

    @inject
    def __init__(
        self,
        db: DatabasePlugin,
        installation_repository: SkillInstallationRepositoryProtocol | None = None,
    ) -> None:
        self._db = db
        # Direct construction remains a supported test seam; the fallback
        # preserves the same Install-or-already-present repository semantics.
        self._installation_repository = (
            installation_repository or SkillInstallationRepository(db)
        )

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
    ) -> list[dict]:
        with self._db.orm_session() as session:
            query = self._scope(session.query(SkillSet), SkillSet).filter(
                SkillSet.bolt_id == bot_id,
                SkillSet.user_id == owner_id,
            )
            if engine_type is not None:
                query = query.filter(SkillSet.engine_type == engine_type)
            rows = query.order_by(SkillSet.is_default.desc(), SkillSet.id).all()
            return [_item(row) for row in rows]

    def ensure_active_skillset_installations(
        self,
        *,
        bot_id: str,
        owner_id: str,
        engine_type: str | None = None,
    ) -> int:
        """Insert only missing rows for legacy active ordinary SkillSet members.

        The canonical mutation commands already keep Installation rows in sync.
        This is intentionally a narrow cutover repair: it never reads the
        Default Set, never removes rows, and never changes existing Direct
        desired state.
        """
        with self._db.orm_session() as session:
            sets = self._scope(session.query(SkillSet), SkillSet).filter(
                SkillSet.bolt_id == bot_id,
                SkillSet.user_id == owner_id,
                SkillSet.is_default.is_(False),
                SkillSet.is_active.is_(True),
            )
            if engine_type is not None:
                sets = sets.filter(SkillSet.engine_type == engine_type)
            active_set_ids = {int(row.id) for row in sets.all()}
            if not active_set_ids:
                return 0

            member_ids = {
                int(row.skill_id)
                for row in self._scope(
                    session.query(SkillSetSkill), SkillSetSkill
                )
                .filter(SkillSetSkill.skill_set_id.in_(active_set_ids))
                .all()
            }
            if not member_ids:
                return 0

        return sum(
            self._installation_repository.install(
                env=get_current_env(),
                owner_id=owner_id,
                bot_id=bot_id,
                skill_id=skill_id,
            )
            for skill_id in member_ids
        )

    def get_set(
        self, *, bot_id: str, set_id: str, engine_type: str | None = None
    ) -> dict:
        with self._db.orm_session() as session:
            row = self._set(
                session, bot_id=bot_id, set_id=set_id, engine_type=engine_type
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
                .filter(SkillSet.bolt_id == bot_id, SkillSet.name == name)
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
                is_active=False,
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
        set_id: str,
        name: str | None,
        description: str | None,
        engine_type: str | None = None,
    ) -> dict:
        with self._db.transactional_orm_session() as session:
            row = self._set(
                session,
                bot_id=bot_id,
                set_id=set_id,
                engine_type=engine_type,
                locked=True,
            )
            if row.is_default and name is not None:
                raise SkillSetControlPlaneConflictError("SYSTEM_DEFAULT_IMMUTABLE")
            if name is not None and name != row.name:
                duplicate = (
                    self._scope(session.query(SkillSet), SkillSet)
                    .filter(
                        SkillSet.bolt_id == bot_id,
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
        self, *, bot_id: str, set_id: str, engine_type: str | None = None
    ) -> None:
        with self._db.transactional_orm_session() as session:
            row = self._set(
                session,
                bot_id=bot_id,
                set_id=set_id,
                engine_type=engine_type,
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
        self, *, bot_id: str, set_id: str, engine_type: str | None = None
    ) -> list[dict]:
        with self._db.orm_session() as session:
            row = self._set(
                session, bot_id=bot_id, set_id=set_id, engine_type=engine_type
            )
            rows = (
                self._scope(session.query(Skill), Skill)
                .join(SkillSetSkill, SkillSetSkill.skill_id == Skill.id)
                .filter(SkillSetSkill.skill_set_id == row.id)
                .order_by(Skill.id)
                .all()
            )
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
    ) -> SkillSetMutation:
        with self._db.transactional_orm_session() as session:
            row = self._set(
                session,
                bot_id=bot_id,
                set_id=set_id,
                engine_type=engine_type,
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
                return SkillSetMutation(_item(row), False, old)
            # An installed skill without an ordinary Membership is Direct-active.
            if skill.id in old.installations:
                raise SkillSetControlPlaneConflictError("RESOURCE_DIRECT_ACTIVE")
            owner = (
                self._scope(session.query(SkillSet), SkillSet)
                .join(SkillSetSkill, SkillSetSkill.skill_set_id == SkillSet.id)
                .filter(
                    SkillSet.bolt_id == bot_id,
                    SkillSet.is_default.is_(False),
                    SkillSetSkill.skill_id == skill.id,
                )
                .first()
            )
            if owner is not None:
                raise SkillSetControlPlaneConflictError(
                    "RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET"
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
                    user_id=row.user_id,
                    env=get_current_env(),
                    avernet_tenant=get_current_avernet_tenant(),
                )
            )
            if row.is_active:
                session.add(
                    BotSkillInstallation(
                        bot_id=bot_id,
                        owner_id=owner_id,
                        skill_id=skill.id,
                        env=get_current_env(),
                        avernet_tenant=get_current_avernet_tenant(),
                    )
                )
            session.flush()
            return SkillSetMutation(_item(row), True, old)

    def remove_skill(
        self,
        *,
        bot_id: str,
        owner_id: str,
        set_id: str,
        skill_id: str,
        engine_type: str | None = None,
    ) -> SkillSetMutation:
        with self._db.transactional_orm_session() as session:
            row = self._set(
                session,
                bot_id=bot_id,
                set_id=set_id,
                engine_type=engine_type,
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
                return SkillSetMutation(_item(row), False, old)
            session.delete(membership)
            if row.is_active:
                self._scope(
                    session.query(BotSkillInstallation), BotSkillInstallation
                ).filter(
                    BotSkillInstallation.owner_id == owner_id,
                    BotSkillInstallation.bot_id == bot_id,
                    BotSkillInstallation.skill_id == int(skill_id),
                ).delete(synchronize_session=False)
            session.flush()
            return SkillSetMutation(_item(row), True, old)

    def set_active(
        self,
        *,
        bot_id: str,
        owner_id: str,
        set_id: str,
        active: bool,
        engine_type: str | None = None,
    ) -> SkillSetMutation:
        with self._db.transactional_orm_session() as session:
            row = self._set(
                session,
                bot_id=bot_id,
                set_id=set_id,
                engine_type=engine_type,
                locked=True,
            )
            if row.is_default:
                if not active:
                    raise SkillSetControlPlaneConflictError("SYSTEM_DEFAULT_IMMUTABLE")
                return SkillSetMutation(
                    _item(row),
                    False,
                    self._snapshot(
                        session, bot_id, owner_id, engine_type=engine_type
                    ),
                )
            old = self._snapshot(
                session, bot_id, owner_id, engine_type=engine_type
            )
            members = (
                self._scope(session.query(SkillSetSkill), SkillSetSkill)
                .filter(SkillSetSkill.skill_set_id == row.id)
                .with_for_update()
                .all()
            )
            ids = {int(member.skill_id) for member in members}
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
                existing = self._installations(session, bot_id, owner_id)
                for skill_id in ids - existing:
                    session.add(
                        BotSkillInstallation(
                            bot_id=bot_id,
                            owner_id=owner_id,
                            skill_id=skill_id,
                            env=get_current_env(),
                            avernet_tenant=get_current_avernet_tenant(),
                        )
                    )
                existing_mcps = self._mcp_installations(session, bot_id, owner_id)
                for server_code in mcp_codes - existing_mcps:
                    session.add(
                        BotMCPInstallation(
                            bot_id=bot_id,
                            owner_id=owner_id,
                            server_code=server_code,
                            env=get_current_env(),
                            avernet_tenant=get_current_avernet_tenant(),
                        )
                    )
            elif ids:
                self._scope(
                    session.query(BotSkillInstallation), BotSkillInstallation
                ).filter(
                    BotSkillInstallation.owner_id == owner_id,
                    BotSkillInstallation.bot_id == bot_id,
                    BotSkillInstallation.skill_id.in_(ids),
                ).delete(synchronize_session=False)
            if not active and mcp_codes:
                self._scope(
                    session.query(BotMCPInstallation), BotMCPInstallation
                ).filter(
                    BotMCPInstallation.bot_id == bot_id,
                    BotMCPInstallation.owner_id == owner_id,
                    BotMCPInstallation.server_code.in_(mcp_codes),
                ).delete(synchronize_session=False)
            session.flush()
            return SkillSetMutation(_item(row), changed, old)

    def replace_active_set(
        self,
        *,
        bot_id: str,
        owner_id: str,
        set_id: str,
        engine_type: str | None = None,
    ) -> SkillSetMutation:
        """Atomically replace all ordinary active sets with ``set_id``.

        This is deliberately distinct from canonical ``activate``.  It exists
        only for the deprecated single-select switch wire, whose published
        operation is an all-or-nothing replacement rather than a sequence of
        deactivate/activate calls.
        """
        with self._db.transactional_orm_session() as session:
            target = self._set(
                session,
                bot_id=bot_id,
                set_id=set_id,
                engine_type=engine_type,
                locked=True,
            )
            self._ordinary(target)
            old = self._snapshot(
                session, bot_id, owner_id, engine_type=engine_type
            )
            query = self._scope(session.query(SkillSet), SkillSet).filter(
                SkillSet.bolt_id == bot_id, SkillSet.is_default.is_(False)
            )
            if engine_type is not None:
                query = query.filter(SkillSet.engine_type == engine_type)
            sets = query.with_for_update().all()
            set_ids = {int(row.id) for row in sets}
            memberships = []
            if set_ids:
                memberships = (
                    self._scope(session.query(SkillSetSkill), SkillSetSkill)
                    .filter(SkillSetSkill.skill_set_id.in_(set_ids))
                    .with_for_update()
                    .all()
                )
                mcp_memberships = (
                    self._scope(
                        session.query(SkillSetMCPServer), SkillSetMCPServer
                    )
                    .filter(SkillSetMCPServer.skill_set_id.in_(set_ids))
                    .with_for_update()
                    .all()
                )
            else:
                mcp_memberships = []
            active_member_ids = {
                int(member.skill_id)
                for member in memberships
                if old.set_active.get(int(member.skill_set_id), False)
            }
            target_member_ids = {
                int(member.skill_id)
                for member in memberships
                if int(member.skill_set_id) == int(target.id)
            }
            active_mcp_codes = {
                str(member.server_code)
                for member in mcp_memberships
                if old.set_active.get(int(member.skill_set_id), False)
            }
            target_mcp_codes = {
                str(member.server_code)
                for member in mcp_memberships
                if int(member.skill_set_id) == int(target.id)
            }
            for row in sets:
                row.is_active = int(row.id) == int(target.id)
            self._require_unique_runtime_names(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                candidate_ids=target_member_ids,
                retired_ids=active_member_ids,
            )
            if active_member_ids:
                self._scope(
                    session.query(BotSkillInstallation), BotSkillInstallation
                ).filter(
                    BotSkillInstallation.owner_id == owner_id,
                    BotSkillInstallation.bot_id == bot_id,
                    BotSkillInstallation.skill_id.in_(active_member_ids),
                ).delete(synchronize_session=False)
            existing = self._installations(session, bot_id, owner_id)
            for skill_id in target_member_ids - existing:
                session.add(
                    BotSkillInstallation(
                        bot_id=bot_id,
                        owner_id=owner_id,
                        skill_id=skill_id,
                        env=get_current_env(),
                        avernet_tenant=get_current_avernet_tenant(),
                    )
                )
            if active_mcp_codes:
                self._scope(
                    session.query(BotMCPInstallation), BotMCPInstallation
                ).filter(
                    BotMCPInstallation.bot_id == bot_id,
                    BotMCPInstallation.owner_id == owner_id,
                    BotMCPInstallation.server_code.in_(active_mcp_codes),
                ).delete(synchronize_session=False)
            existing_mcps = self._mcp_installations(session, bot_id, owner_id)
            for server_code in target_mcp_codes - existing_mcps:
                session.add(
                    BotMCPInstallation(
                        bot_id=bot_id,
                        owner_id=owner_id,
                        server_code=server_code,
                        env=get_current_env(),
                        avernet_tenant=get_current_avernet_tenant(),
                    )
                )
            session.flush()
            activated = (
                [str(target.id)]
                if not old.set_active.get(int(target.id), False)
                else []
            )
            deactivated = [
                str(row.id)
                for row in sets
                if int(row.id) != int(target.id)
                and old.set_active.get(int(row.id), False)
            ]
            changed = any(
                old.set_active.get(int(row.id), False)
                != (int(row.id) == int(target.id))
                for row in sets
            )
            return SkillSetMutation(
                _item(target),
                changed,
                old,
                {"activated": activated, "deactivated": deactivated},
            )

    def restore_desired_state(
        self,
        *,
        bot_id: str,
        owner_id: str,
        state: SkillSetDesiredState,
        engine_type: str | None = None,
    ) -> None:
        """Atomically restore Membership, set-state and Installation facts."""
        with self._db.transactional_orm_session() as session:
            query = self._scope(session.query(SkillSet), SkillSet).filter(
                SkillSet.bolt_id == bot_id, SkillSet.is_default.is_(False)
            )
            if engine_type is not None:
                query = query.filter(SkillSet.engine_type == engine_type)
            current_sets = query.with_for_update().all()
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
            for set_id, server_codes in state.mcp_memberships.items():
                set_row = next((row for row in current_sets if int(row.id) == set_id), None)
                if set_row is None:
                    continue
                for server_code in server_codes:
                    session.add(
                        SkillSetMCPServer(
                            skill_set_id=set_id,
                            server_code=server_code,
                            name=server_code,
                            user_id=set_row.user_id,
                            env=get_current_env(),
                            avernet_tenant=get_current_avernet_tenant(),
                        )
                    )
            self._scope(
                session.query(BotSkillInstallation), BotSkillInstallation
            ).filter(
                BotSkillInstallation.owner_id == owner_id,
                BotSkillInstallation.bot_id == bot_id,
            ).delete(
                synchronize_session=False
            )
            session.flush()
            for skill_id in state.installations:
                session.add(
                    BotSkillInstallation(
                        bot_id=bot_id,
                        owner_id=owner_id,
                        skill_id=skill_id,
                        env=get_current_env(),
                        avernet_tenant=get_current_avernet_tenant(),
                    )
                )
            self._scope(
                session.query(BotMCPInstallation), BotMCPInstallation
            ).filter(
                BotMCPInstallation.bot_id == bot_id,
                BotMCPInstallation.owner_id == owner_id,
            ).delete(
                synchronize_session=False
            )
            for server_code in state.mcp_installations:
                session.add(
                    BotMCPInstallation(
                        bot_id=bot_id,
                        owner_id=owner_id,
                        server_code=server_code,
                        env=get_current_env(),
                        avernet_tenant=get_current_avernet_tenant(),
                    )
                )
            session.flush()

    def snapshot_desired_state(
        self, *, bot_id: str, owner_id: str, engine_type: str | None = None
    ) -> SkillSetDesiredState:
        with self._db.orm_session() as session:
            return self._snapshot(
                session, bot_id, owner_id, engine_type=engine_type
            )

    def _set(
        self,
        session,
        *,
        bot_id: str,
        set_id: str,
        engine_type: str | None = None,
        locked: bool = False,
    ) -> SkillSet:
        query = self._scope(session.query(SkillSet), SkillSet).filter(
            SkillSet.id == int(set_id), SkillSet.bolt_id == bot_id
        )
        if engine_type is not None:
            query = query.filter(SkillSet.engine_type == engine_type)
        if locked:
            query = query.with_for_update()
        row = query.one_or_none()
        if row is None:
            raise SkillSetControlPlaneNotFoundError()
        return row

    @staticmethod
    def _ordinary(row: SkillSet) -> None:
        if row.is_default:
            raise SkillSetControlPlaneConflictError("SYSTEM_DEFAULT_IMMUTABLE")

    def _installations(self, session, bot_id: str, owner_id: str) -> set[int]:
        return {
            int(value[0])
            for value in self._scope(
                session.query(BotSkillInstallation.skill_id), BotSkillInstallation
            )
            .filter(
                BotSkillInstallation.owner_id == owner_id,
                BotSkillInstallation.bot_id == bot_id,
            )
            .all()
        }

    def _mcp_installations(self, session, bot_id: str, owner_id: str) -> set[str]:
        return {
            str(value[0])
            for value in self._scope(
                session.query(BotMCPInstallation.server_code), BotMCPInstallation
            )
            .filter(
                BotMCPInstallation.bot_id == bot_id,
                BotMCPInstallation.owner_id == owner_id,
            )
            .all()
        }

    def _mcp_has_ordinary_membership(
        self, session, bot_id: str, owner_id: str, server_code: str
    ) -> bool:
        return (
            self._scope(session.query(SkillSetMCPServer), SkillSetMCPServer)
            .join(SkillSet, SkillSet.id == SkillSetMCPServer.skill_set_id)
            .filter(
                SkillSet.bolt_id == bot_id,
                SkillSet.is_default.is_(False),
                SkillSetMCPServer.server_code == server_code,
            )
            .first()
            is not None
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
    ) -> SkillSetDesiredState:
        """Lock and capture every ordinary-set desired fact for this Bot."""
        query = self._scope(session.query(SkillSet), SkillSet).filter(
            SkillSet.bolt_id == bot_id,
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
        mcp_memberships: dict[int, list[str]] = {set_id: [] for set_id in set_ids}
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
                    str(member.server_code)
                )
        return SkillSetDesiredState(
            installations=self._installations(session, bot_id, owner_id),
            set_active={int(row.id): bool(row.is_active) for row in sets},
            memberships={set_id: tuple(items) for set_id, items in memberships.items()},
            mcp_installations=self._mcp_installations(session, bot_id, owner_id),
            mcp_memberships={
                set_id: tuple(items) for set_id, items in mcp_memberships.items()
            },
        )
