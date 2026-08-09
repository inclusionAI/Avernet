"""Unified Skill / SkillSet repositories (prod OceanBase + local SQLite).

Two ORM implementations behind the ``SkillRepository`` and
``SkillSetRepository`` Protocols. The only per-environment difference
is the injected :class:`DatabasePlugin`: ``orm_session()`` yields a
SQLAlchemy ``Session`` in both runtimes, so this single body runs
unchanged on OceanBase (prod) and SQLite (local), collapsing the
previous raw-SQL/ORM twins so CI exercises the prod path too.

This is a faithful ORM port of the **prod** relational-store Skill and
SkillSet repositories — prod behavior is the contract and
becomes the new local/test behavior (the old SQLite twin is
discarded, not preserved). Every method reproduces prod's exact
statement shape and predicates:

- ``create`` / ``add_skill_to_set`` / ``add_mcp_to_set`` are **plain
  INSERTs** (no upsert — these tables have no unique key in prod
  DDL). ``delete`` / ``delete_by_*`` are **hard DELETEs** (ac_skill /
  ac_skill_set have no soft-delete column). Single UPDATEs stay
  single. ``gmt_modified=func.now()`` matches prod's literal
  ``NOW()``.
- ``delete_by_name_with_cascade`` / ``delete_by_bot_id`` /
  ``set_active_skill_set`` keep prod's multi-statement shape; each
  prod statement group runs in its own ``orm_session`` exactly as
  prod opened a fresh connection per group (faithful reproduction —
  not artificially fused).
- ``add_default_mcp_exclusion`` is the **only** genuine upsert in S4
  (prod ``INSERT ... ON DUPLICATE KEY UPDATE`` on
  ``uk_user_bot_skillset_mcp``). Implemented with the live
  dialect-aware pattern from
  ``expert_chat_repository.add_chat_bot`` (SQLite
  ``on_conflict_do_update``, MySQL ``on_duplicate_key_update``) — a
  pure dialect branch, no runtime-mode awareness.
- Env predicates are matched **per method** to prod: most are strict
  ``env == current``; a few (``SkillSet.get_by_id`` / ``list_all`` /
  ``list_all_exclude_deleted`` / ``set_active_skill_set`` /
  ``activate``/``deactivate``) are deliberately
  ``env == current OR env IS NULL`` exactly where prod is.
- ``user_id`` int<->str coercion uses prod's ``_parse_user_id``
  (None→None, "anonymous"→0, non-numeric→0) / ``_format_user_id``
  (0→"anonymous"). Return dicts reproduce prod's ``_row_to_dict``
  shapes (NOT the ORM models' ``to_dict()``).
"""
from __future__ import annotations

import json
from hashlib import sha256
from datetime import datetime
from typing import List, Optional

from injector import inject
from sqlalchemy import and_, func, or_

from agentclaw.community.log import get_logger
from agentclaw.community.core.skill_center.errors import ActiveSkillSetReferenceError
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository, SkillSetRepository
from agentclaw.community.core.repository.protocols.skills_pool import SkillsPoolSkillRepositoryProtocol

logger = get_logger()


