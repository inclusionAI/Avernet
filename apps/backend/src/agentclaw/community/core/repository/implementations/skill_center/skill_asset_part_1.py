from __future__ import annotations
import json
from collections.abc import Iterable
from typing import List, Optional
from sqlalchemy import and_, func, or_
from agentclaw.community.log import get_logger
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env
from .skill_repository_common import _normalize_user_id, _skill_to_dict

logger = get_logger()


class SkillAssetPart1:
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

    def get_by_uuid(self, skill_uuid: str, env: str | None = None) -> Optional[dict]:
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

    def list_center_by_name(self, name: str) -> List[dict]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self.Skill)
                .filter(
                    self.Skill.name == name,
                    self.Skill.git_path.like("center://%"),
                    self.Skill.offline_at.is_(None),
                    self.Skill.env == get_current_env(),
                )
                .order_by(self.Skill.id)
                .all()
            )
            return [_skill_to_dict(row) for row in rows]

    def get_by_link_name(
        self, link_name: str, bolt_id: Optional[str] = None
    ) -> Optional[dict]:
        # prod: WHERE REPLACE(SUBSTRING(git_path,7),'/','_') = %s AND env=%s
        with self._db.orm_session() as db:
            git_expr = func.replace(func.substr(self.Skill.git_path, 7), "/", "_")
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
                query = query.filter(self.Skill.user_id == _normalize_user_id(user_id))
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
                query = query.filter(self.Skill.user_id == _normalize_user_id(user_id))
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
                query = query.filter(self.Skill.user_id == _normalize_user_id(user_id))
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
                query = query.filter(self.Skill.user_id == _normalize_user_id(user_id))
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
            row = (
                db.query(self.Skill)
                .filter(
                    self.Skill.env == get_current_env(),
                    self.Skill.bolt_id == bot_id,
                    self.Skill.user_id == _normalize_user_id(user_id),
                    self.Skill.git_path == f"local://{locator}",
                )
                .one_or_none()
            )
            return _skill_to_dict(row) if row is not None else None

    @staticmethod
    def _public_bot_skill(row, active: bool) -> dict:
        data = _skill_to_dict(row)
        data["active"] = bool(active)
        return data

    def list_bot_skills(
        self,
        *,
        bot_id: str,
        user_id: str,
        skill_set_member_ids: Iterable[int],
        page: int,
        page_size: int,
        active: bool | None,
        keyword: str | None,
        source: str | None = None,
    ) -> tuple[int, list[dict]]:
        """Page every Skill one Bot reaches, by desired active state.

        A Bot reaches a Skill three ways, and this answers for all of them: it
        owns the row outright (``bolt_id``/``user_id``, which is every
        ``local://`` asset it ever uploaded); one of its SkillSets bridges a
        shared ``git://``/``center://`` asset to it; or it activated such an
        asset directly, which writes an Installation row and nothing else.
        ``skill_set_member_ids`` carries the second already resolved — the
        SkillSet control plane owns which Sets a Bot has and which Default
        members its owner excluded, and this method deliberately does not
        re-derive either.

        ``active`` is read from the Installation fact alone, so the answer
        holds while the Bot is offline and cannot drift from what an
        activate/deactivate command wrote.
        """
        from agentclaw.community.core.models.skill import BotSkillInstallation

        member_ids = {int(value) for value in skill_set_member_ids}
        with self._db.orm_session() as db:
            installed = (
                db.query(BotSkillInstallation.id)
                .filter(
                    BotSkillInstallation.avernet_tenant == get_current_avernet_tenant(),
                    BotSkillInstallation.env == get_current_env(),
                    BotSkillInstallation.owner_id == _normalize_user_id(user_id),
                    BotSkillInstallation.bot_id == bot_id,
                    BotSkillInstallation.skill_id == self.Skill.id,
                )
                .exists()
            )
            owned = and_(
                self.Skill.bolt_id == bot_id,
                self.Skill.user_id == _normalize_user_id(user_id),
            )
            query = db.query(self.Skill, installed.label("active")).filter(
                self.Skill.env == get_current_env()
            )
            if source == "LOCAL":
                # A Local upload is an exact owner+Bot row. Do not let a
                # shared Direct/SkillSet asset enter this legacy-compatible
                # collection merely because it is currently installed.
                query = query.filter(owned, self.Skill.git_path.like("local://%"))
            else:
                # Three ways, not two. A shared Skill the Bot activated directly
                # belongs to no SkillSet and carries another owner's ``bolt_id``,
                # so neither of the first two predicates finds it — yet
                # ``list_bot_installed_assets`` puts it in the runtime projection,
                # so leaving it out would hide a Skill the Bot is running.
                reachable = [owned, installed]
                if member_ids:
                    reachable.append(self.Skill.id.in_(member_ids))
                query = query.filter(or_(*reachable))
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
                query = query.filter(installed == active)
            total = query.count()
            rows = (
                query.order_by(self.Skill.gmt_modified.desc(), self.Skill.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            return total, [
                self._public_bot_skill(row, is_active) for row, is_active in rows
            ]

    def get_bot_local_skill(
        self, *, skill_id: str, bot_id: str, user_id: str
    ) -> dict | None:
        """Return one exact Bot-owned ``local://`` row or no row."""
        from agentclaw.community.core.models.skill import BotSkillInstallation

        with self._db.orm_session() as db:
            installed = (
                db.query(BotSkillInstallation.id)
                .filter(
                    BotSkillInstallation.avernet_tenant == get_current_avernet_tenant(),
                    BotSkillInstallation.env == get_current_env(),
                    BotSkillInstallation.owner_id == _normalize_user_id(user_id),
                    BotSkillInstallation.bot_id == bot_id,
                    BotSkillInstallation.skill_id == self.Skill.id,
                )
                .exists()
            )
            row = (
                db.query(self.Skill, installed.label("active"))
                .filter(
                    self.Skill.id == int(skill_id),
                    self.Skill.env == get_current_env(),
                    self.Skill.bolt_id == bot_id,
                    self.Skill.user_id == _normalize_user_id(user_id),
                    self.Skill.git_path.like("local://%"),
                )
                .one_or_none()
            )
            return self._public_bot_skill(*row) if row else None

    def list_bot_installed_skills(
        self, *, env: str, owner_id: str, bot_id: str
    ) -> list[dict]:
        """Return assets selected by the active-only Installation fact."""
        from agentclaw.community.core.models.skill import BotSkillInstallation

        with self._db.orm_session() as db:
            rows = (
                db.query(self.Skill)
                .join(
                    BotSkillInstallation,
                    BotSkillInstallation.skill_id == self.Skill.id,
                )
                .filter(
                    BotSkillInstallation.avernet_tenant == get_current_avernet_tenant(),
                    BotSkillInstallation.env == env,
                    BotSkillInstallation.owner_id == _normalize_user_id(owner_id),
                    BotSkillInstallation.bot_id == bot_id,
                    self.Skill.env == env,
                )
                .order_by(self.Skill.id)
                .all()
            )
            return [_skill_to_dict(row) for row in rows]

    def list_bot_local_assets(
        self,
        *,
        env: str,
        owner_id: str,
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
                    self.Skill.user_id == _normalize_user_id(owner_id),
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
                    skill_uuid=getattr(row, "skill_uuid", None),
                    sc_version_number=getattr(row, "sc_version_number", None),
                    mcp_dependencies=tuple(
                        _skill_to_dict(row).get("mcp_dependencies") or ()
                    ),
                )
                for row in rows
            ]

    def list_bot_installed_assets(
        self,
        *,
        env: str,
        bot_id: str,
        owner_id: str,
    ):
        """The Installation→``ac_skill`` join, shaped for the runtime boundary.

        The reader's only backing read: a Skill is active because an
        Installation row says so — no Set walk, no exclusion read, no engine
        filter. The flush has already folded Set configuration into those
        rows.
        """

        from agentclaw.community.core.skills_pool.models import (
            RegisteredSkillAsset,
        )

        seen: set[int] = set()
        assets: list[RegisteredSkillAsset] = []
        for row in self.list_bot_installed_skills(
            env=env, owner_id=owner_id, bot_id=bot_id
        ):
            skill_id = int(row["id"])
            git_path = str(row.get("git_path") or "")
            if not git_path or skill_id in seen:
                continue
            seen.add(skill_id)
            assets.append(
                RegisteredSkillAsset(
                    skill_id=skill_id,
                    name=str(row["name"]),
                    git_path=git_path,
                    skill_uuid=(
                        str(row["skill_uuid"]) if row.get("skill_uuid") else None
                    ),
                    sc_version_number=(
                        str(row["sc_version_number"])
                        if row.get("sc_version_number") is not None
                        else None
                    ),
                    mcp_dependencies=tuple(row.get("mcp_dependencies") or ()),
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
                tags=json.dumps(tags) if isinstance(tags, (list, dict)) else tags,
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

    def update(self, skill_id: str, skill_data: dict) -> Optional[dict]:
        existing = self.get_by_id(skill_id)
        if not existing:
            return None

        def _j(v):
            return json.dumps(v) if isinstance(v, (list, dict)) else v

        tags = skill_data.get("tags", existing.get("tags"))
        input_schema = skill_data.get("input_schema", existing.get("input_schema"))
        output_schema = skill_data.get("output_schema", existing.get("output_schema"))
        mcp_dependencies = skill_data.get(
            "mcp_dependencies", existing.get("mcp_dependencies")
        )
        risk_tags = skill_data.get("risk_tags", existing.get("risk_tags"))
        values = {
            self.Skill.name: skill_data.get("name", existing["name"]),
            self.Skill.description: skill_data.get(
                "description", existing.get("description")
            ),
            self.Skill.git_path: skill_data.get("git_path", existing.get("git_path")),
            self.Skill.category: skill_data.get("category", existing.get("category")),
            self.Skill.tags: _j(tags),
            self.Skill.input_schema: _j(input_schema),
            self.Skill.output_schema: _j(output_schema),
            self.Skill.is_public: skill_data.get(
                "is_public", existing.get("is_public", True)
            ),
            self.Skill.user_id: _normalize_user_id(
                skill_data.get("user_id", existing.get("user_id"))
            ),
            self.Skill.status: skill_data.get("status", existing.get("status")),
            self.Skill.version: skill_data.get("version", existing.get("version")),
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
            self.Skill.zip_url: skill_data.get("zip_url", existing.get("zip_url")),
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
