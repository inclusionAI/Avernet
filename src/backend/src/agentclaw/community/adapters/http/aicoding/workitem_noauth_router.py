"""工作项路由 — 创建工作项、添加关联关系、Arkgw 文件上传。
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field, field_validator

from agentclaw.community.api.workitem_service import WorkItemServiceProtocol
from agentclaw.community.di import Injected


class CreateWorkItemRequest(BaseModel):
    """创建工作项请求 — 透传模式。"""

    staff_id: str = Field(..., alias="staffId", description="用户工号")
    workspace_id: str = Field(..., alias="workspaceId", description="需求空间 ID")
    subject: str = Field(..., description="工作项标题")
    content: str = Field(..., description="工作项详情描述")
    format_type: str = Field("MARKDOWN", alias="formatType", description="文档格式: MARKDOWN / RICHTEXT")

    model_config = {"extra": "allow", "populate_by_name": True}


class CreateRelationRequest(BaseModel):
    """添加工作项关联关系请求（仅 common 类型）。"""

    operator: str = Field("100000", description="操作人工号")
    relation_identifier: Optional[str] = Field(None, alias="relationIdentifier", description="关联类型标识")
    source_identifier: str = Field(..., alias="sourceIdentifier", description="源工作项标识")
    relation_id: Optional[str] = Field(None, alias="relationId", description="关联关系 ID")
    to_value: str = Field(..., alias="toValue", description="关联目标值")
    to_category: Optional[str] = Field(None, alias="toCategory", description="关联目标类别")

    model_config = {"extra": "allow", "populate_by_name": True}

    def model_post_init(self, __context: Any) -> None:
        if self.relation_identifier is None:
            self.relation_identifier = "COMMON"
        if self.relation_id is None:
            self.relation_id = "20001"
        if self.to_category is None:
            self.to_category = "Req"


class UpdateWorkItemRequest(BaseModel):
    """工作项关联 URL 请求 — 专用于给工作项添加 URL 关联。"""

    operator: str = Field("100000", description="操作人工号")
    work_item_id: str = Field(..., alias="dimaId", description="工作项标识")
    url: str = Field(..., description="要关联的 URL")

    model_config = {"extra": "allow", "populate_by_name": True}

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return normalize_dima_http_url(value)


class DeleteRelationRequest(BaseModel):
    """删除工作项关联关系请求（精确按 relationRecordId 删单条）。"""

    operator: str = Field("100000", description="操作人工号")
    relation_identifier: str = Field(..., alias="relationIdentifier", description="关联类型标识，如 URL / COMMON 等")
    relation_record_id: str = Field(..., alias="relationRecordId", description="要删除的关联记录 ID（来自 list 查询）")

    model_config = {"extra": "allow", "populate_by_name": True}


class DimaUpdateWorkItemDocumentRequest(BaseModel):
    """修改工作项描述内容请求。"""

    staff_id: str = Field(..., alias="staffId", description="用户工号")
    work_item_id: str = Field(..., alias="workItemId", description="工作项 ID")
    content: str = Field(..., description="工作项描述内容")
    format_type: str = Field("MARKDOWN", alias="formatType", description="文档格式: RICHTEXT | MARKDOWN")
    editor_type: str = Field("YUQUE", alias="editorType", description="编辑器类型，默认 YUQUE")

    model_config = {"extra": "allow", "populate_by_name": True}


class WorkItemApiResponse(BaseModel):
    success: bool
    code: str = "200"
    message: str = "OK"
    data: Optional[Any] = None


_ALLOWED_DIMA_URL_SCHEMES = {"http", "https"}
_MAX_DIMA_URL_LENGTH = 4096


def normalize_dima_http_url(raw_url: str) -> str:
    """Normalize and validate DIMA URL relation input.

    This endpoint is a pass-through wrapper for DIMA URL relation capability.
    To avoid narrowing DIMA's HTTP(S) linking ability, this validation only
    rejects malformed URLs and non-HTTP(S) schemes such as file:// or gopher://.
    It intentionally does not restrict domains, IP ranges, DNS results, or ports.
    """
    value = raw_url.strip()
    if not value:
        raise ValueError("invalid url")
    if len(value) > _MAX_DIMA_URL_LENGTH:
        raise ValueError("invalid url")

    parsed = urlsplit(value)
    if parsed.scheme.lower() not in _ALLOWED_DIMA_URL_SCHEMES:
        raise ValueError("invalid url")
    if not parsed.hostname:
        raise ValueError("invalid url")

    return value


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/public/dima", tags=["dima"])


@router.post("/work-items/create", response_model=WorkItemApiResponse)
async def create_work_item(
    req: CreateWorkItemRequest,
    service: WorkItemServiceProtocol = Injected(WorkItemServiceProtocol),
) -> WorkItemApiResponse:
    """创建工作项（透传模式）。

    请求体中 staffId 为必填，其余字段直接透传到 OpenAPI。
    """
    logger.info(
        "[workitem.create] staffId=%s, keys=%s",
        req.staff_id, list(req.model_dump(by_alias=True, exclude_none=True).keys()),
    )
    try:
        body = req.model_dump(by_alias=True, exclude_none=True)
        staff_id = body.pop("staffId")
        content = body.pop("content")
        if not content or not content.strip():
            return WorkItemApiResponse(
                success=False, code="400",
                message="content 为空，请在入参 content 字段里提供 content 信息",
            )
        format_type = body.pop("formatType", "MARKDOWN")
        body["workItemDocument"] = {
            "formatType": format_type,
            "editorType": "YUQUE",
            "content": content,
        }
        result = service.create_work_item(
            staff_id=staff_id,
            request_body=body,
        )
        return WorkItemApiResponse(success=True, code="200", message="Work item created", data=result.get("data"))
    except Exception as e:
        logger.error("[workitem.create] Failed: %s", e, exc_info=True)
        return WorkItemApiResponse(success=False, code="500", message=str(e))


@router.post("/work-items/relation/create", response_model=WorkItemApiResponse)
async def create_work_item_relation(
    req: CreateRelationRequest,
    service: WorkItemServiceProtocol = Injected(WorkItemServiceProtocol),
) -> WorkItemApiResponse:
    """添加工作项关联关系。

    POST /api/public/dima/work-items/relation/create
    Body: {
        "operator": "100000",
        "relationIdentifier": "URL",
        "sourceIdentifier": "2024080600104049562",
        "relationId": "RELATION00100000002",
        "toValue": "https://example.com",
        "toCategory": "Url"
    }
    """
    logger.info(
        "[workitem.create_relation] operator=%s, sourceIdentifier=%s",
        req.operator, req.source_identifier,
    )
    try:
        body = req.model_dump(by_alias=True, exclude_none=True, exclude={"type"})
        operator = body.pop("operator")
        result = service.create_work_item_relation(
            operator=operator,
            request_body=body,
        )
        return WorkItemApiResponse(success=True, code="200", message="Relation created", data=result.get("data"))
    except Exception as e:
        logger.error("[workitem.create_relation] Failed: %s", e, exc_info=True)
        return WorkItemApiResponse(success=False, code="500", message=str(e))


@router.post("/work-items/append-file", response_model=WorkItemApiResponse)
async def append_file_to_work_item(
    req: UpdateWorkItemRequest,
    service: WorkItemServiceProtocol = Injected(WorkItemServiceProtocol),
) -> WorkItemApiResponse:
    """给工作项关联 URL。

    POST /api/public/dima/work-items/append-file
    Body: {
        "operator": "100000",
        "dimaId": "2024080600104049562",
        "url": "https://example.com"
    }
    """
    logger.info(
        "[workitem.append_file] operator=%s, dimaId=%s, url=%s",
        req.operator, req.work_item_id, req.url,
    )
    try:
        body = {
            "relationIdentifier": "URL",
            "sourceIdentifier": req.work_item_id,
            "relationId": "RELATION00100000002",
            "toValue": req.url,
            "toCategory": "Url",
        }
        result = service.create_work_item_relation(
            operator=req.operator,
            request_body=body,
        )
        return WorkItemApiResponse(success=True, code="200", message="URL linked", data=result.get("data"))
    except Exception as e:
        logger.error("[workitem.append_file] Failed: %s", e, exc_info=True)
        return WorkItemApiResponse(success=False, code="500", message=str(e))


@router.post("/work-items/relation/delete", response_model=WorkItemApiResponse)
async def delete_work_item_relation(
    req: DeleteRelationRequest,
    service: WorkItemServiceProtocol = Injected(WorkItemServiceProtocol),
) -> WorkItemApiResponse:
    """删除工作项关联关系（精确按 relationRecordId 删单条）。

    POST /api/public/dima/work-items/relation/delete
    Body: {
        "operator": "100000",
        "relationIdentifier": "URL",
        "relationRecordId": "2024041800100538244"
    }

    调用方需先经 list 查询拿到要删记录的 relationRecordId；本接口不替调用方决定删哪条。
    """
    logger.info(
        "[workitem.delete_relation] operator=%s, relationIdentifier=%s, relationRecordId=%s",
        req.operator, req.relation_identifier, req.relation_record_id,
    )
    try:
        body = req.model_dump(by_alias=True, exclude_none=True)
        operator = body.pop("operator")
        result = service.delete_work_item_relation(
            operator=operator,
            request_body=body,
        )
        return WorkItemApiResponse(success=True, code="200", message="Relation deleted", data=result.get("data"))
    except Exception as e:
        logger.error("[workitem.delete_relation] Failed: %s", e, exc_info=True)
        return WorkItemApiResponse(success=False, code="500", message=str(e))


@router.get("/work-items/relation/list", response_model=WorkItemApiResponse)
async def list_work_item_relations(
    workItemId: str = Query(..., description="工作项 ID"),
    operator: str = Query("100000", description="操作人工号"),
    service: WorkItemServiceProtocol = Injected(WorkItemServiceProtocol),
) -> WorkItemApiResponse:
    """查询工作项关联记录（按 relationIdentifier 分组）。

    GET /api/public/dima/work-items/relation/list?workItemId=&operator=

    返回 data 为按 relationIdentifier 分组的 dict，例如：
        {"SUB": [...], "PARENT": [...], "COMMON": [...], "URL": [...], "ATTACHMENT": [...]}

    每条记录含 relationRecordId / relationId / relationName / url / identifier 等。
    调用方据此自行决定要 delete 哪条（本接口不替调用方做选择）。
    """
    logger.info(
        "[workitem.list_relation] operator=%s, workItemId=%s",
        operator, workItemId,
    )
    try:
        result = service.list_work_item_relations(
            work_item_id=workItemId,
            operator=operator,
        )
        return WorkItemApiResponse(success=True, code="200", message="OK", data=result.get("data"))
    except Exception as e:
        logger.error("[workitem.list_relation] Failed: %s", e, exc_info=True)
        return WorkItemApiResponse(success=False, code="500", message=str(e))

@router.post("/work-items/document/update", response_model=WorkItemApiResponse)
async def update_work_item_document(
    req: DimaUpdateWorkItemDocumentRequest,
    service: WorkItemServiceProtocol = Injected(WorkItemServiceProtocol),
) -> WorkItemApiResponse:
    """修改工作项描述内容。

    POST /api/public/dima/work-items/document/update
    Body: {
        "staffId": "xxx",
        "workItemId": "xxx",
        "content": "<p>工作项描述内容</p>",
        "formatType": "RICHTEXT",
        "editorType": "YUQUE"
    }
    """
    logger.info(
        "[dima.update_document] staffId=%s, workItemId=%s, formatType=%s",
        req.staff_id, req.work_item_id, req.format_type,
    )
    try:
        if not req.content or not req.content.strip():
            return WorkItemApiResponse(
                success=False, code="400",
                message="content 为空，请提供工作项描述内容",
            )
        result = service.update_work_item_document(
            staff_id=req.staff_id,
            work_item_id=req.work_item_id,
            content=req.content,
            format_type=req.format_type,
            editor_type=req.editor_type,
        )
        return WorkItemApiResponse(
            success=True, code="200",
            message="Work item document updated",
            data=result.get("data"),
        )
    except Exception as e:
        logger.error("[dima.update_document] Failed: %s", e, exc_info=True)
        return WorkItemApiResponse(success=False, code="500", message=str(e))


# ==================== Arkgw 文件上传 ====================


@router.post("/arkgw/file/upload", response_model=WorkItemApiResponse)
async def upload_file_to_arkgw(
    staffId: str = Form("100000", description="工号（补0），默认 100000"),
    sourceId: str = Form("agentCoding", description="调用方系统名，默认 agentCoding"),
    file: Optional[UploadFile] = File(None, description="文件对象，最大 10MB"),
    url: Optional[str] = Form(None, description="图片文件链接（转存场景）"),
    service: WorkItemServiceProtocol = Injected(WorkItemServiceProtocol),
) -> WorkItemApiResponse:
    """上传文件到 Arkgw。

    file 和 url 二选一：
    - file 非空时上传文件
    - url 非空时转存图片链接
    """
    if not file and not url:
        raise HTTPException(status_code=400, detail="file 和 url 不能同时为空")

    file_content: bytes | None = None
    file_name: str | None = None
    content_type: str = "application/octet-stream"

    if file:
        file_content = await file.read()
        if len(file_content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="文件大小不能超过 10MB")
        file_name = file.filename
        content_type = file.content_type or "application/octet-stream"

    try:
        result = service.upload_file_to_arkgw(
            staff_id=staffId,
            source_id=sourceId,
            file_content=file_content,
            file_name=file_name,
            content_type=content_type,
            url=url,
        )
    except Exception as e:
        logger.error("[workitem.arkgw_upload] Failed: %s", e, exc_info=True)
        return WorkItemApiResponse(success=False, code="500", message=str(e))

    return WorkItemApiResponse(
        success=result.get("success", False),
        code=result.get("code", ""),
        message=result.get("message", ""),
        data=result.get("data"),
    )
