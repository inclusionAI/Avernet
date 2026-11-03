from __future__ import annotations
from typing import List, Optional
from sqlalchemy import func, or_
from agentclaw.community.log import get_logger
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env
from .skill_repository_common import (
    _normalize_user_id,
    _skill_set_to_dict,
    _mcp_assoc_to_dict,
)

logger = get_logger()


class SkillSetPart2:
    def add_default_mcp_exclusion(
        self,
        user_id: str,
        bot_id: str,
        skill_set_id: int,
        server_code: str,
    ) -> bool:
        """The only S4 upsert — prod INSERT ... ON DUPLICATE KEY
        UPDATE on uk_user_bot_skillset_mcp."""
        # DefaultSkillsetMcpExclusion is a shared SQLAlchemy ``Base``
        # declarative model with __tablename__ =
        # "ac_default_skillset_mcp_exclusion" and the genuine unique
        # Index uk_user_bot_skillset_mcp — it maps to the real table
        # and emits a valid INSERT/upsert on OceanBase as well as
        # SQLite. It is owned by the skill_center domain.
        from agentclaw.community.core.skill_center.orm import (
            DefaultSkillsetMcpExclusion,
        )

        with self._db.orm_session() as db:
            # Dialect branch (not mode branch): both arms perform the
            # same atomic INSERT-or-update on the
            # ``uk_user_bot_skillset_mcp`` unique key; SQLAlchemy
            # exposes ON CONFLICT / ON DUPLICATE KEY through two
            # dialect-specific ``insert()`` constructs with no
            # portable single-statement equivalent. Dispatch on
            # ``dialect.name``, not runtime mode — the only
            # sanctioned dialect branch under the unified-repo rule.
            #
            # TODO(repo-unify): collapse via a shared atomic_upsert
            # helper in ``plugins/_sql_helpers.py``.
            dialect = db.get_bind().dialect.name
            table = DefaultSkillsetMcpExclusion.__table__
            values = {
                "avernet_tenant": get_current_avernet_tenant(),
                "user_id": user_id,
                "bot_id": bot_id,
                "skill_set_id": skill_set_id,
                "server_code": server_code,
            }
            if dialect == "sqlite":
                from sqlalchemy.dialects.sqlite import (
                    insert as _insert,
                )

                stmt = _insert(table).values(
                    excluded_at=func.now(),
                    gmt_create=func.now(),
                    gmt_modified=func.now(),
                    **values,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=[
                        "avernet_tenant",
                        "user_id",
                        "bot_id",
                        "skill_set_id",
                        "server_code",
                    ],
                    set_={
                        "excluded_at": func.now(),
                        "gmt_modified": func.now(),
                    },
                )
                result = db.execute(stmt)
            else:
                from sqlalchemy.dialects.mysql import (
                    insert as _insert,
                )

                stmt = _insert(table).values(
                    excluded_at=func.now(),
                    gmt_create=func.now(),
                    gmt_modified=func.now(),
                    **values,
                )
                stmt = stmt.on_duplicate_key_update(
                    excluded_at=func.now(),
                    gmt_modified=func.now(),
                )
                result = db.execute(stmt)
            return result.rowcount > 0

    def remove_default_mcp_exclusion(
        self,
        user_id: str,
        bot_id: str,
        skill_set_id: int,
        server_code: str,
    ) -> bool:
        from agentclaw.community.core.skill_center.orm import (
            DefaultSkillsetMcpExclusion,
        )

        with self._db.orm_session() as db:
            rowcount = (
                db.query(DefaultSkillsetMcpExclusion)
                .filter(
                    DefaultSkillsetMcpExclusion.user_id == user_id,
                    DefaultSkillsetMcpExclusion.bot_id == bot_id,
                    DefaultSkillsetMcpExclusion.skill_set_id == skill_set_id,
                    DefaultSkillsetMcpExclusion.server_code == server_code,
                )
                .delete(synchronize_session=False)
            )
            return rowcount > 0

    def get_excluded_mcps(self, user_id: str, bot_id: str, skill_set_id: int) -> list:
        from agentclaw.community.core.skill_center.orm import (
            DefaultSkillsetMcpExclusion,
        )

        with self._db.orm_session() as db:
            rows = (
                db.query(DefaultSkillsetMcpExclusion.server_code)
                .filter(
                    DefaultSkillsetMcpExclusion.user_id == user_id,
                    DefaultSkillsetMcpExclusion.bot_id == bot_id,
                    DefaultSkillsetMcpExclusion.skill_set_id == skill_set_id,
                )
                .all()
            )
            return [r[0] for r in rows]

    def get_all_excluded_mcps(self, user_id: str, bot_id: str) -> list:
        from agentclaw.community.core.skill_center.orm import (
            DefaultSkillsetMcpExclusion,
        )

        with self._db.orm_session() as db:
            rows = (
                db.query(DefaultSkillsetMcpExclusion.server_code)
                .filter(
                    DefaultSkillsetMcpExclusion.user_id == user_id,
                    DefaultSkillsetMcpExclusion.bot_id == bot_id,
                )
                .all()
            )
            return [r[0] for r in rows]

    def add_default_skill_exclusion(
        self,
        user_id: str,
        bot_id: str,
        skill_set_id: int,
        skill_id: int,
    ) -> bool:
        """Upsert a skill exclusion for a default skill set.

        On duplicate (uk_user_bot_skillset_skill) the row is updated
        with a new ``excluded_at`` / ``gmt_modified`` timestamp.
        """
        from agentclaw.community.core.skill_center.orm import (
            DefaultSkillsetSkillExclusion,
        )

        with self._db.orm_session() as db:
            dialect = db.get_bind().dialect.name
            table = DefaultSkillsetSkillExclusion.__table__
            values = {
                "avernet_tenant": get_current_avernet_tenant(),
                "user_id": user_id,
                "bot_id": bot_id,
                "skill_set_id": skill_set_id,
                "skill_id": skill_id,
            }
            if dialect == "sqlite":
                from sqlalchemy.dialects.sqlite import (
                    insert as _insert,
                )

                stmt = _insert(table).values(
                    excluded_at=func.now(),
                    gmt_create=func.now(),
                    gmt_modified=func.now(),
                    **values,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=[
                        "avernet_tenant",
                        "user_id",
                        "bot_id",
                        "skill_set_id",
                        "skill_id",
                    ],
                    set_={
                        "excluded_at": func.now(),
                        "gmt_modified": func.now(),
                    },
                )
                result = db.execute(stmt)
            else:
                from sqlalchemy.dialects.mysql import (
                    insert as _insert,
                )

                stmt = _insert(table).values(
                    excluded_at=func.now(),
                    gmt_create=func.now(),
                    gmt_modified=func.now(),
                    **values,
                )
                stmt = stmt.on_duplicate_key_update(
                    excluded_at=func.now(),
                    gmt_modified=func.now(),
                )
                result = db.execute(stmt)
            return result.rowcount > 0

    def remove_default_skill_exclusion(
        self,
        user_id: str,
        bot_id: str,
        skill_set_id: int,
        skill_id: int,
    ) -> bool:
        """Delete a skill exclusion row (used for rollback)."""
        from agentclaw.community.core.skill_center.orm import (
            DefaultSkillsetSkillExclusion,
        )

        with self._db.orm_session() as db:
            rowcount = (
                db.query(DefaultSkillsetSkillExclusion)
                .filter(
                    DefaultSkillsetSkillExclusion.user_id == user_id,
                    DefaultSkillsetSkillExclusion.bot_id == bot_id,
                    DefaultSkillsetSkillExclusion.skill_set_id == skill_set_id,
                    DefaultSkillsetSkillExclusion.skill_id == skill_id,
                )
                .delete(synchronize_session=False)
            )
            return rowcount > 0

    def remove_all_default_skill_exclusions(
        self, user_id: str, bot_id: str, skill_id: int
    ) -> bool:
        """Clear stale exclusions left behind by a former default SkillSet."""
        from agentclaw.community.core.skill_center.orm import (
            DefaultSkillsetSkillExclusion,
        )

        with self._db.orm_session() as db:
            rowcount = (
                db.query(DefaultSkillsetSkillExclusion)
                .filter(
                    DefaultSkillsetSkillExclusion.user_id == user_id,
                    DefaultSkillsetSkillExclusion.bot_id == bot_id,
                    DefaultSkillsetSkillExclusion.skill_id == skill_id,
                )
                .delete(synchronize_session=False)
            )
            return rowcount > 0

    def get_excluded_skills(self, user_id: str, bot_id: str, skill_set_id: int) -> list:
        """Return skill_id list excluded by (user, bot, skill_set)."""
        from agentclaw.community.core.skill_center.orm import (
            DefaultSkillsetSkillExclusion,
        )

        with self._db.orm_session() as db:
            rows = (
                db.query(DefaultSkillsetSkillExclusion.skill_id)
                .filter(
                    DefaultSkillsetSkillExclusion.user_id == user_id,
                    DefaultSkillsetSkillExclusion.bot_id == bot_id,
                    DefaultSkillsetSkillExclusion.skill_set_id == skill_set_id,
                )
                .all()
            )
            return [r[0] for r in rows]

    def get_all_excluded_skills(self, user_id: str, bot_id: str) -> list:
        """Return skill_id list excluded by (user, bot) across all
        default skill sets."""
        from agentclaw.community.core.skill_center.orm import (
            DefaultSkillsetSkillExclusion,
        )

        with self._db.orm_session() as db:
            rows = (
                db.query(DefaultSkillsetSkillExclusion.skill_id)
                .filter(
                    DefaultSkillsetSkillExclusion.user_id == user_id,
                    DefaultSkillsetSkillExclusion.bot_id == bot_id,
                )
                .all()
            )
            return [r[0] for r in rows]

    def get_all_user_mcps(self, user_id: str) -> List[dict]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self.SkillSetMCPServer)
                .filter(
                    self.SkillSetMCPServer.user_id == _normalize_user_id(user_id),
                    or_(
                        self.SkillSetMCPServer.env == get_current_env(),
                        self.SkillSetMCPServer.env.is_(None),
                    ),
                )
                .order_by(self.SkillSetMCPServer.gmt_modified.desc())
                .all()
            )
            seen = set()
            unique = []
            for m in rows:
                if m.server_code not in seen:
                    seen.add(m.server_code)
                    unique.append(_mcp_assoc_to_dict(m))
            return unique

    def get_active_skill_set(
        self,
        user_id: Optional[str] = None,
        bolt_id: Optional[str] = None,
        engine_type: Optional[str] = None,
    ) -> Optional[dict]:
        effective_bolt_id = bolt_id if bolt_id else "default"
        with self._db.orm_session() as db:
            query = db.query(self.SkillSet).filter(
                self.SkillSet.is_active == True,  # noqa: E712
                self.SkillSet.env == get_current_env(),
                self.SkillSet.bolt_id == effective_bolt_id,
            )
            if engine_type is not None:
                query = query.filter(self.SkillSet.engine_type == engine_type)
            if user_id:
                query = query.filter(
                    self.SkillSet.user_id == _normalize_user_id(user_id)
                )
            else:
                query = query.filter(self.SkillSet.user_id.is_(None))
            ss = query.limit(1).first()
            return _skill_set_to_dict(ss) if ss else None

    def set_active_skill_set(
        self,
        skill_set_id: str,
        user_id: Optional[str] = None,
        bolt_id: Optional[str] = None,
        engine_type: Optional[str] = None,
    ) -> bool:
        effective_bolt_id = bolt_id if bolt_id else "default"
        parsed = _normalize_user_id(user_id)
        with self._db.orm_session() as db:
            # Step 1: clear other active sets (exclude default)
            clear_q = db.query(self.SkillSet).filter(
                self.SkillSet.bolt_id == effective_bolt_id,
                or_(
                    self.SkillSet.env == get_current_env(),
                    self.SkillSet.env.is_(None),
                ),
                self.SkillSet.is_default == False,  # noqa: E712
            )
            if engine_type is not None:
                clear_q = clear_q.filter(
                    or_(
                        self.SkillSet.engine_type == engine_type,
                        self.SkillSet.engine_type.is_(None),
                    )
                )
            if user_id:
                clear_q = clear_q.filter(self.SkillSet.user_id == parsed)
            else:
                clear_q = clear_q.filter(self.SkillSet.user_id.is_(None))
            clear_q.update(
                {
                    self.SkillSet.is_active: 0,
                    self.SkillSet.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )

            # Step 2: activate the target
            act_q = db.query(self.SkillSet).filter(
                self.SkillSet.id == int(skill_set_id),
                or_(
                    self.SkillSet.env == get_current_env(),
                    self.SkillSet.env.is_(None),
                ),
                self.SkillSet.is_default == False,  # noqa: E712
                self.SkillSet.bolt_id == effective_bolt_id,
            )
            if engine_type is not None:
                act_q = act_q.filter(
                    or_(
                        self.SkillSet.engine_type == engine_type,
                        self.SkillSet.engine_type.is_(None),
                    )
                )
            if user_id:
                act_q = act_q.filter(self.SkillSet.user_id == parsed)
            else:
                act_q = act_q.filter(self.SkillSet.user_id.is_(None))
            activated = act_q.update(
                {
                    self.SkillSet.is_active: 1,
                    self.SkillSet.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )
            return activated > 0

    def clear_active_skill_set(
        self,
        user_id: Optional[str] = None,
        bolt_id: Optional[str] = None,
        engine_type: Optional[str] = None,
    ) -> bool:
        effective_bolt_id = bolt_id if bolt_id else "default"
        parsed = _normalize_user_id(user_id)
        with self._db.orm_session() as db:
            q = db.query(self.SkillSet).filter(
                self.SkillSet.bolt_id == effective_bolt_id,
                self.SkillSet.env == get_current_env(),
                self.SkillSet.is_default == False,  # noqa: E712
            )
            if engine_type is not None:
                q = q.filter(self.SkillSet.engine_type == engine_type)
            if user_id:
                q = q.filter(self.SkillSet.user_id == parsed)
            else:
                q = q.filter(self.SkillSet.user_id.is_(None))
            q.update(
                {
                    self.SkillSet.is_active: 0,
                    self.SkillSet.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )
            return True

    def get_all_active_skill_sets(
        self,
        user_id: Optional[str] = None,
        bolt_id: Optional[str] = None,
        engine_type: Optional[str] = None,
        default_skill_set_bolt_id: Optional[str] = None,
        default_skill_set_engine_type: Optional[str] = None,
    ) -> List[dict]:
        effective_bolt_id = bolt_id if bolt_id else "default"
        parsed = _normalize_user_id(user_id)
        active_sets = []
        with self._db.orm_session() as db:
            query = db.query(self.SkillSet).filter(
                self.SkillSet.is_active == True,  # noqa: E712
                self.SkillSet.is_default == False,  # noqa: E712
                self.SkillSet.env == get_current_env(),
                self.SkillSet.bolt_id == effective_bolt_id,
            )
            if engine_type is not None:
                query = query.filter(self.SkillSet.engine_type == engine_type)
            if user_id:
                query = query.filter(self.SkillSet.user_id == parsed)
            else:
                query = query.filter(self.SkillSet.user_id.is_(None))
            for ss in query.all():
                active_sets.append(_skill_set_to_dict(ss))

        effective_default_engine = (
            engine_type
            if default_skill_set_engine_type is None
            else default_skill_set_engine_type
        )
        default_set = None
        if (
            default_skill_set_bolt_id is None
            or effective_bolt_id == default_skill_set_bolt_id
        ):
            default_set = self.get_default(
                user_id=parsed if user_id is not None and bolt_id is not None else None,
                bolt_id=effective_bolt_id
                if user_id is not None and bolt_id is not None
                else None,
                engine_type=effective_default_engine,
            )
        if default_set:
            enabled = self._get_user_default_enabled(
                user_id, effective_bolt_id, engine_type=engine_type
            )
            if enabled is None or enabled == 1:
                active_sets.append(default_set)
        global_default = self.get_default(
            user_id=None,
            bolt_id=default_skill_set_bolt_id,
            engine_type=effective_default_engine,
        )
        if global_default and all(
            skill_set["id"] != global_default["id"] for skill_set in active_sets
        ):
            active_sets.append(global_default)
        return active_sets

    def get_all_active_skill_sets_for_env(
        self,
        *,
        user_id: Optional[str] = None,
        bolt_id: Optional[str] = None,
        engine_type: Optional[str] = None,
        env: str,
        default_skill_set_bolt_id: Optional[str] = None,
        default_skill_set_engine_type: Optional[str] = None,
    ) -> List[dict]:
        effective_bolt_id = bolt_id if bolt_id else "default"
        parsed = _normalize_user_id(user_id)
        active_sets: list[dict] = []
        with self._db.orm_session() as db:
            query = db.query(self.SkillSet).filter(
                self.SkillSet.is_active == True,  # noqa: E712
                self.SkillSet.is_default == False,  # noqa: E712
                self.SkillSet.env == env,
                self.SkillSet.bolt_id == effective_bolt_id,
            )
            if engine_type is not None:
                query = query.filter(self.SkillSet.engine_type == engine_type)
            if user_id:
                query = query.filter(self.SkillSet.user_id == parsed)
            else:
                query = query.filter(self.SkillSet.user_id.is_(None))
            active_sets.extend(_skill_set_to_dict(ss) for ss in query.all())

        effective_default_engine = (
            engine_type
            if default_skill_set_engine_type is None
            else default_skill_set_engine_type
        )
        bot_default = None
        if (
            default_skill_set_bolt_id is None
            or effective_bolt_id == default_skill_set_bolt_id
        ):
            bot_default = self.get_default(
                user_id=parsed if user_id is not None and bolt_id is not None else None,
                bolt_id=effective_bolt_id
                if user_id is not None and bolt_id is not None
                else None,
                engine_type=effective_default_engine,
                env=env,
            )
        if bot_default:
            active_sets.append(bot_default)
        global_default = self.get_default(
            user_id=None,
            bolt_id=default_skill_set_bolt_id,
            engine_type=effective_default_engine,
            env=env,
        )
        if global_default and all(
            skill_set["id"] != global_default["id"] for skill_set in active_sets
        ):
            active_sets.append(global_default)
        return active_sets

    def _get_user_default_enabled(
        self,
        user_id: Optional[str],
        bolt_id: str,
        engine_type: Optional[str] = None,
    ) -> Optional[int]:
        # ac_user_default_skill_set is decommissioned — always None.
        return None

    def _set_user_default_enabled(
        self,
        user_id: Optional[str],
        bolt_id: str,
        is_enabled: int,
        engine_type: Optional[str] = None,
    ) -> bool:
        # ac_user_default_skill_set is decommissioned — no-op.
        return True

    def activate_skill_set(
        self,
        skill_set_id: str,
        user_id: Optional[str] = None,
        bolt_id: Optional[str] = None,
        engine_type: Optional[str] = None,
    ) -> bool:
        effective_bolt_id = bolt_id if bolt_id else "default"
        parsed = _normalize_user_id(user_id)
        skill_set = self.get_by_id(skill_set_id)
        if skill_set and skill_set.get("is_default"):
            return self._set_user_default_enabled(
                user_id,
                effective_bolt_id,
                is_enabled=1,
                engine_type=engine_type,
            )
        with self._db.orm_session() as db:
            q = db.query(self.SkillSet).filter(
                self.SkillSet.id == int(skill_set_id),
                self.SkillSet.env == get_current_env(),
                self.SkillSet.is_default == False,  # noqa: E712
                or_(
                    self.SkillSet.bolt_id == effective_bolt_id,
                    self.SkillSet.bolt_id.is_(None),
                ),
            )
            if user_id:
                q = q.filter(self.SkillSet.user_id == parsed)
            else:
                q = q.filter(self.SkillSet.user_id.is_(None))
            affected = q.update(
                {
                    self.SkillSet.is_active: 1,
                    self.SkillSet.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )
            return affected > 0

    def deactivate_skill_set(
        self,
        skill_set_id: str,
        user_id: Optional[str] = None,
        bolt_id: Optional[str] = None,
        engine_type: Optional[str] = None,
    ) -> bool:
        effective_bolt_id = bolt_id if bolt_id else "default"
        parsed = _normalize_user_id(user_id)
        skill_set = self.get_by_id(skill_set_id)
        if skill_set and skill_set.get("is_default"):
            return self._set_user_default_enabled(
                user_id,
                effective_bolt_id,
                is_enabled=0,
                engine_type=engine_type,
            )
        with self._db.orm_session() as db:
            q = db.query(self.SkillSet).filter(
                self.SkillSet.id == int(skill_set_id),
                self.SkillSet.env == get_current_env(),
                self.SkillSet.is_default == False,  # noqa: E712
                or_(
                    self.SkillSet.bolt_id == effective_bolt_id,
                    self.SkillSet.bolt_id.is_(None),
                ),
            )
            if user_id:
                q = q.filter(self.SkillSet.user_id == parsed)
            else:
                q = q.filter(self.SkillSet.user_id.is_(None))
            affected = q.update(
                {
                    self.SkillSet.is_active: 0,
                    self.SkillSet.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )
            return affected > 0

    def activate_default_skillset(self) -> int:
        with self._db.orm_session() as db:
            return (
                db.query(self.SkillSet)
                .filter(
                    self.SkillSet.is_default == True,  # noqa: E712
                    self.SkillSet.env == get_current_env(),
                )
                .update(
                    {
                        self.SkillSet.is_active: 1,
                        self.SkillSet.gmt_modified: func.now(),
                    },
                    synchronize_session=False,
                )
            )

    def delete_by_bot_id(self, bot_id: str, owner_id: str) -> int:
        env = get_current_env()
        with self._db.transactional_orm_session() as db:
            ids = [
                r[0]
                for r in db.query(self.SkillSet.id)
                .filter(
                    self.SkillSet.bolt_id == bot_id,
                    self.SkillSet.user_id == _normalize_user_id(owner_id),
                    self.SkillSet.env == env,
                )
                .order_by(self.SkillSet.id)
                .with_for_update()
                .all()
            ]
            count = len(ids)
            if count == 0:
                return 0
            from .skill_set_cleanup import delete_set_rows

            delete_set_rows(db, self, ids)
            return count

    def set_skillset_active(self, env: str, skillset_id: str, is_active: int) -> int:
        with self._db.orm_session() as db:
            return (
                db.query(self.SkillSet)
                .filter(
                    self.SkillSet.id == int(skillset_id),
                    self.SkillSet.env == env,
                )
                .update(
                    {
                        self.SkillSet.is_active: is_active,
                        self.SkillSet.gmt_modified: func.now(),
                    },
                    synchronize_session=False,
                )
            )
