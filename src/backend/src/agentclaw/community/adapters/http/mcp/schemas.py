"""Pydantic schemas for MCP API endpoints."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MCPValidationResponse(BaseModel):
    valid: bool
    error: Optional[str] = None
    requires_api_key: bool = False
    server_code: Optional[str] = None


class MCPListResponse(BaseModel):
    success: bool
    data: List[Dict[str, Any]]
    total: int
    page_num: int
    page_size: int


class MCPDetailResponse(BaseModel):
    success: bool
    data: Dict[str, Any]


class MCPPermissionResponse(BaseModel):
    success: bool
    has_permission: bool
    access_level: Optional[str] = None
    tool_permissions: Optional[Dict[str, Any]] = None


class TenantCategory(BaseModel):
    """租户类目"""
    parentCode: Optional[str] = None
    parentType: Optional[str] = None
    code: str
    name: str
    children: List[Dict[str, Any]] = []


class TenantItem(BaseModel):
    """租户项"""
    site: Optional[str] = None
    archDomainCode: Optional[str] = None
    archDomainName: Optional[str] = None
    code: str
    name: str
    categories: List[TenantCategory] = []


class TenantListResponse(BaseModel):
    """租户列表响应"""
    success: bool
    data: List[TenantItem]


class MCPUnifiedConfigRequest(BaseModel):
    """统一MCP配置请求"""
    server_code: str = Field(..., description="MCP Server Code")
    api_key: Optional[str] = Field(None, description="API Key (授权格式: authorization=xxx 或 x-ling-auth=Bearer xxx). None 表示不修改.")
    headers: Optional[Dict[str, str]] = Field(None, description="自定义 Headers. None 表示不修改.")
    endpoint_env: Optional[str] = Field(None, description="Endpoint 环境: PROD/PRE. None 表示不修改.")
    transport_protocol: Optional[str] = Field(None, description="传输协议: SSE / STREAMABLE_HTTP. None 表示不修改.")
    sync_mode: str = Field(default="single", description="同步模式: single(只同步指定bot) / broadcast(广播到所有bot)")


class MCPSyncResult(BaseModel):
    """MCP同步结果"""
    conn_info: Optional[dict[str, Any]] = None
    bot_id: Optional[str] = None
    synced: bool
    reason: Optional[str] = None
    error: Optional[str] = None


class MCPUnifiedConfigData(BaseModel):
    """统一MCP配置数据"""
    server_code: str
    api_key: Optional[str] = None  # 返回 masked
    endpoint_env: Optional[str] = None
    transport_protocol: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    has_config: bool = False
    sync_results: Optional[List[MCPSyncResult]] = None  # 广播同步结果


class MCPUnifiedConfigResponse(BaseModel):
    """统一MCP配置响应"""
    success: bool
    message: str
    data: Optional[MCPUnifiedConfigData] = None


class MCPApplyPermissionRequest(BaseModel):
    """MCP 权限申请请求"""
    server_code: str = Field(..., description="MCP Server Code")
    tool_list: List[str] = Field(default_factory=list, description="Tool 名称列表，为空则只申请 Server 级别")
    is_public: bool = Field(True, description="是否公开 Server (PUBLIC 模式)")
    reason: str = Field("", description="申请原因")


class MCPApplyPermissionResponse(BaseModel):
    """MCP 权限申请响应"""
    success: bool
    server_code: str
    process_url: Optional[str] = None
    error: Optional[str] = None
