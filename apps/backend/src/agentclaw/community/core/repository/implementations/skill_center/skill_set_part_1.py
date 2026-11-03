from __future__ import annotations
from typing import List, Optional
from sqlalchemy import and_, func, or_
from agentclaw.community.log import get_logger
from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneConflictError,
    SkillSetControlPlaneNotFoundError,
)
from agentclaw.community.core.skill_center.offline_policy import require_skill_online
from agentclaw.community.core.repository.implementations.skill_center.bot_skillset_installations import (
    CENTER_MEMBERSHIP_IDENTITY_MISSING,
    center_membership_skill_uuid,
)
from agentclaw.community.utils.env_utils import get_current_env
from .skill_repository_common import (
    _normalize_user_id,
    _skill_to_dict,
    _skill_set_to_dict,
    _mcp_assoc_to_dict,
)

logger = get_logger()


class SkillSetPart1:
    def get_by_id(self, skill_set_id: str) -> Optional[dict]:
        # prod: WHERE id=%s AND (env=%s OR env IS NULL)
        with self._db.orm_session() as db:
            ss = (
                db.query(self.SkillSet)
                .filter(
                    self.SkillSet.id == int(skill_set_id),
                    or_(
                        self.SkillSet.env == get_current_env(),
                        self.SkillSet.env.is_(None),
                    ),
                )
                .first()
            )
            return _skill_set_to_dict(ss) if ss else None

    def get_default(
        self,
        user_id: Optional[str] = None,
        bolt_id: Optional[str] = None,
        engine_type: Optional[str] = None,
        env: Optional[str] = None,
    ) -> Optional[dict]:
        with self._db.orm_session() as db:
            query = db.query(self.SkillSet).filter(
                self.SkillSet.is_default == True,  # noqa: E712
                self.SkillSet.env == (env or get_current_env()),
            )
            if user_id:
                query = query.filter(
                    self.SkillSet.user_id == _normalize_user_id(user_id)
                )
            else:
                query = query.filter(self.SkillSet.user_id.is_(None))
            if engine_type is not None:
                query = query.filter(self.SkillSet.engine_type == engine_type)
            if bolt_id is not None:
                query = query.filter(self.SkillSet.bolt_id == bolt_id)
            ss = query.limit(1).first()
            return _skill_set_to_dict(ss) if ss else None

    def _default_skill_set_filter(
        self,
        engine_type: Optional[str],
        default_skill_set_bolt_id: Optional[str],
    ):
        conditions = [
            self.SkillSet.is_default == True,  # noqa: E712
            self.SkillSet.engine_type == engine_type,
        ]
        if default_skill_set_bolt_id is not None:
            conditions.append(self.SkillSet.bolt_id == default_skill_set_bolt_id)
        return and_(*conditions)

    def _list_all_query(
        self,
        db,
        user_id,
        bolt_id,
        engine_type,
        default_skill_set_bolt_id=None,
        default_skill_set_engine_type=None,
    ):
        query = db.query(self.SkillSet).filter(
            or_(
                self.SkillSet.env == get_current_env(),
                self.SkillSet.env.is_(None),
            )
        )
        effective_bolt_id = bolt_id if bolt_id else "default"
        effective_default_engine = (
            engine_type
            if default_skill_set_engine_type is None
            else default_skill_set_engine_type
        )
        if engine_type is not None:
            query = query.filter(
                or_(
                    and_(
                        self.SkillSet.is_default == False,  # noqa: E712
                        self.SkillSet.bolt_id == effective_bolt_id,
                    ),
                    self._default_skill_set_filter(
                        effective_default_engine,
                        default_skill_set_bolt_id,
                    ),
                )
            )
        else:
            query = query.filter(
                or_(
                    self.SkillSet.bolt_id == effective_bolt_id,
                    self.SkillSet.bolt_id.is_(None),
                    self.SkillSet.is_default == True,  # noqa: E712
                )
            )
        if user_id:
            query = query.filter(
                or_(
                    self.SkillSet.user_id == _normalize_user_id(user_id),
                    self.SkillSet.is_default == True,  # noqa: E712
                    self.SkillSet.is_builtin == True,  # noqa: E712
                )
            )
        return query.order_by(self.SkillSet.gmt_created.desc())

    def list_all(
        self,
        user_id: Optional[str] = None,
        bolt_id: Optional[str] = None,
        engine_type: Optional[str] = None,
        default_skill_set_bolt_id: Optional[str] = None,
        default_skill_set_engine_type: Optional[str] = None,
    ) -> List[dict]:
        with self._db.orm_session() as db:
            rows = self._list_all_query(
                db,
                user_id,
                bolt_id,
                engine_type,
                default_skill_set_bolt_id,
                default_skill_set_engine_type,
            ).all()
            return [_skill_set_to_dict(s) for s in rows]

    def list_all_exclude_deleted(
        self,
        user_id: Optional[str] = None,
        bolt_id: Optional[str] = None,
        engine_type: Optional[str] = None,
        default_skill_set_bolt_id: Optional[str] = None,
        default_skill_set_engine_type: Optional[str] = None,
    ) -> List[dict]:
        from agentclaw.community.plugin_api.models import BotModel

        with self._db.orm_session() as db:
            query = (
                db.query(self.SkillSet)
                .outerjoin(
                    BotModel,
                    and_(
                        self.SkillSet.bolt_id == BotModel.bot_id,
                        self.SkillSet.env == BotModel.env,
                    ),
                )
                .filter(
                    or_(
                        BotModel.is_delete == 0,
                        BotModel.is_delete.is_(None),
                        self.SkillSet.is_default == True,  # noqa: E712
                    ),
                    or_(
                        self.SkillSet.env == get_current_env(),
                        self.SkillSet.env.is_(None),
                    ),
                )
            )
            effective_bolt_id = bolt_id if bolt_id else "default"
            effective_default_engine = (
                engine_type
                if default_skill_set_engine_type is None
                else default_skill_set_engine_type
            )
            if engine_type is not None:
                query = query.filter(
                    or_(
                        and_(
                            self.SkillSet.is_default == False,  # noqa: E712
                            self.SkillSet.bolt_id == effective_bolt_id,
                        ),
                        self._default_skill_set_filter(
                            effective_default_engine,
                            default_skill_set_bolt_id,
                        ),
                    )
                )
            else:
                query = query.filter(
                    or_(
                        self.SkillSet.bolt_id == effective_bolt_id,
                        self.SkillSet.bolt_id.is_(None),
                        self.SkillSet.is_default == True,  # noqa: E712
                    )
                )
            if user_id:
                query = query.filter(
                    or_(
                        self.SkillSet.user_id == _normalize_user_id(user_id),
                        self.SkillSet.is_default == True,  # noqa: E712
                        self.SkillSet.is_builtin == True,  # noqa: E712
                    )
                )
            rows = query.order_by(self.SkillSet.gmt_created.desc()).all()
            return [_skill_set_to_dict(s) for s in rows]

    def get_skill_set_by_name_include_deleted(
        self, name: str, user_id: str, bolt_id: Optional[str] = None
    ) -> Optional[dict]:
        effective_bolt_id = bolt_id if bolt_id else "default"
        with self._db.orm_session() as db:
            ss = (
                db.query(self.SkillSet)
                .filter(
                    self.SkillSet.name == name,
                    self.SkillSet.user_id == _normalize_user_id(user_id),
                    self.SkillSet.env == get_current_env(),
                    self.SkillSet.bolt_id == effective_bolt_id,
                )
                .limit(1)
                .first()
            )
            return _skill_set_to_dict(ss) if ss else None

    def create(self, skill_set_data: dict) -> dict:
        with self._db.orm_session() as db:
            ss = self.SkillSet(
                name=skill_set_data["name"],
                description=skill_set_data.get("description"),
                is_default=skill_set_data.get("is_default", False),
                is_builtin=skill_set_data.get("is_builtin", False),
                user_id=_normalize_user_id(skill_set_data.get("user_id")),
                bolt_id=skill_set_data.get("bolt_id")
                if skill_set_data.get("bolt_id")
                else "default",
                env=get_current_env(),
                is_active=skill_set_data.get("is_active", 0),
                engine_type=skill_set_data.get("engine_type"),
            )
            db.add(ss)
            db.flush()
            new_id = str(ss.id)
        return self.get_by_id(new_id)

    def update(self, skill_set_id: str, skill_set_data: dict) -> Optional[dict]:
        existing = self.get_by_id(skill_set_id)
        if not existing:
            return None
        values = {
            self.SkillSet.name: skill_set_data.get("name", existing["name"]),
            self.SkillSet.description: skill_set_data.get(
                "description", existing.get("description")
            ),
            self.SkillSet.is_default: skill_set_data.get(
                "is_default", existing.get("is_default", False)
            ),
            self.SkillSet.gmt_modified: func.now(),
        }
        with self._db.orm_session() as db:
            db.query(self.SkillSet).filter(
                self.SkillSet.id == int(skill_set_id),
                self.SkillSet.env == get_current_env(),
            ).update(values, synchronize_session=False)
        return self.get_by_id(skill_set_id)

    def delete(self, skill_set_id: str) -> bool:
        with self._db.transactional_orm_session() as db:
            row = (
                db.query(self.SkillSet)
                .filter(
                    self.SkillSet.id == int(skill_set_id),
                    self.SkillSet.env == get_current_env(),
                )
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                return False
            from .skill_set_cleanup import delete_set_rows

            delete_set_rows(db, self, [row.id])
            return True

    def add_skill_to_set(
        self,
        skill_set_id: str,
        skill_id: str,
        user_id: Optional[str] = None,
    ) -> bool:
        with self._db.transactional_orm_session() as db:
            skill_set = (
                db.query(self.SkillSet)
                .filter(
                    self.SkillSet.id == int(skill_set_id),
                    self.SkillSet.env == get_current_env(),
                )
                .with_for_update()
                .one_or_none()
            )
            skill = (
                db.query(self.Skill)
                .filter(
                    self.Skill.id == int(skill_id),
                    self.Skill.env == get_current_env(),
                )
                .with_for_update()
                .one_or_none()
            )
            if skill_set is None or skill is None:
                return False
            if (
                str(skill.git_path or "").startswith("local://")
                and skill.bolt_id != skill_set.bolt_id
            ):
                raise SkillSetControlPlaneNotFoundError()
            require_skill_online(skill)
            membership_skill_uuid = center_membership_skill_uuid(skill)
            # Existing membership remains the successful idempotent result.
            # Canonical cross-Set uniqueness is enforced by the new control
            # plane under its Bot mutation lease, not by this legacy writer.
            existing_membership = (
                db.query(self.SkillSetSkill)
                .filter(
                    self.SkillSetSkill.skill_set_id == int(skill_set_id),
                    self.SkillSetSkill.skill_id == int(skill_id),
                    self.SkillSetSkill.env == get_current_env(),
                )
                .first()
            )
            if existing_membership is not None:
                if (
                    membership_skill_uuid is not None
                    and existing_membership.skill_uuid != membership_skill_uuid
                ):
                    raise SkillSetControlPlaneConflictError(
                        CENTER_MEMBERSHIP_IDENTITY_MISSING
                    )
                return True
            db.add(
                self.SkillSetSkill(
                    skill_set_id=int(skill_set_id),
                    skill_id=int(skill_id),
                    skill_uuid=membership_skill_uuid,
                    user_id=_normalize_user_id(user_id),
                    env=get_current_env(),
                )
            )
            db.flush()
            return True

    def remove_skill_from_set(self, skill_set_id: str, skill_id: str) -> bool:
        with self._db.orm_session() as db:
            rowcount = (
                db.query(self.SkillSetSkill)
                .filter(
                    self.SkillSetSkill.skill_set_id == int(skill_set_id),
                    self.SkillSetSkill.skill_id == int(skill_id),
                    self.SkillSetSkill.env == get_current_env(),
                )
                .delete(synchronize_session=False)
            )
            return rowcount > 0

    def get_skills_in_set(self, skill_set_id: str) -> list[dict]:
        return self.get_skills_in_set_for_env(
            skill_set_id,
            env=get_current_env(),
        )

    def get_skills_in_set_for_env(
        self,
        skill_set_id: str,
        *,
        env: str,
    ) -> list[dict]:
        from agentclaw.community.core.models import Skill

        with self._db.orm_session() as db:
            # non-center: join by skill_id
            non_center = (
                db.query(Skill, self.SkillSetSkill.skill_uuid)
                .join(
                    self.SkillSetSkill,
                    Skill.id == self.SkillSetSkill.skill_id,
                )
                .filter(
                    self.SkillSetSkill.skill_set_id == int(skill_set_id),
                    Skill.env == env,
                    self.SkillSetSkill.env == env,
                    ~Skill.git_path.like("center://%"),
                )
                .all()
            )
            # center: join by skill_uuid + max PUBLISHED version
            max_ver = (
                db.query(
                    Skill.skill_uuid,
                    func.max(Skill.version).label("mv"),
                )
                .filter(
                    Skill.skill_uuid.isnot(None),
                    Skill.status == "PUBLISHED",
                    Skill.env == env,
                )
                .group_by(Skill.skill_uuid)
                .subquery()
            )
            center = (
                db.query(Skill, self.SkillSetSkill.skill_uuid)
                .join(
                    self.SkillSetSkill,
                    Skill.skill_uuid == self.SkillSetSkill.skill_uuid,
                )
                .join(
                    max_ver,
                    and_(
                        Skill.skill_uuid == max_ver.c.skill_uuid,
                        Skill.version == max_ver.c.mv,
                    ),
                )
                .filter(
                    self.SkillSetSkill.skill_set_id == int(skill_set_id),
                    Skill.env == env,
                    self.SkillSetSkill.env == env,
                    Skill.git_path.like("center://%"),
                    Skill.status == "PUBLISHED",
                )
                .all()
            )
            results = []
            for s, assoc_uuid in list(non_center) + list(center):
                d = _skill_to_dict(s)
                d["assoc_skill_uuid"] = assoc_uuid
                results.append(d)
            return results

    def find_affected_bots_by_skill_uuid(
        self, skill_uuid: str, env: Optional[str] = None
    ) -> list[dict]:
        from agentclaw.community.plugin_api.models import BotModel

        env = env or get_current_env()
        with self._db.orm_session() as db:
            rows = (
                db.query(
                    BotModel.bot_id,
                    BotModel.active_engine,
                    BotModel.owner_id,
                )
                .select_from(self.SkillSet)
                .join(
                    self.SkillSetSkill,
                    and_(
                        self.SkillSet.id == self.SkillSetSkill.skill_set_id,
                        self.SkillSetSkill.env == self.SkillSet.env,
                    ),
                )
                .join(
                    BotModel,
                    and_(
                        BotModel.bot_id == self.SkillSet.bolt_id,
                        BotModel.env == self.SkillSet.env,
                        BotModel.owner_id == self.SkillSet.user_id,
                    ),
                )
                .filter(
                    self.SkillSetSkill.skill_uuid == skill_uuid,
                    self.SkillSet.is_active == True,  # noqa: E712
                    self.SkillSet.is_default == False,  # noqa: E712
                    self.SkillSet.bolt_id.isnot(None),
                    BotModel.is_delete == 0,
                    self.SkillSet.env == env,
                )
                .distinct()
                .all()
            )
            return [
                {
                    "bot_id": r[0],
                    "active_engine": r[1],
                    "owner_id": r[2],
                }
                for r in rows
            ]

    def add_mcp_to_set(
        self,
        skill_set_id: str,
        server_code: str,
        name: str,
        description: Optional[str] = None,
        icon: Optional[str] = None,
        user_id: Optional[str] = None,
        env: Optional[str] = None,
    ) -> bool:
        with self._db.orm_session() as db:
            target_env = env or get_current_env()
            skill_set = (
                db.query(self.SkillSet)
                .filter(
                    self.SkillSet.id == int(skill_set_id),
                    self.SkillSet.env == target_env,
                )
                .one_or_none()
            )
            if skill_set is None:
                return False
            db.add(
                self.SkillSetMCPServer(
                    skill_set_id=int(skill_set_id),
                    server_code=server_code,
                    name=name,
                    description=description,
                    icon=icon,
                    user_id=user_id,
                    # The row must carry the resolved env, not the raw
                    # parameter: a None here wrote env-less rows that every
                    # env-scoped read — the flush included — cannot see.
                    env=target_env,
                )
            )
            db.flush()
            return True

    def get_mcp_servers_in_set(self, skill_set_id: str) -> list[dict]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self.SkillSetMCPServer)
                .filter(self.SkillSetMCPServer.skill_set_id == int(skill_set_id))
                .order_by(self.SkillSetMCPServer.gmt_created)
                .all()
            )
            return [_mcp_assoc_to_dict(m) for m in rows]

    def get_mcp_servers_in_set_for_env(
        self, skill_set_id: str, *, env: str
    ) -> list[dict]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self.SkillSetMCPServer)
                .filter(
                    self.SkillSetMCPServer.skill_set_id == int(skill_set_id),
                    self.SkillSetMCPServer.env == env,
                )
                .order_by(self.SkillSetMCPServer.gmt_created)
                .all()
            )
            return [_mcp_assoc_to_dict(m) for m in rows]

    def remove_mcp_from_set(self, skill_set_id: str, server_code: str) -> bool:
        with self._db.orm_session() as db:
            rowcount = (
                db.query(self.SkillSetMCPServer)
                .filter(
                    self.SkillSetMCPServer.skill_set_id == int(skill_set_id),
                    self.SkillSetMCPServer.server_code == server_code,
                )
                .delete(synchronize_session=False)
            )
            return rowcount > 0
