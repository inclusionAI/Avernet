"""Legacy skills member routes."""

from agentclaw.community.adapters.http.dependencies import (
    RequestContext,
    get_request_context,
)
from agentclaw.community.adapters.http.skill_center.schemas import (
    AddSkillMemberRequest,
    BatchAddMembersResponse,
    BatchAddMembersResult,
    BatchAddSkillMembersRequest,
    MessageResponse,
    SkillMemberListResponse,
    SkillMemberOperationResponse,
    SkillMemberResponse,
    UpdateSkillMemberRoleRequest,
)
from agentclaw.community.api.skill_member_service import SkillMemberServiceProtocol
from agentclaw.community.di import Injected
from fastapi import (
    Depends,
    HTTPException,
)

from .skills_common import (
    logger,
)


from fastapi import APIRouter
router = APIRouter(prefix="/api/skills", tags=["skills"])

@router.get("/{skill_uuid}/members", response_model=SkillMemberListResponse)
async def get_skill_members(
    skill_uuid: str,
    ctx: RequestContext = Depends(get_request_context),
    member_service: SkillMemberServiceProtocol = Injected(SkillMemberServiceProtocol),
) -> SkillMemberListResponse:
    """获取技能的所有成员列表

    Args:
        skill_uuid: 技能 UUID
    """
    try:
        members = member_service.get_members_by_skill_uuid(skill_uuid)

        return SkillMemberListResponse(
            success=True,
            data=[SkillMemberResponse(**m) for m in members],
            count=len(members),
        )
    except Exception as e:
        logger.error(f"[get_skill_members] Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{skill_uuid}/members", response_model=SkillMemberOperationResponse)
async def add_skill_member(
    skill_uuid: str,
    request: AddSkillMemberRequest,
    ctx: RequestContext = Depends(get_request_context),
    member_service: SkillMemberServiceProtocol = Injected(SkillMemberServiceProtocol),
) -> SkillMemberOperationResponse:
    """添加技能成员

    Args:
        skill_uuid: 技能 UUID
        request: 添加成员请求，包含 user_id 和 role
    """
    try:
        member = member_service.add_member(
            skill_uuid=skill_uuid, user_id=request.user_id, role=request.role
        )

        return SkillMemberOperationResponse(
            success=True,
            data=SkillMemberResponse(**member),
            message="Member added successfully",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[add_skill_member] Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{skill_uuid}/members/batch", response_model=BatchAddMembersResponse)
async def add_skill_members_batch(
    skill_uuid: str,
    request: BatchAddSkillMembersRequest,
    ctx: RequestContext = Depends(get_request_context),
    member_service: SkillMemberServiceProtocol = Injected(SkillMemberServiceProtocol),
) -> BatchAddMembersResponse:
    """批量添加技能成员

    Args:
        skill_uuid: 技能 UUID
        request: 批量添加成员请求，包含 members 列表
    """
    try:
        results = member_service.add_members_batch(
            skill_uuid=skill_uuid, members=request.members
        )

        return BatchAddMembersResponse(
            success=True,
            data=BatchAddMembersResult(
                success=results["success"], failed=results["failed"]
            ),
            message=f"Added {len(results['success'])} members, {len(results['failed'])} failed",
        )
    except Exception as e:
        logger.error(f"[add_skill_members_batch] Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{skill_uuid}/members/{user_id}", response_model=MessageResponse)
async def remove_skill_member(
    skill_uuid: str,
    user_id: str,
    ctx: RequestContext = Depends(get_request_context),
    member_service: SkillMemberServiceProtocol = Injected(SkillMemberServiceProtocol),
) -> MessageResponse:
    """移除技能成员

    Args:
        skill_uuid: 技能 UUID
        user_id: 用户 ID
    """
    try:
        member_service.remove_member(skill_uuid=skill_uuid, user_id=user_id)

        return MessageResponse(success=True, message="Member removed successfully")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"[remove_skill_member] Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.put(
    "/{skill_uuid}/members/{user_id}/role", response_model=SkillMemberOperationResponse
)
async def update_skill_member_role(
    skill_uuid: str,
    user_id: str,
    request: UpdateSkillMemberRoleRequest,
    ctx: RequestContext = Depends(get_request_context),
    member_service: SkillMemberServiceProtocol = Injected(SkillMemberServiceProtocol),
) -> SkillMemberOperationResponse:
    """更新成员角色

    Args:
        skill_uuid: 技能 UUID
        user_id: 用户 ID
        request: 更新角色请求，包含新角色
    """
    try:
        member = member_service.update_member_role(
            skill_uuid=skill_uuid, user_id=user_id, role=request.role
        )

        return SkillMemberOperationResponse(
            success=True,
            data=SkillMemberResponse(**member),
            message="Member role updated successfully",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[update_skill_member_role] Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
