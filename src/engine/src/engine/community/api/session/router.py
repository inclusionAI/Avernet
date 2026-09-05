"""
Engine-agnostic standalone session router.

Uses EngineManager for default engine, falls back to factory for explicit engine param.
"""
from __future__ import annotations

import json
import asyncio
import logging
import time
from datetime import datetime
from typing import Dict, Literal, Optional

from fastapi import APIRouter, HTTPException, Request

from engine.community.api.caps import check_capability
from engine.community.api.response import ApiResponse
from engine.community.api.session.schemas import CreateSessionBody
from engine.community.core.engine.capability import Capability
from engine.community.core.engine.exceptions import SessionActorError
from engine.community.core.engine.context import (
    AUTHENTICATED_PRINCIPAL_SCOPE_KEY,
    AuthContext,
    AuthenticatedPrincipal,
)
from engine.community.core.session.models import (
    SessionClearRequest,
    SessionCreateRequest,
    SessionDeleteRequest,
    SessionHistoryRequest,
    SessionListRequest,
    SessionUpdateRequest,
)
from engine.community.core.aicoding.workspace_service import WorkspaceService
from engine.community.core.session_favorite import get_session_favorite_repository
from engine.community.shared.utils import (
    decode_session_key,
    managed_session_keys_equal,
)

log = logging.getLogger("web-sessions")

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _auth_context_from_request(request: Request) -> AuthContext:
    principal = request.scope.get(AUTHENTICATED_PRINCIPAL_SCOPE_KEY)
    if not isinstance(principal, AuthenticatedPrincipal):
        return AuthContext()
    return AuthContext(token=principal.token, user_id=principal.user_id)


def require_session_actor(
    request: Request, requested_user_id: str | None,
    *, operation: Literal["list", "create"], started_at: float | None = None,
) -> AuthContext:
    """Resolve the trusted actor and reject absent or mismatched identities."""
    auth = _auth_context_from_request(request)
    actor_user_id = auth.user_id
    if not isinstance(actor_user_id, str) or not actor_user_id.strip():
        log.warning(
            "event=engine.sessions.%s.denied operation=%s reason=missing_actor "
            "status=401 requested_user_present=%s duration_ms=%.3f",
            operation,
            operation,
            requested_user_id is not None,
            (time.monotonic() - started_at) * 1000
            if started_at is not None else 0.0,
        )
        raise HTTPException(status_code=401, detail="Session authentication required")
    if requested_user_id is not None and requested_user_id != actor_user_id:
        log.warning(
            "event=engine.sessions.%s.denied operation=%s "
            "reason=identity_mismatch status=403 requested_user_present=true "
            "duration_ms=%.3f",
            operation,
            operation,
            (time.monotonic() - started_at) * 1000
            if started_at is not None else 0.0,
        )
        raise HTTPException(status_code=403, detail="Forbidden")
    return auth


def _session_actor_http_error(
    operation: Literal["list", "create"],
    error: SessionActorError | PermissionError,
    started_at: float,
) -> HTTPException:
    if isinstance(error, SessionActorError):
        reason = error.reason
        status_code = error.status_code
    else:
        reason = "actor_unavailable"
        status_code = 401
    log.warning(
        "event=engine.sessions.%s.denied operation=%s reason=%s status=%s "
        "duration_ms=%.3f",
        operation,
        operation,
        reason,
        status_code,
        (time.monotonic() - started_at) * 1000,
    )
    detail = "Forbidden" if status_code == 403 else "Session authentication required"
    return HTTPException(status_code=status_code, detail=detail)


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


