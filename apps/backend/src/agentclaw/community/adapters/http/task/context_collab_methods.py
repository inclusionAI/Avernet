"""L5 method 操作的内部 /api/v1 delivery(2026-11-12 spec §7)。

薄 delivery,与 ``context_collab.py`` 同前缀分组(1000 行边界拆出的
第二文件):装订(认证 → ActorContext)、``to_domain()`` 命令组装、
Envelope 序列化、领域错误交 ``@envelope_errors`` 映射
(``ContextCollabValidationError`` → 422、``ContextCollabVersionConflict``
→ 409、``ContextCollabNotFoundError`` → 404 的既有约定)。路由族:
lifecycle(bind / activate / suspend / resume / terminate / update-config
/ inspect / compose / detach)、Single、Group、Swarm、Open(spec §7);
服务一律经 ``Injected(协议)`` 解析,不 import 实现类。

命名裁决(§7 "路由最终命名对齐既有风格微调"):沿用既有单数
``/context_collab/{id}``` 前缀;group 族拆 ``invite``(INVITED 行创建)
与 ``members``(add_member 迁移)两个动作位,members 的幂等键在
invite 位;swarm 补 ``plans/{plan_id}/validate`` 只读报告位(协议
§4.3 有操作无既有路由位)。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.adapters.http.task.schemas_context_collab_methods import (
    BindMethodRequest,
    ComposeMethodRequest,
    CompositionEdgeDto,
    GroupChangeRoleRequest,
    GroupMemberRequest,
    GroupReplaceMemberRequest,
    MethodBindingDto,
    MethodInspectionDto,
    MethodOpAcceptedDto,
    MethodStateChangeRequest,
    OpenAdmitRequest,
    OpenApplicationRequest,
    OpenDecisionOpRequest,
    OpenOpportunityOpRequest,
    OpenOpportunityRequest,
    ProfileOpAcceptedDto,
    SingleDelegateRequest,
    SingleReplaceRequest,
    SingleSelectRequest,
    SwarmAllocateRequest,
    SwarmExecutionProfileRequest,
    SwarmPlanCandidateRequest,
    SwarmPlanOpRequest,
    SwarmPlanValidationReportDto,
    SwarmStageOutcomeRequest,
    UpdateBindingConfigRequest,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.method_service_api import (
    GroupMethodOpsServiceProtocol,
    MethodLifecycleServiceProtocol,
    OpenMethodOpsServiceProtocol,
    SwarmMethodOpsServiceProtocol,
)
from agentclaw.community.core.task.context_collab.service_api import ActorContext
from agentclaw.community.di import Injected

router = APIRouter(prefix="/api/v1/collaboration/task", tags=["task-context-collab"])


def _actor_context(user: AuthenticatedUser) -> ActorContext:
    """认证主体 → 服务调用上下文;G1 的 actor 一律取 ``user.staffId``。"""
    return ActorContext(actor=PrincipalRef("human", user.staffId), source="api")


def _accepted(result) -> MethodOpAcceptedDto:
    """lifecycle 受理结果的 DTO 投影。"""
    return MethodOpAcceptedDto(
        accepted=result.accepted,
        collab_context_id=result.collab_context_id,
        method_binding_id=result.method_binding_id,
        state=result.state,
        version=result.version,
    )


def _profile_accepted(result) -> ProfileOpAcceptedDto:
    """Profile 对象面受理结果的 DTO 投影。"""
    return ProfileOpAcceptedDto(
        accepted=result.accepted,
        collab_context_id=result.collab_context_id,
        method_binding_id=result.method_binding_id,
        object_kind=result.object_kind,
        object_id=result.object_id,
        state=result.state,
    )


# ---------------------------------------------------------------------------
# 生命周期与组合(spec §3.2 / §3.3)
# ---------------------------------------------------------------------------

_BINDINGS = "/context_collab/{collab_context_id}/method-bindings"


@router.post(_BINDINGS, response_model=Envelope[MethodBindingDto])
@envelope_errors
async def bind_method(
    collab_context_id: str,
    body: BindMethodRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: MethodLifecycleServiceProtocol = Injected(MethodLifecycleServiceProtocol),  # noqa: B008
) -> Envelope[MethodBindingDto]:
    result = service.bind(
        collab_context_id, body.to_domain(), _actor_context(user)
    )
    return envelope(MethodBindingDto(binding=result.binding), request)


@router.post(
    _BINDINGS + "/{binding_id}/activate",
    response_model=Envelope[MethodOpAcceptedDto],
)
@envelope_errors
async def activate_method_binding(
    collab_context_id: str,
    binding_id: str,
    body: MethodStateChangeRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: MethodLifecycleServiceProtocol = Injected(MethodLifecycleServiceProtocol),  # noqa: B008
) -> Envelope[MethodOpAcceptedDto]:
    result = service.activate(
        collab_context_id, binding_id, body.to_domain(), _actor_context(user)
    )
    return envelope(_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/suspend",
    response_model=Envelope[MethodOpAcceptedDto],
)
@envelope_errors
async def suspend_method_binding(
    collab_context_id: str,
    binding_id: str,
    body: MethodStateChangeRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: MethodLifecycleServiceProtocol = Injected(MethodLifecycleServiceProtocol),  # noqa: B008
) -> Envelope[MethodOpAcceptedDto]:
    result = service.suspend(
        collab_context_id, binding_id, body.to_domain(), _actor_context(user)
    )
    return envelope(_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/resume",
    response_model=Envelope[MethodOpAcceptedDto],
)
@envelope_errors
async def resume_method_binding(
    collab_context_id: str,
    binding_id: str,
    body: MethodStateChangeRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: MethodLifecycleServiceProtocol = Injected(MethodLifecycleServiceProtocol),  # noqa: B008
) -> Envelope[MethodOpAcceptedDto]:
    result = service.resume(
        collab_context_id, binding_id, body.to_domain(), _actor_context(user)
    )
    return envelope(_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/terminate",
    response_model=Envelope[MethodOpAcceptedDto],
)
@envelope_errors
async def terminate_method_binding(
    collab_context_id: str,
    binding_id: str,
    body: MethodStateChangeRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: MethodLifecycleServiceProtocol = Injected(MethodLifecycleServiceProtocol),  # noqa: B008
) -> Envelope[MethodOpAcceptedDto]:
    result = service.terminate(
        collab_context_id, binding_id, body.to_domain(), _actor_context(user)
    )
    return envelope(_accepted(result), request)


@router.patch(
    _BINDINGS + "/{binding_id}",
    response_model=Envelope[MethodOpAcceptedDto],
)
@envelope_errors
async def update_method_binding_config(
    collab_context_id: str,
    binding_id: str,
    body: UpdateBindingConfigRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: MethodLifecycleServiceProtocol = Injected(MethodLifecycleServiceProtocol),  # noqa: B008
) -> Envelope[MethodOpAcceptedDto]:
    result = service.update_config(
        collab_context_id, binding_id, body.to_domain(), _actor_context(user)
    )
    return envelope(_accepted(result), request)


@router.get(
    _BINDINGS + "/{binding_id}",
    response_model=Envelope[MethodInspectionDto],
)
@envelope_errors
async def inspect_method_binding(
    collab_context_id: str,
    binding_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: MethodLifecycleServiceProtocol = Injected(MethodLifecycleServiceProtocol),  # noqa: B008
) -> Envelope[MethodInspectionDto]:
    result = service.inspect(collab_context_id, binding_id, _actor_context(user))
    return envelope(
        MethodInspectionDto(
            binding=result.binding,
            state_summary=result.state_summary,
            capabilities=result.capabilities,
            conformance_evidence=result.conformance_evidence,
        ),
        request,
    )


@router.post(
    _BINDINGS + "/{binding_id}/compose",
    response_model=Envelope[CompositionEdgeDto],
)
@envelope_errors
async def compose_method(
    collab_context_id: str,
    binding_id: str,
    body: ComposeMethodRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: MethodLifecycleServiceProtocol = Injected(MethodLifecycleServiceProtocol),  # noqa: B008
) -> Envelope[CompositionEdgeDto]:
    result = service.compose(
        collab_context_id, binding_id, body.to_domain(), _actor_context(user)
    )
    return envelope(CompositionEdgeDto(edge=result.edge), request)


@router.delete(
    _BINDINGS + "/{binding_id}/compose/{edge_id}",
    response_model=Envelope[CompositionEdgeDto],
)
@envelope_errors
async def detach_method(
    collab_context_id: str,
    binding_id: str,
    edge_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: MethodLifecycleServiceProtocol = Injected(MethodLifecycleServiceProtocol),  # noqa: B008
) -> Envelope[CompositionEdgeDto]:
    result = service.detach(
        collab_context_id,
        edge_id,
        MethodStateChangeRequest(reason="detach").to_domain(),
        _actor_context(user),
    )
    return envelope(CompositionEdgeDto(edge=result.edge), request)


# ---------------------------------------------------------------------------
# Single / Group 操作族(spec §4.1 / §4.2)
# ---------------------------------------------------------------------------


@router.post(
    _BINDINGS + "/{binding_id}/single/select",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def select_single(
    collab_context_id: str,
    binding_id: str,
    body: SingleSelectRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: GroupMethodOpsServiceProtocol = Injected(GroupMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.select(
        collab_context_id, binding_id, body.to_domain(), _actor_context(user)
    )
    return envelope(_profile_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/single/replace",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def replace_single(
    collab_context_id: str,
    binding_id: str,
    body: SingleReplaceRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: GroupMethodOpsServiceProtocol = Injected(GroupMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.replace(
        collab_context_id, binding_id, body.to_domain(), _actor_context(user)
    )
    return envelope(_profile_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/single/release",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def release_single(
    collab_context_id: str,
    binding_id: str,
    body: MethodStateChangeRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: GroupMethodOpsServiceProtocol = Injected(GroupMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.release(
        collab_context_id, binding_id, body.to_domain(), _actor_context(user)
    )
    return envelope(_profile_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/single/delegate",
    response_model=Envelope[CompositionEdgeDto],
)
@envelope_errors
async def delegate_single(
    collab_context_id: str,
    binding_id: str,
    body: SingleDelegateRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: GroupMethodOpsServiceProtocol = Injected(GroupMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[CompositionEdgeDto]:
    result = service.delegate(
        collab_context_id, binding_id, body.to_domain(), _actor_context(user)
    )
    return envelope(CompositionEdgeDto(edge=result.edge), request)


@router.post(
    _BINDINGS + "/{binding_id}/group/invite",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def invite_group_member(
    collab_context_id: str,
    binding_id: str,
    body: GroupMemberRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: GroupMethodOpsServiceProtocol = Injected(GroupMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.invite(
        collab_context_id, binding_id, body.to_domain(), _actor_context(user)
    )
    return envelope(_profile_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/group/members",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def add_group_member(
    collab_context_id: str,
    binding_id: str,
    body: GroupMemberRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: GroupMethodOpsServiceProtocol = Injected(GroupMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.add_member(
        collab_context_id, binding_id, body.to_domain(), _actor_context(user)
    )
    return envelope(_profile_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/group/suspend",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def suspend_group_member(
    collab_context_id: str,
    binding_id: str,
    body: GroupMemberRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: GroupMethodOpsServiceProtocol = Injected(GroupMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.suspend_member(
        collab_context_id, binding_id, body.to_domain(), _actor_context(user)
    )
    return envelope(_profile_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/group/resume",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def resume_group_member(
    collab_context_id: str,
    binding_id: str,
    body: GroupMemberRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: GroupMethodOpsServiceProtocol = Injected(GroupMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.resume_member(
        collab_context_id, binding_id, body.to_domain(), _actor_context(user)
    )
    return envelope(_profile_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/group/leave",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def leave_group(
    collab_context_id: str,
    binding_id: str,
    body: GroupMemberRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: GroupMethodOpsServiceProtocol = Injected(GroupMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.leave(
        collab_context_id, binding_id, body.to_domain(), _actor_context(user)
    )
    return envelope(_profile_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/group/remove",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def remove_group_member(
    collab_context_id: str,
    binding_id: str,
    body: GroupMemberRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: GroupMethodOpsServiceProtocol = Injected(GroupMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.remove_member(
        collab_context_id, binding_id, body.to_domain(), _actor_context(user)
    )
    return envelope(_profile_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/group/replace",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def replace_group_member(
    collab_context_id: str,
    binding_id: str,
    body: GroupReplaceMemberRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: GroupMethodOpsServiceProtocol = Injected(GroupMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.replace_member(
        collab_context_id, binding_id, body.to_domain(), _actor_context(user)
    )
    return envelope(_profile_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/group/change-role",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def change_group_member_role(
    collab_context_id: str,
    binding_id: str,
    body: GroupChangeRoleRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: GroupMethodOpsServiceProtocol = Injected(GroupMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.change_role(
        collab_context_id, binding_id, body.to_domain(), _actor_context(user)
    )
    return envelope(_profile_accepted(result), request)


# ---------------------------------------------------------------------------
# Swarm 操作族(spec §4.3)
# ---------------------------------------------------------------------------


@router.post(
    _BINDINGS + "/{binding_id}/swarm/execution-profiles",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def declare_swarm_execution_profile(
    collab_context_id: str,
    binding_id: str,
    body: SwarmExecutionProfileRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: SwarmMethodOpsServiceProtocol = Injected(SwarmMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.declare_execution_profile(
        collab_context_id, binding_id, body.to_domain(), _actor_context(user)
    )
    return envelope(_profile_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/swarm/plans",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def submit_swarm_plan_candidate(
    collab_context_id: str,
    binding_id: str,
    body: SwarmPlanCandidateRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: SwarmMethodOpsServiceProtocol = Injected(SwarmMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.submit_plan_candidate(
        collab_context_id, binding_id, body.to_domain(), _actor_context(user)
    )
    return envelope(_profile_accepted(result), request)


@router.get(
    _BINDINGS + "/{binding_id}/swarm/plans/{plan_id}/validate",
    response_model=Envelope[SwarmPlanValidationReportDto],
)
@envelope_errors
async def validate_swarm_plan(
    collab_context_id: str,
    binding_id: str,
    plan_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: SwarmMethodOpsServiceProtocol = Injected(SwarmMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[SwarmPlanValidationReportDto]:
    report = service.validate_plan(
        collab_context_id, binding_id, plan_id, _actor_context(user)
    )
    return envelope(SwarmPlanValidationReportDto(**report), request)


@router.post(
    _BINDINGS + "/{binding_id}/swarm/plans/{plan_id}/adopt",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def adopt_swarm_plan(
    collab_context_id: str,
    binding_id: str,
    plan_id: str,
    body: SwarmPlanOpRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: SwarmMethodOpsServiceProtocol = Injected(SwarmMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.adopt_plan(
        collab_context_id, binding_id, plan_id, body.to_domain(),
        _actor_context(user),
    )
    return envelope(_profile_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/swarm/plans/{plan_id}/start",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def start_swarm_plan(
    collab_context_id: str,
    binding_id: str,
    plan_id: str,
    body: SwarmPlanOpRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: SwarmMethodOpsServiceProtocol = Injected(SwarmMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.start_plan(
        collab_context_id, binding_id, plan_id, body.to_domain(),
        _actor_context(user),
    )
    return envelope(_profile_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/swarm/plans/{plan_id}/pause",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def pause_swarm_plan(
    collab_context_id: str,
    binding_id: str,
    plan_id: str,
    body: SwarmPlanOpRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: SwarmMethodOpsServiceProtocol = Injected(SwarmMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.pause_plan(
        collab_context_id, binding_id, plan_id, body.to_domain(),
        _actor_context(user),
    )
    return envelope(_profile_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/swarm/plans/{plan_id}/resume",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def resume_swarm_plan(
    collab_context_id: str,
    binding_id: str,
    plan_id: str,
    body: SwarmPlanOpRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: SwarmMethodOpsServiceProtocol = Injected(SwarmMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.resume_plan(
        collab_context_id, binding_id, plan_id, body.to_domain(),
        _actor_context(user),
    )
    return envelope(_profile_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/swarm/plans/{plan_id}/terminate",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def terminate_swarm_plan(
    collab_context_id: str,
    binding_id: str,
    plan_id: str,
    body: SwarmPlanOpRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: SwarmMethodOpsServiceProtocol = Injected(SwarmMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.terminate_plan(
        collab_context_id, binding_id, plan_id, body.to_domain(),
        _actor_context(user),
    )
    return envelope(_profile_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/swarm/stages/{stage_id}/allocate",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def allocate_swarm_stage(
    collab_context_id: str,
    binding_id: str,
    stage_id: str,
    body: SwarmAllocateRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: SwarmMethodOpsServiceProtocol = Injected(SwarmMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.allocate_stage(
        collab_context_id, binding_id, body.to_domain(), _actor_context(user)
    )
    return envelope(_profile_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/swarm/stages/{stage_id}/complete",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def complete_swarm_stage(
    collab_context_id: str,
    binding_id: str,
    stage_id: str,
    body: SwarmStageOutcomeRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: SwarmMethodOpsServiceProtocol = Injected(SwarmMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.complete_stage(
        collab_context_id, binding_id, stage_id, body.to_domain(),
        _actor_context(user),
    )
    return envelope(_profile_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/swarm/stages/{stage_id}/fail",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def fail_swarm_stage(
    collab_context_id: str,
    binding_id: str,
    stage_id: str,
    body: SwarmStageOutcomeRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: SwarmMethodOpsServiceProtocol = Injected(SwarmMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.fail_stage(
        collab_context_id, binding_id, stage_id, body.to_domain(),
        _actor_context(user),
    )
    return envelope(_profile_accepted(result), request)


# ---------------------------------------------------------------------------
# Open 操作族(spec §4.4)
# ---------------------------------------------------------------------------


@router.post(
    _BINDINGS + "/{binding_id}/open/opportunities",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def publish_open_opportunity(
    collab_context_id: str,
    binding_id: str,
    body: OpenOpportunityRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: OpenMethodOpsServiceProtocol = Injected(OpenMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.publish_opportunity(
        collab_context_id, binding_id, body.to_domain(), _actor_context(user)
    )
    return envelope(_profile_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/open/opportunities/{opportunity_id}/pause",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def pause_open_opportunity(
    collab_context_id: str,
    binding_id: str,
    opportunity_id: str,
    body: OpenOpportunityOpRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: OpenMethodOpsServiceProtocol = Injected(OpenMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.pause_opportunity(
        collab_context_id, binding_id, opportunity_id, body.to_domain(),
        _actor_context(user),
    )
    return envelope(_profile_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/open/opportunities/{opportunity_id}/reopen",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def reopen_open_opportunity(
    collab_context_id: str,
    binding_id: str,
    opportunity_id: str,
    body: OpenOpportunityOpRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: OpenMethodOpsServiceProtocol = Injected(OpenMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.reopen_opportunity(
        collab_context_id, binding_id, opportunity_id, body.to_domain(),
        _actor_context(user),
    )
    return envelope(_profile_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/open/opportunities/{opportunity_id}/close",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def close_open_opportunity(
    collab_context_id: str,
    binding_id: str,
    opportunity_id: str,
    body: OpenOpportunityOpRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: OpenMethodOpsServiceProtocol = Injected(OpenMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.close_opportunity(
        collab_context_id, binding_id, opportunity_id, body.to_domain(),
        _actor_context(user),
    )
    return envelope(_profile_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/open/opportunities/{opportunity_id}/applications",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def submit_open_application(
    collab_context_id: str,
    binding_id: str,
    opportunity_id: str,
    body: OpenApplicationRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: OpenMethodOpsServiceProtocol = Injected(OpenMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.submit_application(
        collab_context_id, binding_id, opportunity_id, body.to_domain(),
        _actor_context(user),
    )
    return envelope(_profile_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/open/applications/{application_id}/screen",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def screen_open_application(
    collab_context_id: str,
    binding_id: str,
    application_id: str,
    body: OpenDecisionOpRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: OpenMethodOpsServiceProtocol = Injected(OpenMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.screen(
        collab_context_id, binding_id, application_id, body.to_domain(),
        _actor_context(user),
    )
    return envelope(_profile_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/open/applications/{application_id}/admit",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def admit_open_application(
    collab_context_id: str,
    binding_id: str,
    application_id: str,
    body: OpenAdmitRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: OpenMethodOpsServiceProtocol = Injected(OpenMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.admit(
        collab_context_id, binding_id, application_id, body.to_domain(),
        _actor_context(user),
    )
    return envelope(_profile_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/open/applications/{application_id}/reject",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def reject_open_application(
    collab_context_id: str,
    binding_id: str,
    application_id: str,
    body: OpenDecisionOpRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: OpenMethodOpsServiceProtocol = Injected(OpenMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.reject(
        collab_context_id, binding_id, application_id, body.to_domain(),
        _actor_context(user),
    )
    return envelope(_profile_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/open/applications/{application_id}/withdraw",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def withdraw_open_application(
    collab_context_id: str,
    binding_id: str,
    application_id: str,
    body: OpenDecisionOpRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: OpenMethodOpsServiceProtocol = Injected(OpenMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.withdraw(
        collab_context_id, binding_id, application_id, body.to_domain(),
        _actor_context(user),
    )
    return envelope(_profile_accepted(result), request)


@router.post(
    _BINDINGS + "/{binding_id}/open/applications/{application_id}/revoke",
    response_model=Envelope[ProfileOpAcceptedDto],
)
@envelope_errors
async def revoke_open_admission(
    collab_context_id: str,
    binding_id: str,
    application_id: str,
    body: OpenDecisionOpRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: OpenMethodOpsServiceProtocol = Injected(OpenMethodOpsServiceProtocol),  # noqa: B008
) -> Envelope[ProfileOpAcceptedDto]:
    result = service.revoke_admission(
        collab_context_id, binding_id, application_id, body.to_domain(),
        _actor_context(user),
    )
    return envelope(_profile_accepted(result), request)
