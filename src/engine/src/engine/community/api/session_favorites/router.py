"""Engine-neutral REST API for a user's favorite sessions."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query

from engine.community.api.caps import check_capability
from engine.community.api.response import ApiResponse
from engine.community.api.session.router import _get_session_api, _session_to_dict
from engine.community.core.engine.capability import Capability
from engine.community.core.session.models import SessionListRequest
from engine.community.core.session_favorite import get_session_favorite_repository
from engine.community.shared.utils import decode_session_key

log = logging.getLogger("session-favorites")

router = APIRouter(prefix="/api/session-favorites", tags=["session-favorites"])

# Engine SessionService implementations paginate in memory. Fetching this bounded
# set lets the adapter apply pagination after it filters by the SQLite metadata.
_FAVORITE_SESSION_SCAN_LIMIT = 10_000


def _require_user_id(user_id: str | None) -> str:
    if user_id is None or not user_id.strip():
        raise HTTPException(status_code=422, detail="user_id is required")
    return user_id.strip()


@router.get("")
async def list_session_favorites(
    user_id: str | None = Query(None, description="Current user ID"),
    agent_id: str | None = Query(None, description="Agent ID"),
    limit: int = Query(20, description="Maximum sessions to return"),
    offset: int = Query(0, description="Favorite sessions offset"),
    engine: str | None = Query(None, description="Active engine type"),
) -> ApiResponse:
    """Return favorited sessions in the same shape as ``GET /api/sessions``."""
    resolved_user_id = _require_user_id(user_id)
    warning = check_capability(Capability.SESSION_LIST)
    try:
        repository = get_session_favorite_repository()
        favorite_session_ids = set(
            await asyncio.to_thread(repository.list_session_ids, resolved_user_id)
        )
        if not favorite_session_ids:
            return ApiResponse(success=True, data=[], warning=warning)

        api = _get_session_api(engine)
        sessions = await api.list(
            SessionListRequest(
                user_id=resolved_user_id,
                agent_id=agent_id,
                limit=_FAVORITE_SESSION_SCAN_LIMIT,
                offset=0,
            )
        )
        favorite_sessions = [
            session for session in sessions if session.id in favorite_session_ids
        ]
        paged_sessions = favorite_sessions[offset:offset + limit]
        return ApiResponse(
            success=True,
            data=[_session_to_dict(session) for session in paged_sessions],
            warning=warning,
        )
    except (ConnectionError, TimeoutError) as exc:
        log.error(
            "Session favorites gateway unavailable: %s", exc, exc_info=True
        )
        raise HTTPException(status_code=503, detail="Session gateway unavailable") from exc
    except Exception as exc:
        log.error("Failed to list session favorites: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list session favorites") from exc


@router.put("/{session_id}")
async def add_session_favorite(
    session_id: str,
    user_id: str | None = Query(None, description="Current user ID"),
) -> ApiResponse:
    """Mark one session as a favorite for the current user."""
    resolved_user_id = _require_user_id(user_id)
    decoded_session_id = decode_session_key(session_id)
    try:
        repository = get_session_favorite_repository()
        await asyncio.to_thread(repository.add, resolved_user_id, decoded_session_id)
        return ApiResponse(success=True, message="Session favorited")
    except Exception as exc:
        log.error("Failed to favorite session: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to favorite session") from exc


@router.delete("/{session_id}")
async def remove_session_favorite(
    session_id: str,
    user_id: str | None = Query(None, description="Current user ID"),
) -> ApiResponse:
    """Remove one user's favorite marker without deleting the session."""
    resolved_user_id = _require_user_id(user_id)
    decoded_session_id = decode_session_key(session_id)
    try:
        repository = get_session_favorite_repository()
        await asyncio.to_thread(repository.remove, resolved_user_id, decoded_session_id)
        return ApiResponse(success=True, message="Session unfavorited")
    except Exception as exc:
        log.error("Failed to remove session favorite: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to remove session favorite") from exc
