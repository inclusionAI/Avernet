"""AntProcess 流程审批 Request/Response 模型定义"""
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class StartApprovalRequest(BaseModel):
    """发起审批请求"""
    process_code: str = Field(..., description="流程编码（如：AGENTCLAW_APPROVAL）")
    applicant: str = Field(..., description="申请人工号")
    biz_id: str = Field(..., description="业务唯一ID")
    biz_type: Optional[str] = Field(default=None, description="业务类型，不传则使用 process_code")
    unique_key: Optional[str] = Field(default=None, description="唯一标志，不传则不使用")
    context: Optional[dict] = Field(default={}, description="业务上下文（回调时原样返回）")


class StartApprovalResponse(BaseModel):
    """发起审批响应"""
    success: bool
    puid: Optional[str] = None
    approval_url: Optional[str] = None
    error_msg: Optional[str] = None


class QueryStatusRequest(BaseModel):
    """查询状态请求"""
    puid: str = Field(..., description="流程实例ID，格式: {appName}_{bizType}_{bizId}")


class QueryStatusResponse(BaseModel):
    """查询状态响应"""
    success: bool
    status: Optional[str] = None
    title: Optional[str] = None
    applicant: Optional[str] = None
    process_id: Optional[int] = None
    error_msg: Optional[str] = None


class CancelApprovalRequest(BaseModel):
    """取消审批请求"""
    puid: str = Field(..., description="流程实例ID，格式: {appName}_{bizType}_{bizId}")
    operator: str = Field(..., description="操作人")


class CallbackRequest(BaseModel):
    """审批回调请求（流程平台推送）"""
    app_name: Optional[str] = Field(default="", description="app_name")
    biz_id: Optional[str] = Field(default="", description="biz_id")
    biz_type: Optional[str] = Field(default="", description="biz_type")
    status: Optional[str] = Field(default=None, description="审批状态: AGREE/REJECT/CANCEL")
    context: Optional[Dict[str, Any]] = Field(default=None, description="业务上下文")
