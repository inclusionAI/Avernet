"""Owner-scoped persistence for Team Space Skill editor approval policy."""

from injector import inject

from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.models.space_skill import SkillGrant, SkillSpaceBinding
from agentclaw.community.core.repository.protocols.skill_center_types import (
    SkillEditorApprovalPolicyRecord,
)
from agentclaw.community.core.skill_center.editor_approval_policy_protocol import (
    SkillEditorApprovalPolicyRepositoryProtocol,
)
from agentclaw.community.core.skill_center.errors import (
    SpaceSkillGrantForbiddenError,
    SpaceSkillGrantNotFoundError,
)
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.plugin_api.database import DatabasePlugin


class SkillEditorApprovalPolicyRepository(
    SkillEditorApprovalPolicyRepositoryProtocol
):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def get_policy(
        self, *, space_id: int, skill_id: int, actor_id: str, env: str
    ) -> SkillEditorApprovalPolicyRecord:
        with self._db.transactional_orm_session() as session:
            binding = self._lock_policy(session, space_id, skill_id, actor_id, env)
            return self._record(binding)

    def update_policy(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        auto_approve_editor_requests: bool,
        env: str,
    ) -> SkillEditorApprovalPolicyRecord:
        with self._db.transactional_orm_session() as session:
            binding = self._lock_policy(session, space_id, skill_id, actor_id, env)
            binding.auto_approve_editor_requests = auto_approve_editor_requests
            session.flush()
            return self._record(binding)

    @staticmethod
    def _lock_policy(session, space_id, skill_id, actor_id, env):
        row = (
            session.query(SkillSpaceBinding, SpaceModel, Skill)
            .join(
                SpaceModel,
                (SpaceModel.id == SkillSpaceBinding.space_id)
                & (SpaceModel.env == SkillSpaceBinding.env),
            )
            .join(
                Skill,
                (Skill.id == SkillSpaceBinding.skill_id)
                & (Skill.env == SkillSpaceBinding.env),
            )
            .filter(
                SkillSpaceBinding.space_id == space_id,
                SkillSpaceBinding.skill_id == skill_id,
                SkillSpaceBinding.env == env,
                SpaceModel.deleted_at.is_(None),
            )
            .with_for_update()
            .one_or_none()
        )
        if row is None:
            raise SpaceSkillGrantNotFoundError("space skill not found")
        binding, space, _skill = row
        if space.space_type != "TEAM":
            raise SpaceSkillGrantForbiddenError(
                "editor approval policy is available only for Team Space Skills"
            )
        member = session.query(SpaceMemberModel.id).filter(
            SpaceMemberModel.space_id == space_id,
            SpaceMemberModel.user_id == actor_id,
            SpaceMemberModel.status == "ACTIVE",
            SpaceMemberModel.env == env,
        ).first()
        owner = session.query(SkillGrant.id).filter(
            SkillGrant.skill_id == skill_id,
            SkillGrant.user_id == actor_id,
            SkillGrant.role == "OWNER",
            SkillGrant.status == "ACTIVE",
            SkillGrant.owner_slot == 1,
            SkillGrant.env == env,
        ).first()
        if member is None or owner is None:
            raise SpaceSkillGrantForbiddenError("current Skill Owner role required")
        return binding

    @staticmethod
    def _record(binding: SkillSpaceBinding) -> SkillEditorApprovalPolicyRecord:
        return {
            "auto_approve_editor_requests": binding.auto_approve_editor_requests
        }


__all__ = ["SkillEditorApprovalPolicyRepository"]
