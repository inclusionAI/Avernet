"""Transactional persistence commands for canonical Bot SkillSets.

Each mutation deliberately owns one ``transactional_orm_session``.  Calling
the historical per-row repositories here would open independent sessions and
would make a SkillSet only *eventually* atomic.
"""

from __future__ import annotations

import hashlib
import json

from injector import inject
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from agentclaw.community.core.models.skill import (
    BotSkillInstallation,
    Skill,
    SkillSet,
    SkillSetCreateIdempotency,
    SkillSetSkill,
)
from agentclaw.community.core.repository.protocols.skill_set_control_plane import (
    SkillSetControlPlaneRepositoryProtocol,
)
from agentclaw.community.core.repository.skill_set_control_plane_types import (
    SkillSetDesiredState,
    SkillSetMutation,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


from agentclaw.community.core.skill_center.errors import (
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
        "is_active": bool(row.is_active),
        "user_id": row.user_id,
        "bolt_id": row.bolt_id,
        "engine_type": row.engine_type,
        "gmt_created": row.gmt_created.isoformat() if row.gmt_created else "",
        "gmt_modified": row.gmt_modified.isoformat() if row.gmt_modified else "",
        "env": row.env,
        "type": "default" if row.is_default else "custom",
    }


class SkillSetControlPlaneRepository(SkillSetControlPlaneRepositoryProtocol):
    """Desired-state UoW for SkillSet Membership and Installations."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    @staticmethod
    def _scope(query, model):
        return query.filter(
            model.avernet_tenant == get_current_avernet_tenant(),
            model.env == get_current_env(),
        )

    def list_sets(self, *, bot_id: str, engine_type: str | None = None) -> list[dict]:
        with self._db.orm_session() as session:
            query = self._scope(session.query(SkillSet), SkillSet).filter(
                SkillSet.bolt_id == bot_id
            )
            if engine_type is not None:
                query = query.filter(SkillSet.engine_type == engine_type)
            rows = query.order_by(SkillSet.is_default.desc(), SkillSet.id).all()
            return [_item(row) for row in rows]

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
        idempotency_key: str,
        engine_type: str | None = None,
    ) -> dict:
        request_hash = self._create_request_hash(name=name, description=description)
        try:
            with self._db.transactional_orm_session() as session:
                replay = self._idempotency_record(
                    session,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    idempotency_key=idempotency_key,
                    locked=True,
                )
                if replay is not None:
                    return self._replay_create(
                        session,
                        replay=replay,
                        bot_id=bot_id,
                        request_hash=request_hash,
                        engine_type=engine_type,
                    )
                duplicate = (
                    self._scope(session.query(SkillSet), SkillSet)
                    .filter(
                        SkillSet.bolt_id == bot_id,
                        SkillSet.name == name,
                        SkillSet.engine_type == engine_type,
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
                    is_active=False,
                    env=get_current_env(),
                    avernet_tenant=get_current_avernet_tenant(),
                    engine_type=engine_type,
                )
                session.add(row)
                session.flush()
                session.add(
                    SkillSetCreateIdempotency(
                        bot_id=bot_id,
                        owner_id=owner_id,
                        idempotency_key=idempotency_key,
                        request_name=name,
                        request_description=description,
                        request_hash=request_hash,
                        skill_set_id=row.id,
                        env=get_current_env(),
                        avernet_tenant=get_current_avernet_tenant(),
                    )
                )
                session.flush()
                return _item(row)
        except IntegrityError as exc:
            # The idempotency table's unique key is the concurrency arbiter.
            # A competing command can commit after our initial SELECT but before
            # our INSERT; re-read the durable result instead of surfacing 500.
            with self._db.orm_session() as session:
                replay = self._idempotency_record(
                    session,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    idempotency_key=idempotency_key,
                    locked=False,
                )
                if replay is not None:
                    return self._replay_create(
                        session,
                        replay=replay,
                        bot_id=bot_id,
                        request_hash=request_hash,
                        engine_type=engine_type,
                    )
            raise exc

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
                        SkillSet.engine_type == engine_type,
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
            # Create idempotency records intentionally retain the original
            # response only while their SkillSet exists.  Delete them in the
            # same UoW before removing the parent row; otherwise SQLite and
            # production FK enforcement reject a perfectly valid inactive-set
            # delete.
            self._scope(
                session.query(SkillSetCreateIdempotency), SkillSetCreateIdempotency
            ).filter(SkillSetCreateIdempotency.skill_set_id == row.id).delete(
                synchronize_session=False
            )
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
        self, *, bot_id: str, set_id: str, skill_id: str, engine_type: str | None = None
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
            old = self._snapshot(session, bot_id, engine_type=engine_type)
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
                        skill_id=skill.id,
                        env=get_current_env(),
                        avernet_tenant=get_current_avernet_tenant(),
                    )
                )
            session.flush()
            return SkillSetMutation(_item(row), True, old)

    def remove_skill(
        self, *, bot_id: str, set_id: str, skill_id: str, engine_type: str | None = None
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
            old = self._snapshot(session, bot_id, engine_type=engine_type)
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
                    BotSkillInstallation.bot_id == bot_id,
                    BotSkillInstallation.skill_id == int(skill_id),
                ).delete(synchronize_session=False)
            session.flush()
            return SkillSetMutation(_item(row), True, old)

    def set_active(
        self, *, bot_id: str, set_id: str, active: bool, engine_type: str | None = None
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
                    self._snapshot(session, bot_id, engine_type=engine_type),
                )
            old = self._snapshot(session, bot_id, engine_type=engine_type)
            members = (
                self._scope(session.query(SkillSetSkill), SkillSetSkill)
                .filter(SkillSetSkill.skill_set_id == row.id)
                .with_for_update()
                .all()
            )
            ids = {int(member.skill_id) for member in members}
            changed = bool(row.is_active) != active
            row.is_active = active
            if active:
                existing = self._installations(session, bot_id)
                for skill_id in ids - existing:
                    session.add(
                        BotSkillInstallation(
                            bot_id=bot_id,
                            skill_id=skill_id,
                            env=get_current_env(),
                            avernet_tenant=get_current_avernet_tenant(),
                        )
                    )
            elif ids:
                self._scope(
                    session.query(BotSkillInstallation), BotSkillInstallation
                ).filter(
                    BotSkillInstallation.bot_id == bot_id,
                    BotSkillInstallation.skill_id.in_(ids),
                ).delete(synchronize_session=False)
            session.flush()
            return SkillSetMutation(_item(row), changed, old)

    def replace_active_set(
        self, *, bot_id: str, set_id: str, engine_type: str | None = None
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
            old = self._snapshot(session, bot_id, engine_type=engine_type)
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
            for row in sets:
                row.is_active = int(row.id) == int(target.id)
            if active_member_ids:
                self._scope(
                    session.query(BotSkillInstallation), BotSkillInstallation
                ).filter(
                    BotSkillInstallation.bot_id == bot_id,
                    BotSkillInstallation.skill_id.in_(active_member_ids),
                ).delete(synchronize_session=False)
            existing = self._installations(session, bot_id)
            for skill_id in target_member_ids - existing:
                session.add(
                    BotSkillInstallation(
                        bot_id=bot_id,
                        skill_id=skill_id,
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
            self._scope(
                session.query(BotSkillInstallation), BotSkillInstallation
            ).filter(BotSkillInstallation.bot_id == bot_id).delete(
                synchronize_session=False
            )
            session.flush()
            for skill_id in state.installations:
                session.add(
                    BotSkillInstallation(
                        bot_id=bot_id,
                        skill_id=skill_id,
                        env=get_current_env(),
                        avernet_tenant=get_current_avernet_tenant(),
                    )
                )
            session.flush()

    def snapshot_desired_state(
        self, *, bot_id: str, engine_type: str | None = None
    ) -> SkillSetDesiredState:
        with self._db.orm_session() as session:
            return self._snapshot(session, bot_id, engine_type=engine_type)

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

    def _idempotency_record(
        self,
        session,
        *,
        bot_id: str,
        owner_id: str,
        idempotency_key: str,
        locked: bool,
    ) -> SkillSetCreateIdempotency | None:
        query = self._scope(
            session.query(SkillSetCreateIdempotency), SkillSetCreateIdempotency
        ).filter(
            SkillSetCreateIdempotency.bot_id == bot_id,
            SkillSetCreateIdempotency.owner_id == owner_id,
            SkillSetCreateIdempotency.idempotency_key == idempotency_key,
        )
        if locked:
            query = query.with_for_update()
        return query.one_or_none()

    def _replay_create(
        self,
        session,
        *,
        replay: SkillSetCreateIdempotency,
        bot_id: str,
        request_hash: str,
        engine_type: str | None,
    ) -> dict:
        replay_hash = replay.request_hash or self._create_request_hash(
            name=replay.request_name, description=replay.request_description
        )
        if replay_hash != request_hash:
            raise SkillSetControlPlaneConflictError("IDEMPOTENCY_KEY_REUSED")
        row = self._set(
            session,
            bot_id=bot_id,
            set_id=str(replay.skill_set_id),
            engine_type=engine_type,
        )
        return _item(row)

    @staticmethod
    def _create_request_hash(*, name: str, description: str | None) -> str:
        payload = json.dumps(
            {"description": description, "name": name},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _ordinary(row: SkillSet) -> None:
        if row.is_default:
            raise SkillSetControlPlaneConflictError("SYSTEM_DEFAULT_IMMUTABLE")

    def _installations(self, session, bot_id: str) -> set[int]:
        return {
            int(value[0])
            for value in self._scope(
                session.query(BotSkillInstallation.skill_id), BotSkillInstallation
            )
            .filter(BotSkillInstallation.bot_id == bot_id)
            .all()
        }

    def _snapshot(
        self, session, bot_id: str, *, engine_type: str | None = None
    ) -> SkillSetDesiredState:
        """Lock and capture every ordinary-set desired fact for this Bot."""
        query = self._scope(session.query(SkillSet), SkillSet).filter(
            SkillSet.bolt_id == bot_id, SkillSet.is_default.is_(False)
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
        return SkillSetDesiredState(
            installations=self._installations(session, bot_id),
            set_active={int(row.id): bool(row.is_active) for row in sets},
            memberships={set_id: tuple(items) for set_id, items in memberships.items()},
        )
