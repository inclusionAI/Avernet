"""Authenticated resource-scoped content delivery through a deployment plugin."""

from typing import Literal

from agentclaw.community.adapters.http.knowledge_management.content_schemas import (
    AutoSync,
    YuqueImport,
)
from agentclaw.community.adapters.http.knowledge_management.dependencies import (
    context,
    segment,
)
from agentclaw.community.adapters.http.knowledge_management.errors import KnowledgeRoute
from agentclaw.community.core.knowledge_management.content_policy import (
    MAX_UPLOAD,
    validate_upload_name,
)
from agentclaw.community.kernel.knowledge import KnowledgeError
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from starlette.responses import StreamingResponse

router = APIRouter(
    prefix="/api/knowledge/spaces/{space_id}/knowledge-bases/{kb_id}",
    tags=["knowledge-content"],
    route_class=KnowledgeRoute,
)


@router.get("/sources")
async def sources(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    ctx=Depends(context),
):
    return await ctx.call("sources", offset=offset, limit=limit)


async def uploads(files):
    if not 1 <= len(files) <= 20:
        raise HTTPException(400, "Upload 1-20 TXT or Markdown files")
    result, total = [], 0
    for upload in files:
        filename = upload.filename or ""
        mime = validate_upload_name(filename)
        raw = await upload.read(MAX_UPLOAD - total + 1)
        total += len(raw)
        if total > MAX_UPLOAD:
            raise HTTPException(413, "Upload exceeds 20 MiB")
        result.append((filename, raw, mime))
    return result


@router.post("/sources/upload")
async def upload(
    files: list[UploadFile] = File(...),
    on_name_conflict: Literal["ask", "add"] = Form("ask"),
    ctx=Depends(context),
):
    payload = await uploads(files)
    return await ctx.call("upload", files=payload, on_name_conflict=on_name_conflict)


@router.post("/sources/{source_id}/replace")
async def replace(
    source_id: str,
    file: UploadFile = File(...),
    expected_content_hash: str = Form(..., max_length=128),
    ctx=Depends(context),
):
    payload = await uploads([file])
    return await ctx.call(
        "replace",
        source_id=segment(source_id),
        file=payload[0],
        expected_content_hash=expected_content_hash,
    )


@router.delete("/sources/{source_id}")
async def delete(source_id: str, ctx=Depends(context)):
    return await ctx.call("delete", source_id=segment(source_id))


@router.get("/sources/{source_id}/raw")
async def raw(source_id: str, ctx=Depends(context)):
    try:
        reply, chunks = await ctx.remote.raw(
            kb_id=ctx.kb_id, source_id=segment(source_id), access=ctx.access
        )
    except KnowledgeError as exc:
        raise HTTPException(exc.status, detail={"code": exc.code}) from None
    if chunks is None:
        return Response(reply.body, status_code=reply.status, headers=reply.headers)
    return StreamingResponse(chunks, status_code=reply.status, headers=reply.headers)


@router.get("/yuque/connection")
async def yuque_connection(ctx=Depends(context)):
    return await ctx.call("yuque_connection")


@router.get("/yuque/documents")
async def yuque_docs(
    q: str = Query("", max_length=2048),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    ctx=Depends(context),
):
    return await ctx.call("yuque_documents", query=q, offset=offset, limit=limit)


@router.post("/yuque/import")
async def yuque_import(body: YuqueImport, ctx=Depends(context)):
    return await ctx.call("import_yuque", body=body.model_dump(exclude_none=True))


@router.post("/sources/{source_id}/sync")
async def sync(source_id: str, ctx=Depends(context)):
    return await ctx.call("sync", source_id=segment(source_id))


@router.get("/sources/{source_id}/sync-runs/{run_id}")
async def sync_run(source_id: str, run_id: str, ctx=Depends(context)):
    return await ctx.call(
        "sync_run", source_id=segment(source_id), run_id=segment(run_id)
    )


@router.get("/sources/{source_id}/auto-sync")
async def get_schedule(source_id: str, ctx=Depends(context)):
    return await ctx.call("get_schedule", source_id=segment(source_id))


@router.put("/sources/{source_id}/auto-sync")
async def put_schedule(source_id: str, body: AutoSync, ctx=Depends(context)):
    return await ctx.call(
        "put_schedule", source_id=segment(source_id), body=body.model_dump()
    )


@router.get("/ingest-tasks")
async def tasks(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    ctx=Depends(context),
):
    return await ctx.call("tasks", offset=offset, limit=limit)


@router.get("/source-revisions/{revision_id}")
async def revision(revision_id: str, ctx=Depends(context)):
    return await ctx.call("revision", revision_id=segment(revision_id))


@router.get("/activities")
async def activities(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
    ctx=Depends(context),
):
    return await ctx.call("activities", offset=offset, limit=limit)


@router.get("/ingest-jobs/{job_id}")
async def job(job_id: str, ctx=Depends(context)):
    segment(job_id)
    try:
        reply = await ctx.remote.job(kb_id=ctx.kb_id, job_id=job_id, access=ctx.access)
    except KnowledgeError as exc:
        raise HTTPException(exc.status, detail={"code": exc.code}) from None
    return Response(reply.body, status_code=reply.status, headers=reply.headers)
