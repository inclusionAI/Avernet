"""Resources API — Pydantic Request/Response models.

Only Pydantic BaseModel definitions here.
No imports from core / plugin_api / plugins.
"""
from typing import Optional, Any, Dict, List

from pydantic import BaseModel, Field


# ==================== Request Models ====================


class URLCreateRequest(BaseModel):
    """Create URL resource request."""
    name: str = Field(..., description="资源显示名称")
    url: str = Field(..., description="目标 URL")
    method: str = Field("GET", description="HTTP 方法")
    headers: Optional[Dict[str, str]] = Field(None, description="请求头键值对")
    metadata: Optional[Dict[str, Any]] = Field(None, description="自定义 metadata")
    parent_path: Optional[str] = Field(None, description="所在目录路径")


class NodeCreateRequest(BaseModel):
    """Create Node resource request."""
    name: str = Field(..., description="资源显示名称")
    node_address: str = Field(..., description="节点地址/端点")
    path_alias: Optional[str] = Field(None, description="节点显示别名（默认同 name）")
    scan_recursive: bool = Field(True, description="是否递归扫描子目录")
    metadata: Optional[Dict[str, Any]] = Field(None, description="自定义 metadata")
    parent_path: Optional[str] = Field(None, description="所在目录路径")


class LinkItem(BaseModel):
    """Single link item within a batch create request."""
    url: str = Field(..., description="链接地址（必填）")
    name: Optional[str] = Field(None, description="链接显示名称（可选，默认使用 URL）")
    access_modes: List[str] = Field(default=["READ"], description="权限模式: READ, WRITE")


class UpdateLinkRequest(BaseModel):
    """Update a LINK resource type and/or URL."""
    link_type: Optional[str] = Field(None, description="新的 link_type（yuque/dima/antcode）")
    url: Optional[str] = Field(None, description="新的链接地址")
    name: Optional[str] = Field(None, description="新的显示名称")
    access_modes: Optional[List[str]] = Field(None, description="权限模式: READ, WRITE")


class BatchLinkCreateRequest(BaseModel):
    """Batch create LINK resources request.

    Keys are link_type (yuque/dima/antcode), values are lists of LinkItem.
    Example:
        {
            "links": {
                "yuque": [{"url": "https://yuque.example.com/xxx", "name": "知识库"}],
                "dima": [{"url": "https://dima.example.com/yyy"}]
            }
        }
    """
    links: Dict[str, List[LinkItem]] = Field(
        ...,
        description="按 link_type 分组的链接列表。key 为 link_type（yuque/dima/antcode），value 为该类型下的链接数组",
    )


# ==================== Response: CheckName ====================


class CheckNameData(BaseModel):
    available: bool = Field(..., description="名称是否可用")
    message: str = Field(..., description="说明")


class CheckNameResponse(BaseModel):
    success: bool
    data: CheckNameData


# ==================== Resource List Item ====================


class ResourceListItem(BaseModel):
    """Resource item for list/detail response - unified format for all types."""
    id: Optional[str] = Field(None, description="Resource ID")
    name: str = Field(..., description="Resource display name")
    resource_type: str = Field(..., description="Type: file, url, node, link")
    status: str = Field(..., description="Status: active, pending, error, deleted")
    size: Optional[int] = Field(None, description="File size in bytes")
    path: Optional[str] = Field(None, description="File path")
    url: Optional[str] = Field(None, description="URL link")
    node_address: Optional[str] = Field(None, description="Node address")
    link_type: Optional[str] = Field(None, description="Link type: yuque/dima/antcode (LINK only)")
    description: Optional[str] = Field(None, description="Resource description (LINK only)")
    access_modes: Optional[list[str]] = Field(None, description="Access modes: READ, WRITE (LINK only)")
    is_directory: bool = Field(False, description="Is directory")
    extension: Optional[str] = Field(None, description="File extension")
    child_count: int = Field(0, description="Child count for directories")
    user_id: Optional[str] = Field(None, description="Owner user ID")
    gmt_created: str = Field(..., description="Creation time")
    gmt_modified: str = Field(..., description="Last modified time")

    # Additional fields for detail view
    mime_type: Optional[str] = Field(None, description="MIME type")
    parent_path: Optional[str] = Field(None, description="Parent path")
    content_hash: Optional[str] = Field(None, description="Content hash")
    preview_available: bool = Field(False, description="Preview available")


# ==================== Response Data Models ====================


class ResourceDeleteData(BaseModel):
    """Response data for resource deletion."""
    resource_id: str = Field(..., description="Deleted resource ID")


class ResourceDeleteResponse(BaseModel):
    """Response for resource deletion."""
    success: bool = Field(..., description="Whether deletion was successful")
    message: str = Field(..., description="Response message")
    data: Optional[ResourceDeleteData] = Field(None, description="Deletion result data")


class ResourceDetailResponse(BaseModel):
    """Response for resource detail."""
    success: bool = Field(..., description="Whether operation was successful")
    data: Optional[ResourceListItem] = Field(None, description="Resource detail data")


class ResourceListResponse(BaseModel):
    """Response for resource list (file uploads)."""
    success: bool = Field(..., description="Whether operation was successful")
    data: List[ResourceListItem] = Field(default_factory=list, description="List of uploaded resources")
    total: int = Field(0, description="Total count")
    errors: Optional[List[dict]] = Field(None, description="Error details for failed uploads")


class PreviewData(BaseModel):
    """Preview response data for text files."""
    content: str = Field(..., description="File content preview")
    size: int = Field(..., description="Total file size in bytes")


class PreviewResponse(BaseModel):
    """Response for file preview."""
    success: bool = Field(..., description="Whether operation was successful")
    data: Optional[PreviewData] = Field(None, description="Preview data")
    message: Optional[str] = Field(None, description="Response message")


# ---- File-manager (file_router) response models ----


class FileItem(BaseModel):
    name: str = Field(..., description="File or directory name")
    path: str = Field(..., description="Relative path from workspace root")
    absolute_path: Optional[str] = Field(None, description="Absolute path in workspace (for copy to chat)")
    is_dir: bool = Field(..., description="Whether this is a directory")
    readonly: bool = Field(False, description="Whether this item is read-only (cannot delete)")
    size: Optional[int] = Field(None, description="File size in bytes (files only)")
    size_human: Optional[str] = Field(None, description="Human-readable size (files only)")
    modified_at: Optional[str] = Field(None, description="Last modified time ISO format")


class FileListResponse(BaseModel):
    success: bool = True
    path: str = Field("", description="Current directory path")
    items: List[FileItem] = Field(default_factory=list)


class FileUploadResponse(BaseModel):
    success: bool = True
    uploaded: List[FileItem] = Field(default_factory=list)
    errors: Optional[List[dict]] = None


class FileActionResponse(BaseModel):
    success: bool = True
    message: str = ""