def _format_datetime(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _normalize_user_id(user_id: Optional[str]) -> Optional[str]:
    """Normalize user_id for string-backed persistence (dev rename of
    the old ``_parse_user_id``). The underlying column is now
    VARCHAR(128) on prod — see
    core/skill_center/sql/user_id_int_to_varchar.sql. No more
    "anonymous"→0 coercion; user_id is stored verbatim as a string.
    """
    if user_id is None:
        return None
    return str(user_id)


def _format_user_id(user_id) -> Optional[str]:
    """Return user_id from storage as a string (NULL preserved). The
    0→"anonymous" mapping is gone — callers now pass and receive
    "anonymous" as the literal user_id string."""
    if user_id is None:
        return None
    return str(user_id)


def _skill_to_dict(s) -> dict:
    """Reproduces the prod Skill repository's _row_to_dict from a Skill ORM row."""
    git_path = s.git_path
    if git_path and git_path.startswith("git://"):
        link_name = git_path[6:].replace("/", "_")
    else:
        link_name = None
    risk_tags = s.risk_tags
    mcp_deps = s.mcp_dependencies
    return {
        "id": str(s.id),
        "name": s.name,
        "description": s.description,
        "git_path": git_path,
        "link_name": link_name,
        "category": s.category,
        "tags": s.tags,
        "input_schema": s.input_schema,
        "output_schema": s.output_schema,
        "is_public": bool(s.is_public),
        "is_builtin": bool(s.is_builtin),
        "user_id": _format_user_id(s.user_id),
        "gmt_created": _format_datetime(s.gmt_created),
        "gmt_modified": _format_datetime(s.gmt_modified),
        "risk_tags": json.loads(risk_tags) if risk_tags else [],
        "mcp_dependencies": json.loads(mcp_deps) if mcp_deps else [],
        "bolt_id": s.bolt_id if s.bolt_id else "default",
        "env": s.env if s.env else get_current_env(),
        "status": s.status if s.status else "DEVELOPING",
        "version": s.version if s.version is not None else 1,
        "skill_uuid": s.skill_uuid,
        "source_type": s.source_type,
        "category_path": getattr(s, "category_path", None),
        "package_url": getattr(s, "package_url", None),
        "zip_url": getattr(s, "zip_url", None),
    }


def _skill_set_to_dict(ss) -> dict:
    """Reproduces the prod SkillSet repository's _row_to_dict from a SkillSet row."""
    return {
        "id": str(ss.id),
        "name": ss.name,
        "description": ss.description,
        "is_default": bool(ss.is_default),
        "is_builtin": bool(ss.is_builtin),
        "user_id": _format_user_id(ss.user_id),
        "gmt_created": _format_datetime(ss.gmt_created),
        "gmt_modified": _format_datetime(ss.gmt_modified),
        "bolt_id": ss.bolt_id if ss.bolt_id else "default",
        "env": ss.env if ss.env else get_current_env(),
        "is_active": bool(ss.is_active) if ss.is_active is not None else False,
        "engine_type": ss.engine_type,
    }


def _mcp_assoc_to_dict(m) -> dict:
    return {
        "id": str(m.id),
        "skill_set_id": str(m.skill_set_id),
        "server_code": m.server_code,
        "name": m.name,
        "description": m.description,
        "icon": m.icon,
        "user_id": m.user_id,
        "gmt_created": _format_datetime(m.gmt_created),
        "gmt_modified": _format_datetime(m.gmt_modified),
        "env": m.env,
    }


class SkillRepository(
    SkillRepository,
    SkillsPoolSkillRepositoryProtocol,
):
    """Unified ORM ``SkillRepository`` — faithful prod port."""

    @inject
    def __init__(self, db: DatabasePlugin):
        from agentclaw.community.core.models import Skill, SkillSet, SkillSetSkill

        self._db = db
        self.Skill = Skill
        self.SkillSet = SkillSet
        self.SkillSetSkill = SkillSetSkill

    def get_by_id(self, skill_id: str) -> Optional[dict]:
        with self._db.orm_session() as db:
            s = (
                db.query(self.Skill)
                .filter(
                    self.Skill.id == int(skill_id),
                    self.Skill.env == get_current_env(),
                )
                .first()
            )
            return _skill_to_dict(s) if s else None

    def get_by_uuid(
        self, skill_uuid: str, env: str | None = None
    ) -> Optional[dict]:
        env = env or get_current_env()
        with self._db.orm_session() as db:
            s = (
                db.query(self.Skill)
                .filter(
                    self.Skill.skill_uuid == skill_uuid,
                    self.Skill.env == env,
                    self.Skill.status == "PUBLISHED",
                )
                .first()
            )
            return _skill_to_dict(s) if s else None

    def get_by_git_path(self, git_path: str) -> Optional[dict]:
        with self._db.orm_session() as db:
            s = (
                db.query(self.Skill)
                .filter(
                    self.Skill.git_path == git_path,
                    self.Skill.env == get_current_env(),
                )
                .first()
            )
            return _skill_to_dict(s) if s else None

    def get_by_link_name(
        self, link_name: str, bolt_id: Optional[str] = None
    ) -> Optional[dict]:
        # prod: WHERE REPLACE(SUBSTRING(git_path,7),'/','_') = %s AND env=%s
        with self._db.orm_session() as db:
            git_expr = func.replace(
                func.substr(self.Skill.git_path, 7), "/", "_"
            )
            query = db.query(self.Skill).filter(
                git_expr == link_name,
                self.Skill.env == get_current_env(),
            )
            if bolt_id:
                query = query.filter(
                    or_(
                        self.Skill.bolt_id == bolt_id,
                        self.Skill.bolt_id.is_(None),
                    )
                )
            s = query.limit(1).first()
            if s:
                d = _skill_to_dict(s)
                d["link_name"] = link_name
                return d
            return None

    def get_by_name_global(
        self, name: str, user_id: Optional[str] = None
    ) -> Optional[dict]:
        from agentclaw.community.plugin_api.models import BotModel

        with self._db.orm_session() as db:
            query = (
                db.query(self.Skill)
                .outerjoin(
                    BotModel,
                    and_(
                        self.Skill.bolt_id == BotModel.bot_id,
                        self.Skill.env == BotModel.env,
                    ),
                )
                .filter(
                    self.Skill.name == name,
                    self.Skill.env == get_current_env(),
                    or_(
                        BotModel.is_delete == 0,
                        BotModel.is_delete.is_(None),
                    ),
                )
            )
            if user_id:
                query = query.filter(
                    self.Skill.user_id == _normalize_user_id(user_id)
                )
            s = query.limit(1).first()
            return _skill_to_dict(s) if s else None

    def get_published_ids_by_names(
        self,
        names: list[str],
        env: Optional[str] = None,
        category_path: Optional[str] = None,
    ) -> dict[str, dict]:
        # NOTE (prod-bug surfaced, not reproduced): the prod twin had
        # a ``while cursor.fetchone(): pass`` *inside* the row loop
        # that drained all remaining rows on the first iteration, so
        # prod silently returned only the FIRST matching name. A
        # faithful ORM port cannot sanely reproduce a cursor-drain
        # bug; this returns the full {name: skill} map. Flagged in
        # the PR body — callers must tolerate >1 result (the correct
        # behavior). Same class of latent prod bug as get_by_uuid's
        # category_path column-index drift.
        if not names:
            return {}
        effective_env = env or get_current_env()
        with self._db.orm_session() as db:
            query = db.query(self.Skill).filter(
                self.Skill.name.in_(names),
                self.Skill.env == effective_env,
                self.Skill.status == "PUBLISHED",
            )
            if category_path:
                query = query.filter(
                    func.concat("/", category_path, "/").like(
                        func.concat(self.Skill.category_path, "%")
                    )
                )
            return {r.name: _skill_to_dict(r) for r in query.all()}

    def get_by_name_global_include_deleted(
        self, name: str, user_id: Optional[str] = None
    ) -> Optional[dict]:
        with self._db.orm_session() as db:
            query = db.query(self.Skill).filter(
                self.Skill.name == name,
                self.Skill.env == get_current_env(),
            )
            if user_id:
                query = query.filter(
                    self.Skill.user_id == _normalize_user_id(user_id)
                )
            s = query.limit(1).first()
            return _skill_to_dict(s) if s else None

    def list_skills(
        self,
        user_id: Optional[str] = None,
        bolt_id: Optional[str] = "default",
        env: Optional[str] = None,
    ) -> List[dict]:
        with self._db.orm_session() as db:
            query = db.query(self.Skill).filter(
                self.Skill.env == (env or get_current_env())
            )
            if user_id:
                query = query.filter(
                    self.Skill.user_id == _normalize_user_id(user_id)
                )
            if bolt_id is not None:
                effective_bolt_id = bolt_id if bolt_id else "default"
                query = query.filter(
                    or_(
                        self.Skill.bolt_id == effective_bolt_id,
                        self.Skill.bolt_id.is_(None),
                    )
                )
            query = query.order_by(self.Skill.gmt_created.desc())
            return [_skill_to_dict(s) for s in query.all()]

    def get_bot_local_by_name(
        self,
        *,
        bot_id: str,
        name: str,
        user_id: str | None = None,
    ) -> dict | None:
        """精确命中 Bot-owned local 行，禁止回退到 ``bolt_id IS NULL``。"""

        with self._db.orm_session() as db:
            query = db.query(self.Skill).filter(
                self.Skill.env == get_current_env(),
                self.Skill.bolt_id == bot_id,
                self.Skill.name == name,
                self.Skill.git_path.like("local://%"),
            )
            if user_id is not None:
                query = query.filter(
                    self.Skill.user_id == _normalize_user_id(user_id)
                )
            row = query.order_by(self.Skill.gmt_created.desc()).first()
            return _skill_to_dict(row) if row is not None else None

    def list_bot_local_by_name(self, *, bot_id: str, name: str) -> list[dict]:
        """Return all exact same-name Local rows; never hide legacy duplicates."""
        with self._db.orm_session() as db:
            rows = (
                db.query(self.Skill)
                .filter(
                    self.Skill.env == get_current_env(),
                    self.Skill.bolt_id == bot_id,
                    self.Skill.name == name,
                    self.Skill.git_path.like("local://%"),
                )
                .order_by(self.Skill.id.asc())
                .all()
            )
            return [_skill_to_dict(row) for row in rows]

    def get_bot_local_by_locator(
        self, *, bot_id: str, user_id: str, locator: str
    ) -> dict | None:
        with self._db.orm_session() as db:
            row = db.query(self.Skill).filter(
                self.Skill.env == get_current_env(),
                self.Skill.bolt_id == bot_id,
                self.Skill.user_id == _normalize_user_id(user_id),
                self.Skill.git_path == f"local://{locator}",
            ).one_or_none()
            return _skill_to_dict(row) if row is not None else None

    @staticmethod
    def _public_local_skill(row, active: bool) -> dict:
        data = _skill_to_dict(row)
        data["active"] = bool(active)
        return data

    def list_bot_local_skills(
        self,
        *,
        bot_id: str,
        user_id: str,
        page: int,
        page_size: int,
        active: bool | None,
        keyword: str | None,
    ) -> tuple[int, list[dict]]:
        """Page exact Bot-owned ``local://`` rows by desired active state."""
        from agentclaw.community.core.skill_center.orm import DefaultSkillsetSkillExclusion

        with self._db.orm_session() as db:
            excluded = (
                db.query(DefaultSkillsetSkillExclusion.id)
                .filter(
                    DefaultSkillsetSkillExclusion.user_id
                    == _normalize_user_id(user_id),
                    DefaultSkillsetSkillExclusion.bot_id == bot_id,
                    DefaultSkillsetSkillExclusion.skill_id == self.Skill.id,
                )
                .exists()
            )
            query = db.query(self.Skill, (~excluded).label("active")).filter(
                self.Skill.env == get_current_env(),
                self.Skill.bolt_id == bot_id,
                self.Skill.user_id == _normalize_user_id(user_id),
                self.Skill.git_path.like("local://%"),
            )
            if keyword and keyword.strip():
                term = f"%{keyword.strip().lower()}%"
                query = query.filter(
                    or_(
                        func.lower(self.Skill.name).like(term),
                        func.lower(func.coalesce(self.Skill.description, "")).like(
                            term
                        ),
                    )
                )
            if active is not None:
                query = query.filter((~excluded) == active)
            total = query.count()
            rows = (
                query.order_by(self.Skill.gmt_modified.desc(), self.Skill.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            return total, [
                self._public_local_skill(row, is_active) for row, is_active in rows
            ]

    def get_bot_local_skill(
        self, *, skill_id: str, bot_id: str, user_id: str
    ) -> dict | None:
        """Return one exact Bot-owned ``local://`` row or no row."""
        from agentclaw.community.core.skill_center.orm import DefaultSkillsetSkillExclusion

        with self._db.orm_session() as db:
            excluded = (
                db.query(DefaultSkillsetSkillExclusion.id)
                .filter(
                    DefaultSkillsetSkillExclusion.user_id
                    == _normalize_user_id(user_id),
                    DefaultSkillsetSkillExclusion.bot_id == bot_id,
                    DefaultSkillsetSkillExclusion.skill_id == self.Skill.id,
                )
                .exists()
            )
            row = (
                db.query(self.Skill, (~excluded).label("active"))
                .filter(
                    self.Skill.id == int(skill_id),
                    self.Skill.env == get_current_env(),
                    self.Skill.bolt_id == bot_id,
                    self.Skill.user_id == _normalize_user_id(user_id),
                    self.Skill.git_path.like("local://%"),
                )
                .one_or_none()
            )
            return self._public_local_skill(*row) if row else None

    def list_bot_local_assets(
        self,
        *,
        env: str,
        bot_id: str,
    ):
        """列出精确 Bot 范围内的全部 local 来源行，不包含全局记录。"""

        from agentclaw.community.core.skills_pool.models import (
            RegisteredSkillAsset,
        )

        with self._db.orm_session() as db:
            rows = (
                db.query(self.Skill)
                .filter(
                    self.Skill.env == env,
                    self.Skill.bolt_id == bot_id,
                    self.Skill.git_path.like("local://%"),
                )
                .order_by(self.Skill.id)
                .all()
            )
            return [
                RegisteredSkillAsset(
                    skill_id=row.id,
                    name=row.name,
                    git_path=row.git_path,
                )
                for row in rows
            ]

    def list_bot_active_assets(
        self,
        *,
        env: str,
        bot_id: str,
        user_id: str,
        engine: str,
    ):
        """返回普通 active sets 与该引擎默认 set 的完整、去重来源。"""

        from agentclaw.community.core.skills_pool.models import (
            RegisteredSkillAsset,
        )

        skill_sets = SkillSetRepository(self._db)
        active_sets = skill_sets.get_all_active_skill_sets_for_env(
            user_id=user_id,
            bolt_id=bot_id,
            engine_type=engine,
            env=env,
        )
        seen: set[str] = set()
        assets: list[RegisteredSkillAsset] = []
        for skill_set in active_sets:
            rows = skill_sets.get_skills_in_set_for_env(
                str(skill_set["id"]),
                env=env,
            )
            if skill_set.get("is_default"):
                excluded = set(
                    skill_sets.get_excluded_skills(
                        user_id=user_id,
                        bot_id=bot_id,
                        skill_set_id=int(skill_set["id"]),
                    )
                )
                rows = [
                    row
                    for row in rows
                    if int(row.get("id", 0)) not in excluded
                ]
            for row in rows:
                git_path = str(row.get("git_path") or "")
                if not git_path or git_path in seen:
                    continue
                seen.add(git_path)
                assets.append(
                    RegisteredSkillAsset(
                        skill_id=int(row["id"]),
                        name=str(row["name"]),
                        git_path=git_path,
                    )
                )
        return assets

    def create(self, skill_data: dict) -> dict:
        with self._db.orm_session() as db:
            tags = skill_data.get("tags")
            risk_tags = skill_data.get("risk_tags")
            mcp_deps = skill_data.get("mcp_dependencies")
            skill = self.Skill(
                name=skill_data["name"],
                description=skill_data.get("description"),
                git_path=skill_data.get("git_path"),
                category=skill_data.get("category"),
                tags=json.dumps(tags)
                if isinstance(tags, (list, dict))
                else tags,
                input_schema=skill_data.get("input_schema"),
                output_schema=skill_data.get("output_schema"),
                is_public=skill_data.get("is_public", True),
                is_builtin=skill_data.get("is_builtin", False),
                user_id=_normalize_user_id(skill_data.get("user_id")),
                risk_tags=json.dumps(risk_tags)
                if isinstance(risk_tags, (list, dict))
                else risk_tags,
                mcp_dependencies=json.dumps(mcp_deps)
                if isinstance(mcp_deps, (list, dict))
                else mcp_deps,
                bolt_id=skill_data.get("bolt_id")
                if skill_data.get("bolt_id")
                else "default",
                env=get_current_env(),
                status=skill_data.get("status", "DEVELOPING"),
                version=skill_data.get("version", 1),
                skill_uuid=skill_data.get("skill_uuid"),
                source_type=skill_data.get("source_type"),
                category_path=skill_data.get("category_path"),
                package_url=skill_data.get("package_url"),
                zip_url=skill_data.get("zip_url"),
            )
            db.add(skill)
            db.flush()
            new_id = str(skill.id)
        return self.get_by_id(new_id)

    def update(
        self, skill_id: str, skill_data: dict
    ) -> Optional[dict]:
        existing = self.get_by_id(skill_id)
        if not existing:
            return None

        def _j(v):
            return (
                json.dumps(v) if isinstance(v, (list, dict)) else v
            )

        tags = skill_data.get("tags", existing.get("tags"))
        input_schema = skill_data.get(
            "input_schema", existing.get("input_schema")
        )
        output_schema = skill_data.get(
            "output_schema", existing.get("output_schema")
        )
        mcp_dependencies = skill_data.get(
            "mcp_dependencies", existing.get("mcp_dependencies")
        )
        risk_tags = skill_data.get(
            "risk_tags", existing.get("risk_tags")
        )
        values = {
            self.Skill.name: skill_data.get("name", existing["name"]),
            self.Skill.description: skill_data.get(
                "description", existing.get("description")
            ),
            self.Skill.git_path: skill_data.get(
                "git_path", existing.get("git_path")
            ),
            self.Skill.category: skill_data.get(
                "category", existing.get("category")
            ),
            self.Skill.tags: _j(tags),
            self.Skill.input_schema: _j(input_schema),
            self.Skill.output_schema: _j(output_schema),
            self.Skill.is_public: skill_data.get(
                "is_public", existing.get("is_public", True)
            ),
            self.Skill.user_id: _normalize_user_id(
                skill_data.get("user_id", existing.get("user_id"))
            ),
            self.Skill.status: skill_data.get(
                "status", existing.get("status")
            ),
            self.Skill.version: skill_data.get(
                "version", existing.get("version")
            ),
            self.Skill.skill_uuid: existing.get("skill_uuid"),
            self.Skill.source_type: skill_data.get(
                "source_type", existing.get("source_type")
            ),
            self.Skill.category_path: skill_data.get(
                "category_path", existing.get("category_path")
            ),
            self.Skill.package_url: skill_data.get(
                "package_url", existing.get("package_url")
            ),
            self.Skill.zip_url: skill_data.get(
                "zip_url", existing.get("zip_url")
            ),
            self.Skill.mcp_dependencies: _j(mcp_dependencies),
            self.Skill.risk_tags: _j(risk_tags),
            self.Skill.gmt_modified: func.now(),
        }
        with self._db.orm_session() as db:
            db.query(self.Skill).filter(
                self.Skill.id == int(skill_id),
                self.Skill.env == get_current_env(),
            ).update(values, synchronize_session=False)
        return self.get_by_id(skill_id)

    def delete(self, skill_id: str) -> bool:
        """Delete one Skill and its set associations as one transaction.

        This remains a bulk-delete repository method, so SQLAlchemy cannot
        apply ``Skill.skill_sets``' ORM cascade. Remove matching association
        rows explicitly before deleting the Skill. A real transaction keeps
        association cleanup and the Skill-row delete atomic and propagates
        every database error to the caller.
        """
        with self._db.transactional_orm_session() as db:
            self._delete_skill_set_associations(db, int(skill_id))
            rowcount = (
                db.query(self.Skill)
                .filter(
                    self.Skill.id == int(skill_id),
                    self.Skill.env == get_current_env(),
                )
                .delete(synchronize_session=False)
            )
            return rowcount > 0

    def replace_bot_local_skill(
        self,
        *,
        skill_id: str,
        owner_id: str,
        bot_id: str,
        old_locator: str,
        new_locator: str,
        description: str,
        requires_runtime_restore: bool,
        cleanup_work_id: int,
    ) -> int | None:
        """Atomically switch one Local Skill and commit obsolete-package work."""
        from agentclaw.community.core.skill_center.local_skill_cleanup import (
            LocalSkillCleanupWorkModel,
        )

        with self._db.transactional_orm_session() as db:
            locator_hash = sha256(old_locator.encode("utf-8")).hexdigest()
            cleanup = (
                db.query(LocalSkillCleanupWorkModel)
                .filter(
                    LocalSkillCleanupWorkModel.id == cleanup_work_id,
                    LocalSkillCleanupWorkModel.env == get_current_env(),
                    LocalSkillCleanupWorkModel.owner_id == owner_id,
                    LocalSkillCleanupWorkModel.bot_id == bot_id,
                    LocalSkillCleanupWorkModel.package_locator == old_locator,
                    LocalSkillCleanupWorkModel.package_locator_hash == locator_hash,
                    LocalSkillCleanupWorkModel.status == "preparing",
                )
                .with_for_update()
                .one_or_none()
            )
            if cleanup is None:
                raise RuntimeError("Local Skill cleanup preparation is missing")
            skill = (
                db.query(self.Skill)
                .filter(
                    self.Skill.id == int(skill_id),
                    self.Skill.env == get_current_env(),
                    self.Skill.user_id == _normalize_user_id(owner_id),
                    self.Skill.bolt_id == bot_id,
                    self.Skill.git_path == f"local://{old_locator}",
                )
                .with_for_update()
                .one_or_none()
            )
            if skill is None:
                return None
            skill.description = description
            skill.git_path = f"local://{new_locator}"
            skill.user_id = _normalize_user_id(owner_id)
            skill.gmt_modified = func.now()
            cleanup.requires_runtime_restore = requires_runtime_restore
            cleanup.status = "pending"
            cleanup.last_error = None
            cleanup.cleaned_at = None
            db.flush()
            return int(cleanup.id)

    @staticmethod
    def _delete_skill_set_associations(db, skill_id: int) -> None:
        """Apply #684's association cleanup inside the caller's transaction."""
        from agentclaw.community.core.models import SkillSetSkill

        db.query(SkillSetSkill).filter(
            SkillSetSkill.skill_id == skill_id,
            SkillSetSkill.env == get_current_env(),
        ).delete(synchronize_session=False)

    def delete_bot_local_skill(
        self,
        *,
        skill_id: str,
        owner_id: str,
        bot_id: str,
        quarantine_locator: str,
        cleanup_work_id: int,
    ) -> int | None:
        """Atomically delete one inactive Local Skill and commit its purge."""
        from agentclaw.community.core.models import SkillSetSkill
        from agentclaw.community.core.skill_center.orm import DefaultSkillsetSkillExclusion
        from agentclaw.community.core.skill_center.local_skill_cleanup import (
            LocalSkillCleanupWorkModel,
        )

        with self._db.transactional_orm_session() as db:
            locator_hash = sha256(quarantine_locator.encode("utf-8")).hexdigest()
            cleanup = db.query(LocalSkillCleanupWorkModel).filter(
                LocalSkillCleanupWorkModel.id == cleanup_work_id,
                LocalSkillCleanupWorkModel.env == get_current_env(),
                LocalSkillCleanupWorkModel.owner_id == owner_id,
                LocalSkillCleanupWorkModel.bot_id == bot_id,
                LocalSkillCleanupWorkModel.package_locator == quarantine_locator,
                LocalSkillCleanupWorkModel.package_locator_hash == locator_hash,
                LocalSkillCleanupWorkModel.status.in_(("preparing", "repair_required")),
            ).with_for_update().one_or_none()
            if cleanup is None:
                raise RuntimeError("Local Skill quarantine preparation is missing")
            skill = db.query(self.Skill).filter(
                self.Skill.id == int(skill_id),
                self.Skill.env == get_current_env(),
                self.Skill.user_id == _normalize_user_id(owner_id),
                self.Skill.bolt_id == bot_id,
                self.Skill.git_path.like("local://%"),
            ).one_or_none()
            if skill is None:
                return None
            active_custom_reference = (
                db.query(self.SkillSet.id)
                .join(SkillSetSkill, SkillSetSkill.skill_set_id == self.SkillSet.id)
                .filter(
                    SkillSetSkill.skill_id == int(skill_id),
                    SkillSetSkill.env == get_current_env(),
                    self.SkillSet.env == get_current_env(),
                    self.SkillSet.user_id == _normalize_user_id(owner_id),
                    self.SkillSet.bolt_id == bot_id,
                    self.SkillSet.is_default == False,  # noqa: E712
                    self.SkillSet.is_active == True,  # noqa: E712
                )
                .with_for_update()
                .first()
            )
            if active_custom_reference is not None:
                raise ActiveSkillSetReferenceError()
            db.query(DefaultSkillsetSkillExclusion).filter(
                DefaultSkillsetSkillExclusion.user_id == _normalize_user_id(owner_id),
                DefaultSkillsetSkillExclusion.bot_id == bot_id,
                DefaultSkillsetSkillExclusion.skill_id == int(skill_id),
            ).delete(synchronize_session=False)
            self._delete_skill_set_associations(db, int(skill_id))
            db.delete(skill)
            cleanup.status = "pending"
            cleanup.last_error = None
            db.flush()
            return int(cleanup.id)

    def check_skill_blocked_by_bot(
        self, name: str, env: Optional[str] = None
    ) -> list[str]:
        from agentclaw.community.core.models import SkillSetSkill

        effective_env = env or get_current_env()
        with self._db.orm_session() as db:
            rows = (
                db.query(self.SkillSet.bolt_id)
                .select_from(self.Skill)
                .join(
                    SkillSetSkill,
                    SkillSetSkill.skill_uuid == self.Skill.skill_uuid,
                )
                .join(
                    self.SkillSet,
                    self.SkillSet.id == SkillSetSkill.skill_set_id,
                )
                .filter(
                    self.Skill.name == name,
                    self.Skill.env == effective_env,
                    self.SkillSet.bolt_id.isnot(None),
                    self.SkillSet.bolt_id != "default",
                )
                .distinct()
                .all()
            )
            return [r[0] for r in rows]

    def delete_by_name_with_cascade(
        self, name: str, env: Optional[str] = None
    ) -> dict:
        from agentclaw.community.core.models import SkillSetSkill
        from agentclaw.community.core.models.skill import AcSkillMember

        effective_env = env or get_current_env()

        # 1. collect skill_uuids (own session — prod opens fresh conn)
        with self._db.orm_session() as db:
            uuids = [
                r[0]
                for r in db.query(self.Skill.skill_uuid)
                .filter(
                    self.Skill.name == name,
                    self.Skill.env == effective_env,
                )
                .distinct()
                .all()
                if r[0]
            ]

        cleaned_set_skill = 0
        cleaned_member = 0

        # 2. clean association tables by skill_uuid (one transaction)
        if uuids:
            with self._db.orm_session() as db:
                cleaned_set_skill = (
                    db.query(SkillSetSkill)
                    .filter(SkillSetSkill.skill_uuid.in_(uuids))
                    .delete(synchronize_session=False)
                )
                cleaned_member = (
                    db.query(AcSkillMember)
                    .filter(AcSkillMember.skill_uuid.in_(uuids))
                    .delete(synchronize_session=False)
                )

        # 3. delete the main table (all versions)
        with self._db.orm_session() as db:
            deleted_count = (
                db.query(self.Skill)
                .filter(
                    self.Skill.name == name,
                    self.Skill.env == effective_env,
                )
                .delete(synchronize_session=False)
            )

        return {
            "deleted_skill_count": deleted_count,
            "cleaned_set_skill": cleaned_set_skill,
            "cleaned_member": cleaned_member,
        }

    def update_risk_tags(
        self, skill_id: str, risk_tags: list
    ) -> Optional[dict]:
        with self._db.orm_session() as db:
            db.query(self.Skill).filter(
                self.Skill.id == int(skill_id),
                self.Skill.env == get_current_env(),
            ).update(
                {
                    self.Skill.risk_tags: json.dumps(risk_tags)
                    if risk_tags
                    else None,
                    self.Skill.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )
        return self.get_by_id(skill_id)

    def update_mcp_dependencies(
        self, skill_id: str, mcp_dependencies: list
    ) -> Optional[dict]:
        with self._db.orm_session() as db:
            db.query(self.Skill).filter(
                self.Skill.id == int(skill_id),
                self.Skill.env == get_current_env(),
            ).update(
                {
                    self.Skill.mcp_dependencies: json.dumps(
                        mcp_dependencies
                    )
                    if mcp_dependencies
                    else None,
                    self.Skill.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )
        return self.get_by_id(skill_id)

    def delete_by_bot_id(self, bot_id: str) -> int:
        env = get_current_env()
        with self._db.orm_session() as db:
            count = (
                db.query(self.Skill)
                .filter(
                    self.Skill.bolt_id == bot_id,
                    self.Skill.env == env,
                )
                .count()
            )
            if count > 0:
                db.query(self.Skill).filter(
                    self.Skill.bolt_id == bot_id,
                    self.Skill.env == env,
                ).delete(synchronize_session=False)
            return count

    def list_skill_set_references(
        self,
        skill_id: str,
        skill_uuid: str | None = None,
    ) -> list[dict]:
        """List live SkillSet associations, regardless of activation state."""
        from agentclaw.community.core.models import SkillSetSkill

        identity_filter = SkillSetSkill.skill_id == int(skill_id)
        if skill_uuid:
            identity_filter = or_(
                identity_filter,
                SkillSetSkill.skill_uuid == skill_uuid,
            )
        with self._db.orm_session() as db:
            rows = (
                db.query(SkillSetSkill.skill_set_id)
                .join(
                    self.SkillSet,
                    SkillSetSkill.skill_set_id == self.SkillSet.id,
                )
                .filter(
                    identity_filter,
                    SkillSetSkill.env == get_current_env(),
                    or_(
                        self.SkillSet.env == get_current_env(),
                        self.SkillSet.env.is_(None),
                    ),
                )
                .distinct()
                .order_by(SkillSetSkill.skill_set_id)
                .all()
            )
            return [{"skill_set_id": str(row[0])} for row in rows]

    def get_active_skills_by_bot(
        self,
        bot_id: str,
        entity_type: str,
        owner_id: str,
        env: str | None = None,
    ) -> list[dict]:
        from agentclaw.community.core.models import SkillSetSkill

        env = env or get_current_env()
        with self._db.orm_session() as db:
            rows = (
                db.query(
                    self.Skill.id,
                    self.Skill.name,
                    self.Skill.git_path,
                )
                .join(
                    SkillSetSkill,
                    self.Skill.id == SkillSetSkill.skill_id,
                )
                .join(
                    self.SkillSet,
                    SkillSetSkill.skill_set_id == self.SkillSet.id,
                )
                .filter(
                    self.SkillSet.bolt_id == bot_id,
                    self.SkillSet.is_active == True,  # noqa: E712
                    self.Skill.env == env,
                    SkillSetSkill.env == env,
                    self.SkillSet.env == env,
                )
                .order_by(SkillSetSkill.gmt_created)
                .all()
            )
            results = []
            for r in rows:
                git_path = r[2]
                if git_path and git_path.startswith("git://"):
                    link_name = git_path[6:].replace("/", "_")
                else:
                    link_name = r[1].replace("/", "_")
                results.append(
                    {
                        "id": str(r[0]),
                        "name": r[1],
                        "git_path": git_path,
                        "link_name": link_name,
                    }
                )
            return results

    def list_git_skills_with_order(
        self, orderby: Optional[str] = None
    ) -> list[dict]:
        from agentclaw.community.core.models import SkillSetSkill

        env = get_current_env()
        with self._db.orm_session() as db:
            git_filter = or_(
                self.Skill.git_path.like("git://%"),
                self.Skill.git_path.like("center://%"),
            )
            if orderby == "hotest":
                use_count = func.count(SkillSetSkill.skill_id)
                rows = (
                    db.query(self.Skill, use_count.label("uc"))
                    .outerjoin(
                        SkillSetSkill,
                        and_(
                            self.Skill.id == SkillSetSkill.skill_id,
                            self.Skill.env == SkillSetSkill.env,
                        ),
                    )
                    .filter(self.Skill.env == env, git_filter)
                    .group_by(self.Skill.id)
                    .order_by(
                        use_count.desc(),
                        self.Skill.gmt_created.desc(),
                    )
                    .all()
                )
                out = []
                for s, uc in rows:
                    d = _skill_to_dict(s)
                    d["use_count"] = int(uc) if uc is not None else 0
                    out.append(d)
                return out
            rows = (
                db.query(self.Skill)
                .filter(self.Skill.env == env, git_filter)
                .order_by(self.Skill.gmt_created.desc())
                .all()
            )
            out = []
            for s in rows:
                d = _skill_to_dict(s)
                d["use_count"] = 0
                out.append(d)
            return out

    def list_published_center_skills(self) -> list[dict]:
        with self._db.orm_session() as db:
            rows = (
                db.query(
                    self.Skill.id,
                    self.Skill.name,
                    self.Skill.skill_uuid,
                    self.Skill.git_path,
                    self.Skill.status,
                )
                .filter(
                    self.Skill.git_path.like("center://%"),
                    self.Skill.status == "PUBLISHED",
                    self.Skill.env == get_current_env(),
                )
                .all()
            )
            return [
                {
                    "id": r[0],
                    "name": r[1],
                    "skill_uuid": r[2],
                    "git_path": r[3],
                    "status": r[4],
                }
                for r in rows
            ]


class SkillSetRepository(
    SkillSetRepository,
):
    """Unified ORM ``SkillSetRepository`` — faithful prod port."""

    @inject
    def __init__(self, db: DatabasePlugin):
        from agentclaw.community.core.models import (
            Skill,
            SkillSet,
            SkillSetMCPServer,
            SkillSetSkill,
        )

        self._db = db
        self.Skill = Skill
        self.SkillSet = SkillSet
        self.SkillSetSkill = SkillSetSkill
        self.SkillSetMCPServer = SkillSetMCPServer

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
                query = query.filter(
                    self.SkillSet.engine_type == engine_type
                )
            if bolt_id is not None:
                query = query.filter(self.SkillSet.bolt_id == bolt_id)
            ss = query.limit(1).first()
            return _skill_set_to_dict(ss) if ss else None

    def _list_all_query(
        self, db, user_id, bolt_id, engine_type
    ):
        query = db.query(self.SkillSet).filter(
            or_(
                self.SkillSet.env == get_current_env(),
                self.SkillSet.env.is_(None),
            )
        )
        effective_bolt_id = bolt_id if bolt_id else "default"
        if engine_type is not None:
            query = query.filter(
                or_(
                    and_(
                        self.SkillSet.is_default == False,  # noqa: E712
                        self.SkillSet.bolt_id == effective_bolt_id,
                    ),
                    and_(
                        self.SkillSet.is_default == True,  # noqa: E712
                        self.SkillSet.engine_type == engine_type,
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
    ) -> List[dict]:
        with self._db.orm_session() as db:
            rows = self._list_all_query(
                db, user_id, bolt_id, engine_type
            ).all()
            return [_skill_set_to_dict(s) for s in rows]

    def list_all_exclude_deleted(
        self,
        user_id: Optional[str] = None,
        bolt_id: Optional[str] = None,
        engine_type: Optional[str] = None,
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
            if engine_type is not None:
                query = query.filter(
                    or_(
                        and_(
                            self.SkillSet.is_default == False,  # noqa: E712
                            self.SkillSet.bolt_id == effective_bolt_id,
                        ),
                        and_(
                            self.SkillSet.is_default == True,  # noqa: E712
                            self.SkillSet.engine_type == engine_type,
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
                        self.SkillSet.user_id
                        == _normalize_user_id(user_id),
                        self.SkillSet.is_default == True,  # noqa: E712
                        self.SkillSet.is_builtin == True,  # noqa: E712
                    )
                )
            rows = query.order_by(
                self.SkillSet.gmt_created.desc()
            ).all()
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
                user_id=_normalize_user_id(
                    skill_set_data.get("user_id")
                ),
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

    def update(
        self, skill_set_id: str, skill_set_data: dict
    ) -> Optional[dict]:
        existing = self.get_by_id(skill_set_id)
        if not existing:
            return None
        values = {
            self.SkillSet.name: skill_set_data.get(
                "name", existing["name"]
            ),
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
        with self._db.orm_session() as db:
            rowcount = (
                db.query(self.SkillSet)
                .filter(
                    self.SkillSet.id == int(skill_set_id),
                    self.SkillSet.env == get_current_env(),
                )
                .delete(synchronize_session=False)
            )
            return rowcount > 0

    def add_skill_to_set(
        self,
        skill_set_id: str,
        skill_id: str,
        user_id: Optional[str] = None,
    ) -> bool:
        with self._db.orm_session() as db:
            skill_set = (
                db.query(self.SkillSet)
                .filter(
                    self.SkillSet.id == int(skill_set_id),
                    self.SkillSet.env == get_current_env(),
                )
                .one_or_none()
            )
            skill = (
                db.query(self.Skill)
                .filter(
                    self.Skill.id == int(skill_id),
                    self.Skill.env == get_current_env(),
                )
                .one_or_none()
            )
            if skill_set is None or skill is None:
                return False
            db.add(
                self.SkillSetSkill(
                    skill_set_id=int(skill_set_id),
                    skill_id=int(skill_id),
                    user_id=_normalize_user_id(user_id),
                    env=get_current_env(),
                )
            )
            db.flush()
            return True

    def remove_skill_from_set(
        self, skill_set_id: str, skill_id: str
    ) -> bool:
        with self._db.orm_session() as db:
            rowcount = (
                db.query(self.SkillSetSkill)
                .filter(
                    self.SkillSetSkill.skill_set_id
                    == int(skill_set_id),
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
                    self.SkillSetSkill.skill_set_id
                    == int(skill_set_id),
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
                    Skill.skill_uuid
                    == self.SkillSetSkill.skill_uuid,
                )
                .join(
                    max_ver,
                    and_(
                        Skill.skill_uuid == max_ver.c.skill_uuid,
                        Skill.version == max_ver.c.mv,
                    ),
                )
                .filter(
                    self.SkillSetSkill.skill_set_id
                    == int(skill_set_id),
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
                        self.SkillSet.id
                        == self.SkillSetSkill.skill_set_id,
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
                    env=env,
                )
            )
            db.flush()
            return True

    def get_mcp_servers_in_set(
        self, skill_set_id: str
    ) -> list[dict]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self.SkillSetMCPServer)
                .filter(
                    self.SkillSetMCPServer.skill_set_id
                    == int(skill_set_id)
                )
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

    def remove_mcp_from_set(
        self, skill_set_id: str, server_code: str
    ) -> bool:
        with self._db.orm_session() as db:
            rowcount = (
                db.query(self.SkillSetMCPServer)
                .filter(
                    self.SkillSetMCPServer.skill_set_id
                    == int(skill_set_id),
                    self.SkillSetMCPServer.server_code
                    == server_code,
                )
                .delete(synchronize_session=False)
            )
            return rowcount > 0

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
        from agentclaw.community.core.skill_center.orm import DefaultSkillsetMcpExclusion

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
        from agentclaw.community.core.skill_center.orm import DefaultSkillsetMcpExclusion

        with self._db.orm_session() as db:
            rowcount = (
                db.query(DefaultSkillsetMcpExclusion)
                .filter(
                    DefaultSkillsetMcpExclusion.user_id == user_id,
                    DefaultSkillsetMcpExclusion.bot_id == bot_id,
                    DefaultSkillsetMcpExclusion.skill_set_id
                    == skill_set_id,
                    DefaultSkillsetMcpExclusion.server_code
                    == server_code,
                )
                .delete(synchronize_session=False)
            )
            return rowcount > 0

    def get_excluded_mcps(
        self, user_id: str, bot_id: str, skill_set_id: int
    ) -> list:
        from agentclaw.community.core.skill_center.orm import DefaultSkillsetMcpExclusion

        with self._db.orm_session() as db:
            rows = (
                db.query(DefaultSkillsetMcpExclusion.server_code)
                .filter(
                    DefaultSkillsetMcpExclusion.user_id == user_id,
                    DefaultSkillsetMcpExclusion.bot_id == bot_id,
                    DefaultSkillsetMcpExclusion.skill_set_id
                    == skill_set_id,
                )
                .all()
            )
            return [r[0] for r in rows]

    def get_all_excluded_mcps(
        self, user_id: str, bot_id: str
    ) -> list:
        from agentclaw.community.core.skill_center.orm import DefaultSkillsetMcpExclusion

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

    # ------------------------------------------------------------------
    # Default skill-set skill exclusion
    # (mirrors add/remove/get_excluded_mcps above, but for skills)
    # ------------------------------------------------------------------

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
        from agentclaw.community.core.skill_center.orm import DefaultSkillsetSkillExclusion

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
        from agentclaw.community.core.skill_center.orm import DefaultSkillsetSkillExclusion

        with self._db.orm_session() as db:
            rowcount = (
                db.query(DefaultSkillsetSkillExclusion)
                .filter(
                    DefaultSkillsetSkillExclusion.user_id == user_id,
                    DefaultSkillsetSkillExclusion.bot_id == bot_id,
                    DefaultSkillsetSkillExclusion.skill_set_id
                    == skill_set_id,
                    DefaultSkillsetSkillExclusion.skill_id
                    == skill_id,
                )
                .delete(synchronize_session=False)
            )
            return rowcount > 0

    def remove_all_default_skill_exclusions(
        self, user_id: str, bot_id: str, skill_id: int
    ) -> bool:
        """Clear stale exclusions left behind by a former default SkillSet."""
        from agentclaw.community.core.skill_center.orm import DefaultSkillsetSkillExclusion

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

    def get_excluded_skills(
        self, user_id: str, bot_id: str, skill_set_id: int
    ) -> list:
        """Return skill_id list excluded by (user, bot, skill_set)."""
        from agentclaw.community.core.skill_center.orm import DefaultSkillsetSkillExclusion

        with self._db.orm_session() as db:
            rows = (
                db.query(DefaultSkillsetSkillExclusion.skill_id)
                .filter(
                    DefaultSkillsetSkillExclusion.user_id == user_id,
                    DefaultSkillsetSkillExclusion.bot_id == bot_id,
                    DefaultSkillsetSkillExclusion.skill_set_id
                    == skill_set_id,
                )
                .all()
            )
            return [r[0] for r in rows]

    def get_all_excluded_skills(
        self, user_id: str, bot_id: str
    ) -> list:
        """Return skill_id list excluded by (user, bot) across all
        default skill sets."""
        from agentclaw.community.core.skill_center.orm import DefaultSkillsetSkillExclusion

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
                    self.SkillSetMCPServer.user_id
                    == _normalize_user_id(user_id),
                    or_(
                        self.SkillSetMCPServer.env
                        == get_current_env(),
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
                query = query.filter(
                    self.SkillSet.engine_type == engine_type
                )
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
                clear_q = clear_q.filter(
                    self.SkillSet.user_id == parsed
                )
            else:
                clear_q = clear_q.filter(
                    self.SkillSet.user_id.is_(None)
                )
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
                act_q = act_q.filter(
                    self.SkillSet.user_id.is_(None)
                )
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
                q = q.filter(
                    self.SkillSet.engine_type == engine_type
                )
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
                query = query.filter(
                    self.SkillSet.engine_type == engine_type
                )
            if user_id:
                query = query.filter(
                    self.SkillSet.user_id == parsed
                )
            else:
                query = query.filter(self.SkillSet.user_id.is_(None))
            for ss in query.all():
                active_sets.append(_skill_set_to_dict(ss))

        default_set = self.get_default(
            user_id=parsed if user_id is not None and bolt_id is not None else None,
            bolt_id=effective_bolt_id if user_id is not None and bolt_id is not None else None,
            engine_type=engine_type,
        )
        if default_set:
            enabled = self._get_user_default_enabled(
                user_id, effective_bolt_id, engine_type=engine_type
            )
            if enabled is None or enabled == 1:
                active_sets.append(default_set)
        global_default = self.get_default(
            user_id=None,
            bolt_id=None,
            engine_type=engine_type,
        )
        if global_default and all(
            skill_set["id"] != global_default["id"]
            for skill_set in active_sets
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

        bot_default = self.get_default(
            user_id=parsed if user_id is not None and bolt_id is not None else None,
            bolt_id=effective_bolt_id if user_id is not None and bolt_id is not None else None,
            engine_type=engine_type,
            env=env,
        )
        if bot_default:
            active_sets.append(bot_default)
        global_default = self.get_default(
            user_id=None,
            bolt_id=None,
            engine_type=engine_type,
            env=env,
        )
        if global_default and all(
            skill_set["id"] != global_default["id"]
            for skill_set in active_sets
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

    def delete_by_bot_id(self, bot_id: str) -> int:
        env = get_current_env()
        with self._db.orm_session() as db:
            ids = [
                r[0]
                for r in db.query(self.SkillSet.id)
                .filter(
                    self.SkillSet.bolt_id == bot_id,
                    self.SkillSet.env == env,
                )
                .all()
            ]
            count = len(ids)
            if count == 0:
                return 0
            db.query(self.SkillSetSkill).filter(
                self.SkillSetSkill.skill_set_id.in_(ids)
            ).delete(synchronize_session=False)
            # Prod tolerates a missing ac_skill_set_mcp table (added
            # after the original schema; legacy DBs may lack it) — it
            # wraps this DELETE in a plain try/except swallowing
            # "doesn't exist". Faithful reproduction = the SAME plain
            # try/except, NOT a SAVEPOINT: prod's ZdasDB.orm_session()
            # runs at isolation_level=AUTOCOMMIT, so a failed
            # statement is its own auto-committed unit and does not
            # taint the subsequent skill_set DELETE (exactly as prod's
            # raw cursor.execute did). A begin_nested() SAVEPOINT
            # would itself break under AUTOCOMMIT on MySQL/OceanBase
            # (no outer txn → the ROLLBACK TO SAVEPOINT errors with
            # "SAVEPOINT ... does not exist"). On SQLite (test/local)
            # the table is always present so this branch is inert.
            try:
                db.query(self.SkillSetMCPServer).filter(
                    self.SkillSetMCPServer.skill_set_id.in_(ids)
                ).delete(synchronize_session=False)
            except Exception as e:  # noqa: BLE001 - prod parity
                msg = str(e)
                if (
                    "doesn't exist" not in msg
                    and "no such table" not in msg
                ):
                    raise
            db.query(self.SkillSet).filter(
                self.SkillSet.bolt_id == bot_id,
                self.SkillSet.env == env,
            ).delete(synchronize_session=False)
            return count

    def set_skillset_active(
        self, env: str, skillset_id: str, is_active: int
    ) -> int:
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
