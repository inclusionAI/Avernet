"""Transactional persistence commands for canonical Bot SkillSets.

Each mutation deliberately owns one ``transactional_orm_session``.  Calling
the historical per-row repositories here would open independent sessions and
would make a SkillSet only *eventually* atomic.
"""

from __future__ import annotations

from dataclasses import dataclass

from injector import inject
from sqlalchemy.exc import IntegrityError
from agentclaw.community.core.models.skill import (
    BotSkillInstallation, Skill, SkillSet, SkillSetCreateIdempotency,
    SkillSetNameClaim, SkillSetSkill,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneConflictError, SkillSetControlPlaneNotFoundError,
)


@dataclass(frozen=True)
class SkillSetDesiredState:
    installations: set[int]
    set_active: dict[int, bool]
    memberships: dict[int, tuple[tuple[int, str | None, str | None], ...]]


@dataclass(frozen=True)
class SkillSetMutation:
    item: dict
    changed: bool
    previous_state: SkillSetDesiredState


def _item(row: SkillSet) -> dict:
    return {
        "id": str(row.id), "name": row.name, "description": row.description,
        "is_default": bool(row.is_default), "is_active": bool(row.is_active),
    }


class SkillSetControlPlaneRepository:
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

    def list_sets(self, *, bot_id: str) -> list[dict]:
        with self._db.orm_session() as session:
            rows = self._scope(session.query(SkillSet), SkillSet).filter(
                SkillSet.bolt_id == bot_id
            ).order_by(SkillSet.is_default.desc(), SkillSet.id).all()
            return [_item(row) for row in rows]

    def get_set(self, *, bot_id: str, set_id: str) -> dict:
        with self._db.orm_session() as session:
            row = self._set(session, bot_id=bot_id, set_id=set_id)
            return _item(row)

    def create_set(self, *, bot_id: str, owner_id: str, name: str, description: str | None, idempotency_key: str) -> dict:
        try:
            return self._create_set(
                bot_id=bot_id, owner_id=owner_id, name=name,
                description=description, idempotency_key=idempotency_key,
            )
        except IntegrityError:
            # A unique claim/idempotency insert lost a concurrent race.  The
            # losing transaction has already rolled back; read the winner in a
            # new session and turn it into the public replay/conflict contract.
            with self._db.orm_session() as session:
                replay = self._scope(session.query(SkillSetCreateIdempotency), SkillSetCreateIdempotency).filter(
                    SkillSetCreateIdempotency.bot_id == bot_id,
                    SkillSetCreateIdempotency.owner_id == owner_id,
                    SkillSetCreateIdempotency.idempotency_key == idempotency_key,
                ).one_or_none()
                if replay is not None:
                    if replay.request_name != name or replay.request_description != description:
                        raise SkillSetControlPlaneConflictError("IDEMPOTENCY_KEY_REUSED")
                    return _item(self._set(session, bot_id=bot_id, set_id=str(replay.skill_set_id)))
                raise SkillSetControlPlaneConflictError("SKILL_SET_NAME_CONFLICT")

    def _create_set(self, *, bot_id: str, owner_id: str, name: str, description: str | None, idempotency_key: str) -> dict:
        with self._db.transactional_orm_session() as session:
            replay = self._scope(session.query(SkillSetCreateIdempotency), SkillSetCreateIdempotency).filter(
                SkillSetCreateIdempotency.bot_id == bot_id,
                SkillSetCreateIdempotency.owner_id == owner_id,
                SkillSetCreateIdempotency.idempotency_key == idempotency_key,
            ).with_for_update().one_or_none()
            if replay is not None:
                if replay.request_name != name or replay.request_description != description:
                    raise SkillSetControlPlaneConflictError("IDEMPOTENCY_KEY_REUSED")
                row = self._set(session, bot_id=bot_id, set_id=str(replay.skill_set_id))
                return _item(row)
            duplicate = self._scope(session.query(SkillSet), SkillSet).filter(
                SkillSet.bolt_id == bot_id, SkillSet.name == name
            ).first()
            if duplicate is not None:
                raise SkillSetControlPlaneConflictError("SKILL_SET_NAME_CONFLICT")
            row = SkillSet(
                name=name, description=description, bolt_id=bot_id, user_id=owner_id,
                is_default=False, is_builtin=False, is_active=False, env=get_current_env(),
                avernet_tenant=get_current_avernet_tenant(),
            )
            session.add(row)
            session.flush()
            session.add(SkillSetCreateIdempotency(
                bot_id=bot_id, owner_id=owner_id, idempotency_key=idempotency_key,
                request_name=name, request_description=description, skill_set_id=row.id,
                env=get_current_env(), avernet_tenant=get_current_avernet_tenant(),
            ))
            session.add(SkillSetNameClaim(
                bot_id=bot_id, name=name, skill_set_id=row.id,
                env=get_current_env(), avernet_tenant=get_current_avernet_tenant(),
            ))
            session.flush()
            return _item(row)

    def update_set(self, *, bot_id: str, set_id: str, name: str | None, description: str | None) -> dict:
        with self._db.transactional_orm_session() as session:
            row = self._set(session, bot_id=bot_id, set_id=set_id, locked=True)
            if row.is_default and name is not None:
                raise SkillSetControlPlaneConflictError("SYSTEM_DEFAULT_IMMUTABLE")
            if name is not None and name != row.name:
                duplicate = self._scope(session.query(SkillSet), SkillSet).filter(
                    SkillSet.bolt_id == bot_id, SkillSet.name == name, SkillSet.id != row.id
                ).first()
                if duplicate is not None:
                    raise SkillSetControlPlaneConflictError("SKILL_SET_NAME_CONFLICT")
                claim = self._scope(session.query(SkillSetNameClaim), SkillSetNameClaim).filter(
                    SkillSetNameClaim.bot_id == bot_id, SkillSetNameClaim.name == name
                ).with_for_update().one_or_none()
                if claim is not None:
                    raise SkillSetControlPlaneConflictError("SKILL_SET_NAME_CONFLICT")
                session.add(SkillSetNameClaim(
                    bot_id=bot_id, name=name, skill_set_id=row.id,
                    env=get_current_env(), avernet_tenant=get_current_avernet_tenant(),
                ))
                old_claim = self._scope(session.query(SkillSetNameClaim), SkillSetNameClaim).filter(
                    SkillSetNameClaim.skill_set_id == row.id,
                    SkillSetNameClaim.name == row.name,
                ).with_for_update().one_or_none()
                if old_claim is not None:
                    session.delete(old_claim)
                row.name = name
            if description is not None:
                row.description = description
            session.flush()
            return _item(row)

    def delete_set(self, *, bot_id: str, set_id: str) -> None:
        with self._db.transactional_orm_session() as session:
            row = self._set(session, bot_id=bot_id, set_id=set_id, locked=True)
            if row.is_default:
                raise SkillSetControlPlaneConflictError("SYSTEM_DEFAULT_IMMUTABLE")
            if row.is_active:
                raise SkillSetControlPlaneConflictError("SKILL_SET_ACTIVE")
            self._scope(session.query(SkillSetNameClaim), SkillSetNameClaim).filter(
                SkillSetNameClaim.skill_set_id == row.id
            ).delete(synchronize_session=False)
            session.delete(row)

    def list_skills(self, *, bot_id: str, set_id: str) -> list[dict]:
        with self._db.orm_session() as session:
            row = self._set(session, bot_id=bot_id, set_id=set_id)
            rows = self._scope(session.query(Skill), Skill).join(
                SkillSetSkill, SkillSetSkill.skill_id == Skill.id
            ).filter(SkillSetSkill.skill_set_id == row.id).order_by(Skill.id).all()
            return [{"id": str(skill.id), "name": skill.name, "description": skill.description} for skill in rows]

    def add_skill(self, *, bot_id: str, set_id: str, skill_id: str) -> SkillSetMutation:
        with self._db.transactional_orm_session() as session:
            row = self._set(session, bot_id=bot_id, set_id=set_id, locked=True)
            self._ordinary(row)
            skill = self._scope(session.query(Skill), Skill).filter(Skill.id == int(skill_id)).with_for_update().one_or_none()
            if skill is None or (str(skill.git_path or "").startswith("local://") and skill.bolt_id != bot_id):
                raise SkillSetControlPlaneNotFoundError()
            old = self._snapshot(session, bot_id)
            current = self._scope(session.query(SkillSetSkill), SkillSetSkill).filter(
                SkillSetSkill.skill_set_id == row.id, SkillSetSkill.skill_id == skill.id
            ).first()
            if current is not None:
                return SkillSetMutation(_item(row), False, old)
            # An Installation with no ordinary Membership is Direct only when
            # System Default does not also source it.  Default is always
            # active and deliberately shares the Installation projection.
            if skill.id in old.installations and not self._is_default_member(
                session, bot_id=bot_id, skill_id=int(skill.id)
            ):
                raise SkillSetControlPlaneConflictError("RESOURCE_DIRECT_ACTIVE")
            owner = self._scope(session.query(SkillSet), SkillSet).join(
                SkillSetSkill, SkillSetSkill.skill_set_id == SkillSet.id
            ).filter(
                SkillSet.bolt_id == bot_id, SkillSet.is_default.is_(False),
                SkillSetSkill.skill_id == skill.id,
            ).first()
            if owner is not None:
                raise SkillSetControlPlaneConflictError("RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET")
            session.add(SkillSetSkill(
                skill_set_id=row.id, skill_id=skill.id, user_id=row.user_id, env=get_current_env(),
                avernet_tenant=get_current_avernet_tenant(),
            ))
            if row.is_active:
                session.add(BotSkillInstallation(
                    bot_id=bot_id, skill_id=skill.id, env=get_current_env(),
                    avernet_tenant=get_current_avernet_tenant(),
                ))
            session.flush()
            return SkillSetMutation(_item(row), True, old)

    def remove_skill(self, *, bot_id: str, set_id: str, skill_id: str) -> SkillSetMutation:
        with self._db.transactional_orm_session() as session:
            row = self._set(session, bot_id=bot_id, set_id=set_id, locked=True)
            self._ordinary(row)
            old = self._snapshot(session, bot_id)
            membership = self._scope(session.query(SkillSetSkill), SkillSetSkill).filter(
                SkillSetSkill.skill_set_id == row.id, SkillSetSkill.skill_id == int(skill_id)
            ).first()
            if membership is None:
                return SkillSetMutation(_item(row), False, old)
            session.delete(membership)
            if row.is_active and not self._has_other_active_source(
                session, bot_id=bot_id, skill_id=int(skill_id), excluding_set_id=int(row.id)
            ):
                self._scope(session.query(BotSkillInstallation), BotSkillInstallation).filter(
                    BotSkillInstallation.bot_id == bot_id,
                    BotSkillInstallation.skill_id == int(skill_id),
                ).delete(synchronize_session=False)
            session.flush()
            return SkillSetMutation(_item(row), True, old)

    def set_active(self, *, bot_id: str, set_id: str, active: bool) -> SkillSetMutation:
        with self._db.transactional_orm_session() as session:
            row = self._set(session, bot_id=bot_id, set_id=set_id, locked=True)
            if row.is_default:
                if not active:
                    raise SkillSetControlPlaneConflictError("SYSTEM_DEFAULT_IMMUTABLE")
                return SkillSetMutation(_item(row), False, self._snapshot(session, bot_id))
            old = self._snapshot(session, bot_id)
            members = self._scope(session.query(SkillSetSkill), SkillSetSkill).filter(
                SkillSetSkill.skill_set_id == row.id
            ).with_for_update().all()
            ids = {int(member.skill_id) for member in members}
            changed = bool(row.is_active) != active
            row.is_active = active
            if active:
                existing = self._installations(session, bot_id)
                for skill_id in ids - existing:
                    session.add(BotSkillInstallation(bot_id=bot_id, skill_id=skill_id, env=get_current_env(), avernet_tenant=get_current_avernet_tenant()))
            elif ids:
                removable = {
                    skill_id for skill_id in ids
                    if not self._has_other_active_source(
                        session, bot_id=bot_id, skill_id=skill_id,
                        excluding_set_id=int(row.id),
                    )
                }
                if removable:
                    self._scope(session.query(BotSkillInstallation), BotSkillInstallation).filter(
                        BotSkillInstallation.bot_id == bot_id,
                        BotSkillInstallation.skill_id.in_(removable),
                    ).delete(synchronize_session=False)
            session.flush()
            return SkillSetMutation(_item(row), changed, old)

    def restore_desired_state(self, *, bot_id: str, state: SkillSetDesiredState) -> None:
        """Atomically restore Membership, set-state and Installation facts."""
        with self._db.transactional_orm_session() as session:
            current_sets = self._scope(session.query(SkillSet), SkillSet).filter(
                SkillSet.bolt_id == bot_id, SkillSet.is_default.is_(False)
            ).with_for_update().all()
            current_ids = {int(row.id) for row in current_sets}
            if current_ids:
                self._scope(session.query(SkillSetSkill), SkillSetSkill).filter(
                    SkillSetSkill.skill_set_id.in_(current_ids)
                ).delete(synchronize_session=False)
            for row in current_sets:
                row.is_active = state.set_active.get(int(row.id), False)
            for set_id, members in state.memberships.items():
                for skill_id, user_id, skill_uuid in members:
                    session.add(SkillSetSkill(
                        skill_set_id=set_id, skill_id=skill_id, user_id=user_id,
                        skill_uuid=skill_uuid, env=get_current_env(),
                        avernet_tenant=get_current_avernet_tenant(),
                    ))
            self._scope(session.query(BotSkillInstallation), BotSkillInstallation).filter(
                BotSkillInstallation.bot_id == bot_id
            ).delete(synchronize_session=False)
            session.flush()
            for skill_id in state.installations:
                session.add(BotSkillInstallation(bot_id=bot_id, skill_id=skill_id, env=get_current_env(), avernet_tenant=get_current_avernet_tenant()))
            session.flush()

    def _set(self, session, *, bot_id: str, set_id: str, locked: bool = False) -> SkillSet:
        query = self._scope(session.query(SkillSet), SkillSet).filter(
            SkillSet.id == int(set_id), SkillSet.bolt_id == bot_id
        )
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

    def _installations(self, session, bot_id: str) -> set[int]:
        return {
            int(value[0]) for value in self._scope(
                session.query(BotSkillInstallation.skill_id), BotSkillInstallation
            ).filter(BotSkillInstallation.bot_id == bot_id).all()
        }

    def _is_default_member(self, session, *, bot_id: str, skill_id: int) -> bool:
        return self._scope(session.query(SkillSetSkill), SkillSetSkill).join(
            SkillSet, SkillSet.id == SkillSetSkill.skill_set_id
        ).filter(
            SkillSet.bolt_id == bot_id, SkillSet.is_default.is_(True),
            SkillSetSkill.skill_id == skill_id,
        ).first() is not None

    def _has_other_active_source(
        self, session, *, bot_id: str, skill_id: int, excluding_set_id: int
    ) -> bool:
        return self._scope(session.query(SkillSetSkill), SkillSetSkill).join(
            SkillSet, SkillSet.id == SkillSetSkill.skill_set_id
        ).filter(
            SkillSet.bolt_id == bot_id, SkillSet.is_active.is_(True),
            SkillSet.id != excluding_set_id, SkillSetSkill.skill_id == skill_id,
        ).first() is not None

    def _snapshot(self, session, bot_id: str) -> SkillSetDesiredState:
        """Lock and capture every ordinary-set desired fact for this Bot."""
        sets = self._scope(session.query(SkillSet), SkillSet).filter(
            SkillSet.bolt_id == bot_id, SkillSet.is_default.is_(False)
        ).with_for_update().all()
        set_ids = {int(row.id) for row in sets}
        memberships: dict[int, list[tuple[int, str | None, str | None]]] = {
            set_id: [] for set_id in set_ids
        }
        if set_ids:
            rows = self._scope(session.query(SkillSetSkill), SkillSetSkill).filter(
                SkillSetSkill.skill_set_id.in_(set_ids)
            ).with_for_update().all()
            for member in rows:
                memberships[int(member.skill_set_id)].append(
                    (int(member.skill_id), member.user_id, member.skill_uuid)
                )
        return SkillSetDesiredState(
            installations=self._installations(session, bot_id),
            set_active={int(row.id): bool(row.is_active) for row in sets},
            memberships={set_id: tuple(items) for set_id, items in memberships.items()},
        )
