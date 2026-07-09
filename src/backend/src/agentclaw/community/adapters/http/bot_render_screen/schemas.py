"""Bot Render Screen API Schemas — Pydantic Request/Response models."""

from pydantic import BaseModel, Field


class CreateRenderScreenRequest(BaseModel):
    """添加 CDN 链接请求。"""
    bot_id: str = Field(..., description="Bot ID")
    name: str = Field(..., description="链接名称，同一 Bot 下唯一")
    cdn_url: str = Field(..., description="CDN 链接地址")


class UpdateRenderScreenRequest(BaseModel):
    """更新 CDN 链接请求。"""
    name: str = Field(..., description="链接名称")
    cdn_url: str = Field(..., description="CDN 链接地址")


class RenderScreenResponse(BaseModel):
    """单条 CDN 配置响应。"""
    id: int
    bot_id: str
    owner_id: str
    name: str
    cdn_url: str
    creator_id: str
    gmt_create: str | None = None
    gmt_modified: str | None = None


class RenderScreenApiResponse(BaseModel):
    """统一 API 响应格式。"""
    success: bool
    message: str = "OK"
    error_code: int = 200
    data: dict | list | None = None
