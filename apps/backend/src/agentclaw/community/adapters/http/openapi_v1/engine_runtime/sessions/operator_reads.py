"""Paged engine reads shared by authorized HTTP and Evolution callers.

Callers must resolve access and supply BotFacts before invoking these helpers;
these functions shape transport responses and do not authorize a Bot or Run.
"""

from typing import Any

from fastapi import Request

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope, PageParams
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import RuntimeStage
from agentclaw.community.adapters.http.openapi_v1.responses import page as page_envelope
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.core.engine_runtime.models import BotFacts
from agentclaw.community.core.engine_runtime.session_key import SessionKeyCodecRegistry

from .schemas import MessagePage, SessionPage
from .schemas_helpers import (
    _as_list, _history_page, _history_window, _map_message, _map_session,
    _page, _require_within_depth,
)


async def list_operator_sessions(
    request: Request, relay: EngineRuntimeRelayProtocol, bot_id: str, owner_id: str,
    facts: BotFacts, stage: RuntimeStage, page: PageParams, params: dict[str, Any],
    device_uuid: str | None = None,
) -> Envelope[SessionPage]:
    """Read and shape one page on an already-authorized operator runtime."""
    result = await relay.call(
        bot_id=bot_id,
        owner_id=owner_id,
        facts=facts,
        stage=stage.value,
        **({"device_uuid": device_uuid} if device_uuid is not None else {}),
        method="GET",
        path="/api/sessions",
        params=params,
    )
    mapped = [_map_session(d, engine_type=facts.active_engine) for d in _as_list(result.data)]
    # The session list reports no total; derive it from the window.
    total, items = _page(mapped, page, reported=result.total)
    return page_envelope(total, items, request)


async def list_operator_messages(
    request: Request, relay: EngineRuntimeRelayProtocol, codecs: SessionKeyCodecRegistry,
    bot_id: str, owner_id: str, facts: BotFacts, stage: RuntimeStage, session_id: str,
    page: PageParams, device_uuid: str | None = None,
) -> Envelope[MessagePage]:
    """Read and shape an already-authorized operator's bounded history page."""
    _require_within_depth(page)
    result = await relay.call(
        bot_id=bot_id,
        owner_id=owner_id,
        facts=facts,
        stage=stage.value,
        **({"device_uuid": device_uuid} if device_uuid is not None else {}),
        method="GET",
        path=f"/api/sessions/{codecs.resolve(facts.active_engine).encode(session_id)}/messages",
        # The history route tail-limits rather than paginating, so the offset is
        # applied here instead of being sent. See ``_history_window``.
        params=_history_window(page),
    )
    mapped = [_map_message(d, session_id) for d in _as_list(result.data)]
    # The engine's envelope carries a total field, so it is used when filled —
    # but both bundled adapters return None for history, so this is normally the
    # derived value. See ``_history_page``.
    total, items = _history_page(mapped, page, reported=result.total)
    return page_envelope(total, items, request)