def _validated_cwd(cwd: Optional[str]) -> Optional[str]:
    """Canonicalise a caller-supplied ``cwd`` and hold it to the allowed roots.

    ``cwd`` reaches the ``claude_code`` relay as the directory a session is bound
    to, and that session runs with ``permissionMode: bypassPermissions``. The
    relay's own ``validateDirPath`` only asks whether the path is absolute and
    is an existing directory — ``/etc`` satisfies both — so without this gate a
    caller of the create/update routes could bind the agent anywhere on the
    device it can read. Every other caller-supplied ``cwd`` on this engine is
    already held to ``AICODING_CWD_ALLOW_ROOTS`` /
    ``CONTAINER_WORKSPACE_BASE``; these two routes are brought under the same
    rule rather than being given a weaker one.

    Prefix-and-format only (:meth:`_validate_cwd_prefix`), not
    :meth:`validate_cwd`: existence is the relay's to enforce at bind time, and
    requiring it here would reject a directory the engine process cannot stat
    but the relay can. ``ValueError`` is the caller's error — the routes turn it
    into a 400 *before* their ``except Exception`` can bury it as a 500.
    """
    if not cwd:
        return cwd
    return WorkspaceService._validate_cwd_prefix(cwd)


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
    request: Request,
    user_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    session_key: str | None = None,
    source: Literal["all_but_others"] | None = None,
    limit: int = 20,
    offset: int = 0,
    engine: Optional[str] = None,
) -> ApiResponse:
    started_at = time.monotonic()
    legacy_unauthenticated = source is None and user_id is None
    auth = None if legacy_unauthenticated else require_session_actor(
        request, user_id, operation="list", started_at=started_at
    )
    actor_user_id = auth.user_id if auth is not None else None
    actor_present = bool(
        auth is not None and isinstance(auth.user_id, str) and auth.user_id.strip()
    )
    log.info(
        "event=engine.sessions.list.request system=engine direction=inbound "
        "operation=sessions.list method=GET route=/api/sessions "
        "source=%s actor_present=%s requested_user_present=%s offset=%s limit=%s "
        "duration_ms=%.3f",
        source,
        str(actor_present).lower(),
        user_id is not None,
        offset,
        limit,
        (time.monotonic() - started_at) * 1000,
    )
    warning = check_capability(Capability.SESSION_LIST)
    try:
        api = _get_session_api(engine)
        session_request = SessionListRequest(
            user_id=actor_user_id,
            agent_id=agent_id,
            session_key=session_key,
            source=source,
            limit=limit,
            offset=offset,
        )
        if auth is None:
            sessions = await api.list(session_request)
        else:
            sessions = await api.list(session_request, auth=auth)
        log.info(
            "event=engine.sessions.list.success operation=sessions.list status=200 "
            "returned_count=%s source=%s actor_present=%s duration_ms=%.3f",
            len(sessions),
            source,
            str(actor_present).lower(),
            (time.monotonic() - started_at) * 1000,
        )
        return ApiResponse(
            success=True,
            data=[_session_to_dict(s) for s in sessions],
            warning=warning,
        )
    except (ConnectionError, TimeoutError) as e:
        log.error(
            "event=engine.sessions.list.failure operation=sessions.list status=503 "
            "reason=gateway_unavailable error_type=%s duration_ms=%.3f",
            type(e).__name__,
            (time.monotonic() - started_at) * 1000,
        )
        raise HTTPException(status_code=503, detail="Session gateway unavailable") from e
    except SessionActorError as e:
        raise _session_actor_http_error("list", e, started_at) from e
    except PermissionError as e:
        raise _session_actor_http_error("list", e, started_at) from e
    except Exception as e:
        log.error(
            "event=engine.sessions.list.failure operation=sessions.list status=500 "
            "reason=unexpected error_type=%s duration_ms=%.3f",
            type(e).__name__,
            (time.monotonic() - started_at) * 1000,
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("")
async def create_session(request: Request, body: CreateSessionBody) -> ApiResponse:
    started_at = time.monotonic()
    auth = require_session_actor(
        request, body.user_id, operation="create", started_at=started_at
    )
    actor_user_id = auth.user_id
    log.info(
        "event=engine.sessions.create.request system=engine direction=inbound "
        "operation=sessions.create method=POST route=/api/sessions "
        "actor_present=%s requested_user_present=%s agent_present=%s duration_ms=%.3f",
        str(auth.user_id is not None).lower(),
        body.user_id is not None,
        body.agent_id is not None,
        (time.monotonic() - started_at) * 1000,
    )
    warning = check_capability(Capability.SESSION_CREATE)
    # Outside the try: this route's ``except Exception`` maps everything to 500,
    # which would turn a rejected cwd into a server error instead of a 400.
    try:
        cwd = _validated_cwd(body.cwd)
    except ValueError as e:
        log.warning("[create_session] cwd 拒绝: %s", e)
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        api = _get_session_api(body.engine)
        session = await api.create(SessionCreateRequest(
            title=body.title, user_id=actor_user_id, agent_id=body.agent_id, model=body.model,
            runtime=body.runtime,
            cwd=cwd,
            uuid=body.uuid,
            extInfo=body.extInfo,
            payload=body.payload,
        ), auth=auth)
        log.info(
            "event=engine.sessions.create.success operation=sessions.create status=200 "
            "actor_present=%s generated_key_format=%s duration_ms=%.3f",
            str(auth.user_id is not None).lower(),
            "agent_user" if body.agent_id else "session_user",
            (time.monotonic() - started_at) * 1000,
        )
        return ApiResponse(success=True, data=_session_to_dict(session), warning=warning)
    except SessionActorError as e:
        raise _session_actor_http_error("create", e, started_at) from e
    except PermissionError as e:
        raise _session_actor_http_error("create", e, started_at) from e
    except Exception as e:
        log.error(
            "event=engine.sessions.create.failure operation=sessions.create status=500 "
            "reason=unexpected error_type=%s duration_ms=%.3f",
            type(e).__name__,
            (time.monotonic() - started_at) * 1000,
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{session_id}")
async def get_session(session_id: str, engine: Optional[str] = None) -> ApiResponse:
    log.info(f"[get_session] 收到请求: session_id={session_id}, engine={engine}")
    # Uses the underlying list() plugin method to find one session; SESSION_LIST
    # is the matching capability. The plugin filters a session_key before
    # pagination, so an inactive conversation cannot be mistaken for missing
    # merely because it falls outside the first page.
    warning = check_capability(Capability.SESSION_LIST)
    decoded_id = decode_session_key(session_id)
    if decoded_id != session_id:
        log.info(f"[get_session] session_id 解码: {session_id} -> {decoded_id}")
    session_id = decoded_id
    try:
        api = _get_session_api(engine)
        # Engines that support exact lookup filter before pagination. Engines that
        # do not yet support it retain the previous first-100 fallback behavior.
        sessions = await api.list(
            SessionListRequest(session_key=session_id, limit=100, offset=0)
        )
        log.info(f"[get_session] 列表查询返回 {len(sessions)} 条, 正在匹配 session_id={session_id}")
        session = next(
            (
                s
                for s in sessions
                if managed_session_keys_equal(s.id, session_id)
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
    except (ConnectionError, TimeoutError) as error:
        log.error(f"[get_session] gateway unavailable: {error}", exc_info=True)
        raise HTTPException(status_code=503, detail="Session gateway unavailable") from error
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
        try:
            await asyncio.to_thread(get_session_favorite_repository().remove_session, session_id)
        except Exception as cleanup_error:
            # The engine has already deleted the session. Do not misreport that
            # successful destructive operation because local metadata cleanup failed.
            log.warning(
                "[delete_session] 收藏记录清理失败: session_id=%s, error=%s",
                session_id,
                cleanup_error,
            )
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
    ext_info: Optional[str] = None,
    engine: Optional[str] = None,
) -> ApiResponse:
    # ext_info 为可选 JSON 字符串，引擎实现自行解析其中命名空间化的扩展
    # 字段（OSS 层不感知具体内部词汇）。
    parsed_ext_info = None
    if ext_info:
        try:
            parsed_ext_info = json.loads(ext_info)
        except (json.JSONDecodeError, TypeError):
            raise HTTPException(status_code=400, detail="ext_info must be valid JSON")
    log.info(f"[update_session] 收到请求: session_id={session_id}, title={title}, model={model}, runtime={runtime}, cwd={cwd}, permission_mode={permission_mode}, engine={engine}")
    warning = check_capability(Capability.SESSION_UPDATE)
    session_id = decode_session_key(session_id)
    # Same gate as create, and for the same reason: the public create route
    # reaches this one for a friend bot, whose requested fields are applied
    # through /update rather than the create body.
    try:
        cwd = _validated_cwd(cwd)
    except ValueError as e:
        log.warning("[update_session] cwd 拒绝: %s", e)
        raise HTTPException(status_code=400, detail=str(e)) from e

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
            ext_info=parsed_ext_info,
        ))
        log.info(f"[update_session] 更新成功: session_id={session.id}")
        return ApiResponse(success=True, data=_session_to_dict(session), warning=warning)
    except Exception as e:
        log.error(f"[update_session] 执行异常: session_id={session_id}, error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
