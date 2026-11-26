"""Session collection read adapter, including opt-in source windows."""

from typing import Annotated, Any, Literal

from fastapi import HTTPException, Query, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import BotIdPath, Envelope, PageParamsDep
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import RuntimeStage
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.friend_chat import FriendUserIdQuery
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import DeviceUuidQuery, OwnerIdDep, StageQuery
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import envelope, envelope_errors, page as page_envelope
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.api.expert_chat_service import ExpertChatServiceProtocol
from agentclaw.community.api.human_bot_friendship_service import HumanBotFriendshipServiceProtocol
from agentclaw.community.api.session_source_listing import SessionSourceListingProtocol
from agentclaw.community.core.engine_runtime.errors import EngineDeviceNotReadyError, EngineResourceNotFoundError
from agentclaw.community.core.expert_chat.errors import BotNotFoundError as ExpertBotNotFoundError, ConnectionError as ExpertConnectionError
from agentclaw.community.di import Injected
from agentclaw.community.utils.runtime_list_timing import mark

from .backend import resolve_session_backend
from .schemas import SessionPage
from .schemas_helpers import _as_list, _map_session, _page, _window

def _raise_expert_error(error: Exception) -> None:
    if isinstance(error, ExpertBotNotFoundError):
        raise EngineResourceNotFoundError("friend session not found") from error
    if isinstance(error, ExpertConnectionError):
        raise EngineDeviceNotReadyError("friend bot runtime is not ready") from error
    raise error


@envelope_errors
async def list_sessions(
    bot_id: BotIdPath,
    page: PageParamsDep,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    device_uuid: DeviceUuidQuery = None,
    f_user_id: FriendUserIdQuery = None,
    agent_id: Annotated[str | None, Query(description="Only sessions belonging to this agent.")] = None,
    session_key: Annotated[str | None, Query(description="Only the session with this key. Pass the value verbatim; no encoding is required.")] = None,
    source: Annotated[
        Literal["mine", "others"] | None,
        Query(description="Opt-in caller-relative filtering for owners and collaborators. Omit to preserve legacy behavior. Uses bounded cursor windows, not page-number pagination. Unknown creators are retained in mine and excluded from others. Cannot combine with f_user_id."),
    ] = None,
    source_cursor: Annotated[
        str | None, Query(min_length=1, max_length=512, description="Continue the source window with the same Bot/caller/stage/filters/page_size. Keep page=1. Follow next_cursor even for short/empty windows. Not snapshot-consistent."),
    ] = None,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
    friendships: HumanBotFriendshipServiceProtocol = Injected(HumanBotFriendshipServiceProtocol),
    expert: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol),
    listing: SessionSourceListingProtocol = Injected(SessionSourceListingProtocol),
) -> Envelope[SessionPage]:
    """List sessions; source filtering is opt-in and does not expand access."""
    mark("handler")
    if (source_cursor is not None and source is None) or (
        source is not None and (f_user_id is not None or page.page != 1)
    ):
        raise HTTPException(422, "source requires page=1, no f_user_id; source_cursor requires source")
    facts = await resolve_session_backend(
        relay=relay, friendships=friendships, expert=expert, request=request,
        bot_id=bot_id, user_id=user_id, owner_id=owner_id, friend_user_id=f_user_id,
        stage=stage, device_uuid=device_uuid,
    )
    if source is not None:
        assert facts is not None  # f_user_id was refused above; operator gate passed.
        window = await listing.list_sessions(
            facts=facts, caller_id=user_id, stage=stage.value, source=source,
            page_size=page.page_size, cursor=source_cursor, device_uuid=device_uuid,
            agent_id=agent_id, session_key=session_key,
        )
        return envelope(SessionPage(
            source=source, total=window.total,
            items=[_map_session(row, engine_type=facts.active_engine) for row in window.items],
            has_more=window.has_more, next_cursor=window.next_cursor, scan_limited=window.scan_limited,
        ), request)
    # Legacy page-number behavior is intentionally unchanged, including its
    # owner-only source filter and the separately authorized friend backend.
    params: dict[str, Any] = _window(page)
    if facts is not None and (
        f_user_id is not None or facts.owner_id == user_id
    ):
        params.update(user_id=user_id, source="all_but_others")
    if agent_id:
        params["agent_id"] = agent_id
    if session_key:
        params["session_key"] = session_key
    if facts is None:
        try:
            friend_result = await expert.list_chat_sessions(
                user_id=f_user_id, bot_id=bot_id, owner_id=owner_id,
                session_key=session_key, limit=params["limit"], offset=params["offset"],
                iam_token=request.cookies.get("IAM_TOKEN") or None,
                bcn_friend_authorized=True, actor_user_id=user_id,
            )
        except Exception as error:
            _raise_expert_error(error)
        mapped = [_map_session(d) for d in _as_list(friend_result)]
        total, items = _page(mapped, page, reported=friend_result.get("total"))
        return page_envelope(total, items, request)
    result = await relay.call(
        bot_id=bot_id, owner_id=owner_id, facts=facts, stage=stage.value,
        **({"device_uuid": device_uuid} if device_uuid is not None else {}),
        method="GET", path="/api/sessions", params=params,
    )
    mapped = [_map_session(d, engine_type=facts.active_engine) for d in _as_list(result.data)]
    total, items = _page(mapped, page, reported=result.total)
    return page_envelope(total, items, request)
