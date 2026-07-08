"""HTTP API 接口：提供流程审批的 HTTP 接口"""

from fastapi import APIRouter, HTTPException, Request

from agentclaw.community.adapters.http.antprocess.schemas import (
    StartApprovalRequest,
    StartApprovalResponse,
    QueryStatusRequest,
    QueryStatusResponse,
    CancelApprovalRequest,
)
from agentclaw.community.core.approval.callback_handler import handle_approval_callback
from agentclaw.community.plugin_api.approval_workflow import ApprovalWorkflowPlugin
from agentclaw.community.api.bot_public_service import BotPublicServiceProtocol
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

logger = get_logger()

# 创建路由器
router = APIRouter(prefix="/api/v1/antprocess", tags=["流程审批"])


# ============ HTTP 接口 ============

@router.post("/start", response_model=StartApprovalResponse, summary="发起审批")
async def start_approval(
    request: StartApprovalRequest,
    service: ApprovalWorkflowPlugin = Injected(ApprovalWorkflowPlugin),
):
    """
    发起一个新的审批流程

    - **process_code**: 流程编码，需要在流程平台预先配置
    - **applicant**: 申请人工号
    - **biz_id**: 业务唯一标识
    - **biz_type**: 业务类型，不传则使用 process_code
    - **context**: 上下文数据（回调时会原样返回）
    """
    result = service.start_approval(
        process_code=request.process_code,
        applicant=request.applicant,
        biz_id=request.biz_id,
        biz_type=request.biz_type,
        unique_key=request.unique_key,
        context=request.context
    )

    return StartApprovalResponse(**result)


@router.post("/query", response_model=QueryStatusResponse, summary="查询审批状态")
async def query_status(
    request: QueryStatusRequest,
    service: ApprovalWorkflowPlugin = Injected(ApprovalWorkflowPlugin),
):
    """
    查询指定审批单的当前状态
    """
    result = service.query_approval_status(puid=request.puid)

    return QueryStatusResponse(**result)


@router.post("/cancel", summary="取消审批")
async def cancel_approval(
    request: CancelApprovalRequest,
    service: ApprovalWorkflowPlugin = Injected(ApprovalWorkflowPlugin),
):
    """
    取消进行中的审批流程
    """
    success = service.cancel_approval(
        puid=request.puid,
        operator=request.operator
    )

    if not success:
        raise HTTPException(status_code=400, detail="取消审批失败")

    return {"success": True, "message": "取消成功"}


@router.post("/callback", summary="审批回调接口")
async def approval_callback(
    request: Request,
    bot_public_service: BotPublicServiceProtocol = Injected(BotPublicServiceProtocol),
):
    """
    接收流程平台审批结果回调
    支持表单格式 (application/x-www-form-urlencoded)
    """
    # 解析表单数据
    form_data = await request.form()
    data = dict(form_data)

    logger.info("[回调] 收到表单数据: %s", data)

    # 转换为统一格式处理
    callback_data = {
        "global_unique_id": data.get("globalUniqueId"),
        "last_operate": data.get("lastOperate"),
        "owner_id": data.get("ownerId"),
        "bot_id": data.get("botId"),
    }

    return handle_approval_callback(callback_data, bot_public_service)
