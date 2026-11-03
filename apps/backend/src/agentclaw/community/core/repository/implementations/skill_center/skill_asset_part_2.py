from __future__ import annotations
import json
from typing import Optional
from sqlalchemy import and_, func, or_
from agentclaw.community.log import get_logger
from agentclaw.community.core.skill_center.errors import SkillAssetInUseError
from agentclaw.community.utils.env_utils import get_current_env
from .skill_repository_common import _normalize_user_id, _skill_to_dict

logger = get_logger()


class SkillAssetPart2:
    def delete(self, skill_id: str) -> bool:
        """Delete one unreferenced Skill, or fail closed."""
        with self._db.transactional_orm_session() as db:
            skill = (
                db.query(self.Skill)
                .filter(
                    self.Skill.id == int(skill_id),
                    self.Skill.env == get_current_env(),
                )
                .with_for_update()
                .one_or_none()
            )
            if skill is None:
                return False
            self._require_unreferenced_for_delete(db, skill)
            db.delete(skill)
            db.flush()
            return True

    def require_unreferenced_for_delete(self, skill_id: str) -> None:
        """Preflight external cleanup; ``delete`` repeats this under its lock."""
        with self._db.transactional_orm_session() as db:
            skill = (
                db.query(self.Skill)
                .filter(
                    self.Skill.id == int(skill_id),
                    self.Skill.env == get_current_env(),
                )
                .with_for_update()
                .one_or_none()
            )
            if skill is not None:
                self._require_unreferenced_for_delete(db, skill)

    @staticmethod
    def _require_unreferenced_for_delete(
        db, skill, *, env: str | None = None, ignore_membership: bool = False
    ) -> None:
        """Recheck database blockers while the exact Skill row is locked."""
        from agentclaw.community.core.models import (
            BotSkillInstallation,
            SkillDraftEditLease,
            SkillGrant,
            SkillPublicationAttempt,
            SkillSetSkill,
            SkillSpaceBinding,
            SkillVersion,
        )
        from agentclaw.community.core.models.space_skill import (
            SkillDraftUpgradeRequest,
        )
        from agentclaw.community.core.models.skill import AcSkillMember

        effective_env = env or get_current_env()
        installation_count = (
            db.query(BotSkillInstallation.id)
            .filter(
                BotSkillInstallation.env == effective_env,
                BotSkillInstallation.skill_id == int(skill.id),
            )
            .count()
        )
        membership_identity = SkillSetSkill.skill_id == int(skill.id)
        if skill.skill_uuid:
            membership_identity = or_(
                membership_identity,
                SkillSetSkill.skill_uuid == skill.skill_uuid,
            )
        membership_count = (
            0
            if ignore_membership
            else (
                db.query(SkillSetSkill.id)
                .filter(
                    SkillSetSkill.env == effective_env,
                    membership_identity,
                )
                .count()
            )
        )
        # ac_skill_member predates tenant/env columns. It is still served by
        # the legacy member APIs, so a matching UUID remains a global,
        # fail-closed grant blocker until that table is retired.
        grant_count = 0
        if skill.skill_uuid:
            grant_count = (
                db.query(AcSkillMember.id)
                .filter(AcSkillMember.skill_uuid == skill.skill_uuid)
                .count()
            )
        space_binding_count = (
            db.query(SkillSpaceBinding.id)
            .filter(
                SkillSpaceBinding.env == effective_env,
                SkillSpaceBinding.skill_id == int(skill.id),
            )
            .count()
        )
        grant_count += (
            db.query(SkillGrant.id)
            .filter(
                SkillGrant.env == effective_env,
                SkillGrant.skill_id == int(skill.id),
            )
            .count()
        )
        version_count = (
            db.query(SkillVersion.id)
            .filter(
                SkillVersion.env == effective_env,
                SkillVersion.skill_id == int(skill.id),
            )
            .count()
        )
        # Replayable Service Artifacts carry exact Center refs only. Every
        # such ref is produced from an immutable SkillVersion, so Version is
        # the dominant database blocker for hard deletion. Offline impact
        # still scans Artifact lineage to show the authorized product detail;
        # the repository intentionally does not depend on service_bot I/O.
        publication_count = (
            db.query(SkillPublicationAttempt.id)
            .filter(
                SkillPublicationAttempt.env == effective_env,
                SkillPublicationAttempt.skill_id == int(skill.id),
            )
            .count()
        )
        draft_count = int(skill.draft_status is not None)
        draft_count += (
            db.query(SkillDraftEditLease.id)
            .filter(
                SkillDraftEditLease.env == effective_env,
                SkillDraftEditLease.skill_id == int(skill.id),
            )
            .count()
        )
        draft_count += (
            db.query(SkillDraftUpgradeRequest.id)
            .filter(
                SkillDraftUpgradeRequest.env == effective_env,
                SkillDraftUpgradeRequest.skill_id == int(skill.id),
            )
            .count()
        )
        blockers = {
            "installation": installation_count,
            "membership": membership_count,
            "grant": grant_count,
            "space_binding": space_binding_count,
            "draft": draft_count,
            "publication": publication_count,
            "version": version_count,
        }
        if any(blockers.values()):
            raise SkillAssetInUseError(blockers)

    def replace_bot_local_skill(
        self,
        *,
        skill_id: str,
        owner_id: str,
        bot_id: str,
        old_locator: str,
        new_locator: str,
        description: str,
    ) -> dict | None:
        """Update one Local Skill while preserving its canonical locator."""

        if old_locator != new_locator:
            raise ValueError("Local Skill replacement cannot change git_path")

        with self._db.transactional_orm_session() as db:
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
            skill.user_id = _normalize_user_id(owner_id)
            skill.gmt_modified = func.now()
            db.flush()
            result = _skill_to_dict(skill)
            return result

    def delete_bot_local_skill(
        self,
        *,
        skill_id: str,
        owner_id: str,
        bot_id: str,
    ) -> bool | None:
        """Atomically delete one inactive Local Skill and its scoped state."""
        from agentclaw.community.core.skill_center.orm import (
            DefaultSkillsetSkillExclusion,
        )

        with self._db.transactional_orm_session() as db:
            skill = (
                db.query(self.Skill)
                .filter(
                    self.Skill.id == int(skill_id),
                    self.Skill.env == get_current_env(),
                    self.Skill.user_id == _normalize_user_id(owner_id),
                    self.Skill.bolt_id == bot_id,
                    self.Skill.git_path.like("local://%"),
                )
                .with_for_update()
                .one_or_none()
            )
            if skill is None:
                return None
            self._require_unreferenced_for_delete(db, skill)
            db.query(DefaultSkillsetSkillExclusion).filter(
                DefaultSkillsetSkillExclusion.user_id == _normalize_user_id(owner_id),
                DefaultSkillsetSkillExclusion.bot_id == bot_id,
                DefaultSkillsetSkillExclusion.skill_id == int(skill_id),
            ).delete(synchronize_session=False)
            db.delete(skill)
            db.flush()
            return True

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

    def delete_by_name_with_cascade(self, name: str, env: Optional[str] = None) -> dict:
        effective_env = env or get_current_env()
        with self._db.transactional_orm_session() as db:
            skills = (
                db.query(self.Skill)
                .filter(
                    self.Skill.name == name,
                    self.Skill.env == effective_env,
                )
                .order_by(self.Skill.id)
                .with_for_update()
                .all()
            )
            for skill in skills:
                self._require_unreferenced_for_delete(db, skill, env=effective_env)
            for skill in skills:
                db.delete(skill)
            db.flush()

        return {
            "deleted_skill_count": len(skills),
            "cleaned_set_skill": 0,
            "cleaned_member": 0,
        }

    def update_risk_tags(self, skill_id: str, risk_tags: list) -> Optional[dict]:
        with self._db.orm_session() as db:
            db.query(self.Skill).filter(
                self.Skill.id == int(skill_id),
                self.Skill.env == get_current_env(),
            ).update(
                {
                    self.Skill.risk_tags: json.dumps(risk_tags) if risk_tags else None,
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
                    self.Skill.mcp_dependencies: json.dumps(mcp_dependencies)
                    if mcp_dependencies
                    else None,
                    self.Skill.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )
        return self.get_by_id(skill_id)

    def _bot_skills(self, db, bot_id: str, owner_id: str, env: str):
        return (
            db.query(self.Skill)
            .filter(
                self.Skill.bolt_id == bot_id,
                self.Skill.user_id == _normalize_user_id(owner_id),
                self.Skill.env == env,
            )
            .order_by(self.Skill.id)
            .with_for_update()
            .all()
        )

    def _delete_one_bot_skill(
        self, db, skill, bot_id: str, owner_id: str, env: str
    ) -> None:
        local = str(skill.git_path or "").startswith("local://")
        if local:
            # Unknown or foreign Set ownership must remain untouched.  This
            # includes historic orphan associations, which are out of scope.
            blockers = (
                db.query(self.SkillSetSkill.id)
                .outerjoin(
                    self.SkillSet, self.SkillSet.id == self.SkillSetSkill.skill_set_id
                )
                .filter(
                    self.SkillSetSkill.skill_id == skill.id,
                    self.SkillSetSkill.env == env,
                    or_(
                        self.SkillSet.id.is_(None),
                        self.SkillSet.bolt_id != bot_id,
                        self.SkillSet.bolt_id.is_(None),
                        self.SkillSet.user_id != _normalize_user_id(owner_id),
                        self.SkillSet.user_id.is_(None),
                        self.SkillSet.env != env,
                        self.SkillSet.env.is_(None),
                    ),
                )
                .count()
            )
            if blockers:
                raise SkillAssetInUseError({"membership": blockers})
            owned_sets = db.query(self.SkillSet.id).filter(
                self.SkillSet.bolt_id == bot_id,
                self.SkillSet.user_id == _normalize_user_id(owner_id),
                self.SkillSet.env == env,
            )
            db.query(self.SkillSetSkill).filter(
                self.SkillSetSkill.skill_id == skill.id,
                self.SkillSetSkill.env == env,
                self.SkillSetSkill.skill_set_id.in_(owned_sets),
            ).delete(synchronize_session=False)
        self._require_unreferenced_for_delete(
            db, skill, env=env, ignore_membership=local
        )
        db.query(self.Skill).filter(
            self.Skill.id == skill.id,
            self.Skill.bolt_id == bot_id,
            self.Skill.user_id == _normalize_user_id(owner_id),
            self.Skill.env == env,
        ).delete(synchronize_session=False)
        db.flush()

    def delete_by_bot_id(self, bot_id: str, owner_id: str) -> int:
        env = get_current_env()
        with self._db.transactional_orm_session() as db:
            skills = self._bot_skills(db, bot_id, owner_id, env)
            for skill in skills:
                self._delete_one_bot_skill(db, skill, bot_id, owner_id, env)
            return len(skills)

    def cleanup_by_bot_id(self, bot_id: str, owner_id: str) -> dict:
        """Delete each unreferenced asset; retain and report independently blocked ones."""
        env = get_current_env()
        deleted = 0
        retained = 0
        blocker_counts: dict[str, int] = {}
        with self._db.transactional_orm_session() as db:
            skills = self._bot_skills(db, bot_id, owner_id, env)
            for skill in skills:
                try:
                    with db.begin_nested():
                        self._delete_one_bot_skill(db, skill, bot_id, owner_id, env)
                    deleted += 1
                except SkillAssetInUseError as exc:
                    retained += 1
                    for kind, count in exc.blocker_counts.items():
                        blocker_counts[kind] = blocker_counts.get(kind, 0) + count
        return {
            "deleted": deleted,
            "retained": retained,
            "blocker_counts": blocker_counts,
        }

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

    def list_git_skills_with_order(self, orderby: Optional[str] = None) -> list[dict]:
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
