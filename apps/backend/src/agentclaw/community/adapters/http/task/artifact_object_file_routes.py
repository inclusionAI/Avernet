"""OBJECT_FILE 产物路由:POST /artifacts/object-file(上传)+ GET /artifacts/{id}/content(下载)。

spec 2026-10-20-task-artifact-domain §1.3 的 2026-10-26 修订:OBJECT_FILE 介质经
后端流式代理搬字节(见 core ``TaskArtifactObjectFileService``);既有两介质维持
原裁决。领域语义全部在核心服务;HTTP 层只转协议(Rule 22):

- 上传:multipart(task_id/node_id/attempt/artifact_name?/file)→ 一步原子上传,
  成功返回读侧 Descriptor;put 失败不落行(502),内容/归属失配 422,超上限 413,
  服务未接线 503。
- 下载:按 artifact_id 逐次校验后回发字节(200);行未知/对象缺失 404,存储失败
  502,非本介质 422。任何临时凭证不落产物内容行(§1.4 不变)。
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.adapters.http.task.artifact_actor import (
    ArtifactWriteActor,
    resolve_artifact_write_actor,
)
from agentclaw.community.adapters.http.task.schemas_artifact import (
    TaskArtifactDescriptorDTO,
    artifact_record_to_descriptor,
)
from agentclaw.community.api.task.task_artifact_object_file_service import (
    TaskArtifactObjectFileServiceProtocol,
)
from agentclaw.community.di import Injected

router = APIRouter(tags=["task"])

_DEFAULT_FILE_TYPE = "application/octet-stream"
_DEFAULT_FILE_NAME = "artifact.bin"


@router.post(
    "/artifacts/object-file",
    response_model=Envelope[TaskArtifactDescriptorDTO],
)
@envelope_errors
async def upload_task_artifact_object_file(
    request: Request,
    task_id: str = Form(..., description="任务 ID(创建时签发)"),
    node_id: str = Form(..., description="产出节点 ID"),
    attempt: int = Form(..., description="执行轮次(须等于图上 harness_retries 快照)"),
    artifact_name: str | None = Form(
        None, description="业务展示名(可选;缺省回落上传文件名)", max_length=255
    ),
    file: UploadFile = File(..., description="产物文件字节"),
    actor: ArtifactWriteActor = Depends(resolve_artifact_write_actor),
    service: TaskArtifactObjectFileServiceProtocol = Injected(TaskArtifactObjectFileServiceProtocol),  # noqa: B008
) -> Envelope[TaskArtifactDescriptorDTO]:
    """上传一个 OBJECT_FILE 产物(字节先落对象存储,成功后登记产品行;幂等)。

    内容寻址 key 由服务端派生(``{prefix}/{task_id}/{sha256}{ext}``),同一字节
    重复上传收敛为同一行。双凭证(spec §14):``X-Relay-Token``(relay 尝试,token
    fence + creator=节点 assignee)或登录用户(creator=user.id)二选一;下载读口
    仍为用户面。"""
    payload = await file.read()
    record = service.upload_object_file(
        task_id=task_id,
        node_id=node_id,
        attempt=attempt,
        artifact_name=artifact_name,
        file_name=file.filename or _DEFAULT_FILE_NAME,
        content_type=file.content_type,
        payload=payload,
        creator=actor.user.id if actor.user else None,
        relay_token=actor.relay_token,
    )
    return envelope(artifact_record_to_descriptor(record), request)


@router.get("/artifacts/{artifact_id}/content")
@envelope_errors
async def download_task_artifact_object_file(
    request: Request,
    artifact_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: TaskArtifactObjectFileServiceProtocol = Injected(TaskArtifactObjectFileServiceProtocol),  # noqa: B008
) -> Response:
    """下载一个 OBJECT_FILE 产物的字节(后端代理;逐次校验,无缓存凭证)。

    访问姿势与既有产物列表/看板读口一致(认证用户);行上只存稳定对象 key。
    404 = artifact_id 未知或对象缺失;502 = 对象存储不可用;503 = 服务未接线。"""
    del user
    download = service.read_object_file(artifact_id)
    content = download.record.content
    file_name = getattr(content, "file_name", _DEFAULT_FILE_NAME) or _DEFAULT_FILE_NAME
    file_type = getattr(content, "file_type", "") or _DEFAULT_FILE_TYPE
    return Response(
        content=download.payload,
        media_type=file_type,
        headers={
            "Content-Disposition": (
                "attachment; filename*=UTF-8''" + quote(file_name)
            ),
            "Content-Length": str(len(download.payload)),
        },
    )