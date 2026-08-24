"""Independent service entry points for the Bot Logs OpenAPI."""

from datetime import datetime, timedelta, timezone

from injector import inject

from agentclaw.community.core.bot_chat.errors import (
    InvalidBotLogQueryError,
    SessionNotFoundError,
)
from agentclaw.community.core.repository.implementations.chat.open import (
    OpenBotChatRepository,
)
from agentclaw.community.core.bot_chat.schemas import (
    ConversationDetail,
    SessionListResponse,
)

_OPEN_PAGE_SIZE = 100
_USER_BOT_TIME_RANGE_HOURS = 72


class OpenBotChatService:
    """Serve only the independently secured Bot Logs OpenAPI contract."""

    @inject
    def __init__(self, repository: OpenBotChatRepository) -> None:
        self._repository = repository

    def _require_group_viewer(
        self,
        *,
        group_id: str | None,
        bot_id: str | None,
        user_id: str | None,
        owner_id: str | None,
        not_found_message: str,
        allow_legacy_group: bool,
    ) -> None:
        """Validate an optional product-Bot viewer against BCS membership."""
        has_viewer_context = any(
            value is not None for value in (bot_id, user_id, owner_id)
        )
        if not has_viewer_context:
            if group_id is not None and not allow_legacy_group:
                raise InvalidBotLogQueryError(
                    "group_id, bot_id and user_id must be provided together"
                )
            return
        if group_id is None or bot_id is None or user_id is None:
            raise InvalidBotLogQueryError(
                "group_id, bot_id and user_id must be provided together"
            )
        bot_uuid = f"{bot_id}:{owner_id or user_id}"
        if not self._repository.is_bot_in_group(group_id, bot_uuid):
            raise SessionNotFoundError(not_found_message)

    async def list_open_sessions(
        self,
        session_key: str | None = None,
        biz_scene: str | None = None,
        biz_task_id: str | None = None,
        group_id: str | None = None,
        bot_id: str | None = None,
        user_id: str | None = None,
        owner_id: str | None = None,
        page: int = 1,
        limit: int = _OPEN_PAGE_SIZE,
    ) -> SessionListResponse:
        """List traces by one exact public identifier without owner filtering."""
        session_key = (session_key.strip() or None) if session_key is not None else None
        biz_scene = (biz_scene.strip() or None) if biz_scene is not None else None
        biz_task_id = (biz_task_id.strip() or None) if biz_task_id is not None else None
        group_id = (group_id.strip() or None) if group_id is not None else None
        bot_id = (bot_id.strip() or None) if bot_id is not None else None
        user_id = (user_id.strip() or None) if user_id is not None else None
        owner_id = (owner_id.strip() or None) if owner_id is not None else None

        session_mode = session_key is not None
        task_mode = biz_scene is not None or biz_task_id is not None
        group_mode = group_id is not None
        if sum((session_mode, task_mode, group_mode)) != 1:
            raise InvalidBotLogQueryError(
                "provide exactly one of session_key, biz_scene+biz_task_id, or group_id"
            )
        if task_mode and (biz_scene is None or biz_task_id is None):
            raise InvalidBotLogQueryError(
                "biz_scene and biz_task_id must be provided together"
            )
        self._require_group_viewer(
            group_id=group_id,
            bot_id=bot_id,
            user_id=user_id,
            owner_id=owner_id,
            not_found_message="group not found or not accessible",
            allow_legacy_group=True,
        )

        return self._repository.list_scope_traces(
            from_ms=0,
            to_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
            page=max(1, page),
            limit=min(max(1, limit), _OPEN_PAGE_SIZE),
            session_key=session_key,
            biz_scene=biz_scene,
            biz_task_id=biz_task_id,
            group_id=group_id,
        )

    async def get_open_session(
        self,
        trace_id: str,
        bot_id: str | None = None,
        group_id: str | None = None,
        user_id: str | None = None,
        owner_id: str | None = None,
    ) -> ConversationDetail:
        """Get an exact trace detail without applying owner access filters."""
        trace_id = trace_id.strip()
        if not trace_id:
            raise InvalidBotLogQueryError("trace_id must not be empty")
        bot_id = (bot_id.strip() or None) if bot_id is not None else None
        group_id = (group_id.strip() or None) if group_id is not None else None
        user_id = (user_id.strip() or None) if user_id is not None else None
        owner_id = (owner_id.strip() or None) if owner_id is not None else None
        self._require_group_viewer(
            group_id=group_id,
            bot_id=bot_id,
            user_id=user_id,
            owner_id=owner_id,
            not_found_message="trace not found or not accessible",
            allow_legacy_group=False,
        )
        detail = self._repository.get_trace_detail(trace_id)
        if group_id is not None and detail.group_id != group_id:
            raise SessionNotFoundError("trace not found or not accessible")
        return detail

    async def list_open_user_bot_traces(
        self,
        user_id: str,
        bot_id: str,
        page: int = 1,
        limit: int = _OPEN_PAGE_SIZE,
    ) -> SessionListResponse:
        """List the recent traces for one explicit user-and-Bot pair.

        This is a query boundary, not caller authorization: the open Gateway
        surface permits an authenticated User+App caller to name the pair.
        """
        user_id = user_id.strip()
        bot_id = bot_id.strip()
        if not user_id:
            raise InvalidBotLogQueryError("user_id must not be empty")
        if not bot_id:
            raise InvalidBotLogQueryError("bot_id must not be empty")

        now = datetime.now(timezone.utc)
        from_date = now - timedelta(hours=_USER_BOT_TIME_RANGE_HOURS)
        return self._repository.list_user_bot_traces(
            user_id=user_id,
            bot_id=bot_id,
            from_ms=int(from_date.timestamp() * 1000),
            to_ms=int(now.timestamp() * 1000),
            page=max(1, page),
            limit=min(max(1, limit), _OPEN_PAGE_SIZE),
        )
