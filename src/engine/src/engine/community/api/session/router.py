"""
Engine-agnostic standalone session router.

Uses EngineManager for default engine, falls back to factory for explicit engine param.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException

from engine.community.api.caps import check_capability
from engine.community.api.response import ApiResponse
from engine.community.api.session.schemas import CreateSessionBody
from engine.community.core.engine.capability import Capability
from engine.community.core.session.models import (
    SessionClearRequest,
    SessionCreateRequest,
    SessionDeleteRequest,
    SessionHistoryRequest,
    SessionListRequest,
    SessionUpdateRequest,
)
from engine.community.shared.utils import decode_session_key

log = logging.getLogger("web-sessions")

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _get_session_api(engine: Optional[str] = None):
    """Return the SessionService for the given engine."""
    log.info(f"[new-arch] _get_session_api called, engine={engine}")
    from engine.community.manager import EngineManager
    manager = EngineManager.get_instance()

    if engine is not None and engine != manager.engine:
        raise ValueError(
            f"Unsupported engine type: {engine}. Only '{manager.engine}' is supported."
        )
    return manager.session


def _fmt_dt(dt) -> str:
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt) if dt else ""


def _sanitize_surrogates(obj):
    # Pydantic's JSON serializer rejects unpaired UTF-16 surrogates (e.g. a
    # truncated emoji \ud83d) that can sneak in from upstream payloads, even
    # though json.loads accepts them. Round-trip through surrogatepass/replace
    # to drop the bad halves before serialization.
    if isinstance(obj, str):
        return obj.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")
    if isinstance(obj, dict):
        return {k: _sanitize_surrogates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_surrogates(item) for item in obj]
    return obj


def _build_message_dict(msg) -> Dict:
    result = {
        "id": msg.id,
        "session_id": msg.session_id,
        "role": msg.role,
        "content": msg.content,
        "metadata": msg.metadata or {},
        "gmt_created": _fmt_dt(msg.created_at),
    }
    if msg.history_meta:
        result["history_meta"] = msg.history_meta
    return result


def _session_to_dict(session) -> Dict:
    result = {
        "id": session.id,
        "title": session.title or session.id,
        "user_id": session.user_id,
        "agent_id": session.agent_id,
        "model": session.model,
        "permission_mode": session.permission_mode,
        "cwd": session.cwd,
        "gmt_created": _fmt_dt(session.created_at),
        "gmt_modified": _fmt_dt(session.updated_at),
        "message_count": session.message_count,
    }
    if session.runtime:
        result["runtime"] = session.runtime
    if session.last_message:
        result["last_message"] = _build_message_dict(session.last_message)
    if session.ext_info:
        result["ext_info"] = session.ext_info
    return _sanitize_surrogates(result)


def _message_to_dict(msg) -> Dict:
    return _sanitize_surrogates(_build_message_dict(msg))


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("")
async def list_sessions(
    user_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    session_key: str | None = None,
    limit: int = 20,
    offset: int = 0,
    engine: Optional[str] = None,
) -> ApiResponse:
    log.info(f"[list_sessions] 收到请求: user_id={user_id}, agent_id={agent_id}, limit={limit}, offset={offset}, engine={engine}")
    warning = check_capability(Capability.SESSION_LIST)
    try:
        api = _get_session_api(engine)
        sessions = await api.list(SessionListRequest(
            user_id=user_id,
            agent_id=agent_id,
            session_key=session_key,
            limit=limit,
            offset=offset,
        ))
        log.info(f"[list_sessions] 查询完成, 返回 {len(sessions)} 条会话")
        return ApiResponse(
            success=True,
            data=[_session_to_dict(s) for s in sessions],
            warning=warning,
        )
    except Exception as e:
        log.error(f"[list_sessions] 执行异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("")
async def create_session(body: CreateSessionBody) -> ApiResponse:
    warning = check_capability(Capability.SESSION_CREATE)
    try:
        api = _get_session_api(body.engine)
        log.info(f"[create_session] 收到请求: title={body.title}, user_id={body.user_id}, agent_id={body.agent_id}, model={body.model}, runtime={body.runtime}, engine={body.engine}, uuid={body.uuid}")
        session = await api.create(SessionCreateRequest(
            title=body.title, user_id=body.user_id or "default", agent_id=body.agent_id, model=body.model,
            runtime=body.runtime,
            uuid=body.uuid,
            extInfo=body.extInfo,
            payload=body.payload,
        ))
        log.info(f"[create_session] 创建成功: session_id={session.id}")
        return ApiResponse(success=True, data=_session_to_dict(session), warning=warning)
    except Exception as e:
        log.error(f"[create_session] 执行异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{session_id}")
async def get_session(session_id: str, engine: Optional[str] = None) -> ApiResponse:
    log.info(f"[get_session] 收到请求: session_id={session_id}, engine={engine}")
    # Uses the underlying list() plugin method to find one session; SESSION_LIST
    # is the matching capability.
    warning = check_capability(Capability.SESSION_LIST)
    decoded_id = decode_session_key(session_id)
    if decoded_id != session_id:
        log.info(f"[get_session] session_id 解码: {session_id} -> {decoded_id}")
    session_id = decoded_id
    try:
        api = _get_session_api(engine)
        sessions = await api.list(SessionListRequest(limit=100, offset=0))
        log.info(f"[get_session] 列表查询返回 {len(sessions)} 条, 正在匹配 session_id={session_id}")
        session = next(
            (
                s
                for s in sessions
                if s.id == session_id
            ),
            None,
        )
        if not session:
            log.warning(f"[get_session] 未找到会话: session_id={session_id}")
            raise HTTPException(status_code=404, detail="Session not found")
        log.info(f"[get_session] 找到会话: session_id={session.id}, title={session.title}")
        return ApiResponse(success=True, data=_session_to_dict(session), warning=warning)
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"[get_session] 执行异常: session_id={session_id}, error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{session_id}")
async def delete_session(session_id: str, force: bool = False, engine: Optional[str] = None) -> ApiResponse:
    log.info(f"[delete_session] 收到请求: session_id={session_id}, force={force}, engine={engine}")
    warning = check_capability(Capability.SESSION_DELETE)
    session_id = decode_session_key(session_id)
    try:
        api = _get_session_api(engine)
        ok = await api.delete(SessionDeleteRequest(session_id=session_id, force=force))
        if not ok:
            log.warning(f"[delete_session] 删除失败或会话不存在: session_id={session_id}")
            raise HTTPException(status_code=404, detail="Session not found or delete failed")
        log.info(f"[delete_session] 删除成功: session_id={session_id}")
        return ApiResponse(success=True, message="Session deleted", warning=warning)
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"[delete_session] 执行异常: session_id={session_id}, error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    limit: Optional[int] = None,
    offset: int = 0,
    engine: Optional[str] = None,
) -> ApiResponse:
    log.info(f"[get_session_messages] 收到请求: session_id={session_id}, limit={limit}, offset={offset}, engine={engine}")
    warning = check_capability(Capability.SESSION_HISTORY)
    session_id = decode_session_key(session_id)
    try:
        api = _get_session_api(engine)
        result = await api.get_history(SessionHistoryRequest(
            session_id=session_id, limit=limit, offset=offset,
        ))
        log.info(f"[get_session_messages] 查询完成: session_id={session_id}, 返回 {len(result.messages)} 条消息")
        return ApiResponse(
            success=True,
            data=[_message_to_dict(m) for m in result.messages],
            total=result.total,
            warning=warning,
        )
    except Exception as e:
        log.error(f"[get_session_messages] 执行异常: session_id={session_id}, error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{session_id}/messages")
async def clear_session_messages(session_id: str, engine: Optional[str] = None) -> ApiResponse:
    log.info(f"[clear_session_messages] 收到请求: session_id={session_id}, engine={engine}")
    # Clearing the message history is a destructive session write — gate on
    # SESSION_DELETE since there's no dedicated "clear messages" capability.
    warning = check_capability(Capability.SESSION_DELETE)
    session_id = decode_session_key(session_id)
    try:
        api = _get_session_api(engine)
        await api.clear(SessionClearRequest(session_id=session_id))
        log.info(f"[clear_session_messages] 清除成功: session_id={session_id}")
        return ApiResponse(success=True, message="Messages cleared", warning=warning)
    except Exception as e:
        log.error(f"[clear_session_messages] 执行异常: session_id={session_id}, error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{session_id}/update")
async def update_session(
    session_id: str,
    title: Optional[str] = None,
    model: Optional[str] = None,
    runtime: Optional[str] = None,
    cwd: Optional[str] = None,
    user_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    permission_mode: Optional[str] = None,
    dima_url: Optional[str] = None,
    dima_space_id: Optional[str] = None,
    dima_item_id: Optional[str] = None,
    engine: Optional[str] = None,
) -> ApiResponse:
    log.info(f"[update_session] 收到请求: session_id={session_id}, title={title}, model={model}, runtime={runtime}, permission_mode={permission_mode}, dima_item_id={dima_item_id}, engine={engine}")
    warning = check_capability(Capability.SESSION_UPDATE)
    session_id = decode_session_key(session_id)

    try:
        api = _get_session_api(engine)
        session = await api.update(SessionUpdateRequest(
            session_id=session_id,
            title=title,
            model=model,
            runtime=runtime,
            cwd=cwd,
            user_id=user_id,
            agent_id=agent_id,
            permission_mode=permission_mode,
            dima_url=dima_url,
            dima_space_id=dima_space_id,
            dima_item_id=dima_item_id,
        ))
        log.info(f"[update_session] 更新成功: session_id={session.id}")
        return ApiResponse(success=True, data=_session_to_dict(session), warning=warning)
    except Exception as e:
        log.error(f"[update_session] 执行异常: session_id={session_id}, error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
