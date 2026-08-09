"""Repository contracts owned by the ``chat`` domain.

Moved here by the ``core/repository`` consolidation. Every member is
``@abstractmethod``: an implementation that omits one fails at construction
naming the missing member, instead of raising ``AttributeError`` at the call
site. Domain imports are ``TYPE_CHECKING``-only — see the module docstring in
``core/repository/README.md`` for why that direction is load-bearing.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Dict, List, Optional, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.bot_chat.query_support import QueryScope
    from agentclaw.community.core.bot_chat.schemas import (
        ConversationDetail,
        ConversationObservation,
        ConversationSession,
        SessionListResponse,
    )
    from agentclaw.community.core.channel.models import ChannelRecord


@runtime_checkable
class ExpertChatRepository(Protocol):
    """Protocol for expert chat repository implementations.

    Implementation: a single unified ORM body at
    ``plugins.expert_chat_repository.ExpertChatRepository`` (runs on
    both the corp store and SQLite via the injected ``DatabasePlugin``).
    """

    @abstractmethod
    def add_chat_bot(self, user_id: str, bot_id: str, owner_id: str) -> Dict[str, Any]:
        """添加专家Bot到用户对话列表

        Args:
            user_id: 用户ID（使用人）
            bot_id: Bot ID
            owner_id: Bot 所有者ID

        Returns:
            创建的记录
        """
        ...

    @abstractmethod
    def remove_chat_bot(self, user_id: str, bot_id: str, owner_id: str) -> bool:
        """从用户对话列表移除专家Bot（软删除）

        Args:
            user_id: 用户ID（使用人）
            bot_id: Bot ID
            owner_id: Bot 所有者ID

        Returns:
            是否成功
        """
        ...

    @abstractmethod
    def list_chat_bots(self, user_id: str) -> List[Dict[str, str]]:
        """获取用户对话列表中的Bot信息列表

        Args:
            user_id: 用户ID

        Returns:
            包含 bot_id 和 owner_id 的字典列表
        """
        ...

    @abstractmethod
    def get_session(self, user_id: str, bot_id: str, owner_id: str) -> Optional[str]:
        """获取 user-bot 的 session_key

        Args:
            user_id: 用户ID
            bot_id: Bot ID
            owner_id: Bot 所有者ID

        Returns:
            session_key 或 None
        """
        ...

    @abstractmethod
    def save_session(self, user_id: str, bot_id: str, owner_id: str, session_key: str) -> None:
        """保存 user-bot 的 session_key

        Args:
            user_id: 用户ID
            bot_id: Bot ID
            owner_id: Bot 所有者ID
            session_key: session:uuid 格式
        """
        ...

    @abstractmethod
    def delete_session(self, user_id: str, bot_id: str, owner_id: str) -> bool:
        """删除 user-bot 的 session（只清空 session_key，不删除记录）

        Args:
            user_id: 用户ID
            bot_id: Bot ID
            owner_id: Bot 所有者ID

        Returns:
            是否成功
        """
        ...


@runtime_checkable
class ExpertChatInstanceRepository(Protocol):
    """Protocol for the ``ac_expert_chat_instance`` persistence layer.

    Separate from :class:`ExpertChatRepository` — the instance table is
    the per-caller baas container lifecycle ledger, distinct from the
    chat-session/​bot-list ledger. A single unified ORM body under
    ``plugins.expert_chat_repository.ExpertChatInstanceRepository`` runs
    on both the corp store and SQLite.

    ``ext`` is round-tripped as JSON: callers hand in plain dicts, the
    repo serializes; getters return plain dicts (``None`` absent).
    """

    @abstractmethod
    def get_instance(
        self,
        user_id: str,
        bot_id: str,
        owner_id: str,
    ) -> Optional[Dict[str, Any]]:
        """按 (bot_id, owner_id, user_id, env) 取 caller 实例记录。

        Returns:
            实例记录字典（``ext`` 已反序列化为 dict），不存在返回 None。
        """
        ...

    @abstractmethod
    def upsert_instance(
        self,
        user_id: str,
        bot_id: str,
        owner_id: str,
        status: str,
        ext: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """按唯一键 (bot_id, owner_id, user_id, env) 原子 insert-or-update。

        不存在则插入 ``status`` / ``ext``；已存在则整体覆盖 ``status`` /
        ``ext``（caller 持有实例全量 ext 时调用；局部更新走
        ``update_instance``）。返回 upsert 后的实例记录。

        Args:
            status: ``init`` / ``active`` / ``release``
            ext: 关联键 JSON（``bot_uuid`` / ``service_bot_publish_id`` /
                ``version`` / ``binding_id`` / ``baas_publish``）；None 时落空。

        Returns:
            upsert 后的实例记录字典。
        """
        ...

    @abstractmethod
    def update_instance(
        self,
        user_id: str,
        bot_id: str,
        owner_id: str,
        status: Optional[str] = None,
        ext: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """局部更新实例记录的 ``status`` 和/或 ``ext``。

        只更新非 None 的字段；``ext`` 为整体覆盖（非 merge）。记录不存在
        时为 no-op，返回 False（与 ``save_session`` 的 blind update 语义一致）。

        Returns:
            是否命中并更新了行。
        """
        ...


@runtime_checkable
class ChannelRepository(Protocol):
    """Protocol for channel repository implementations.

    Implementation: a single unified ORM body at
    ``plugins.channel_repository.ChannelRepository`` (runs on both
    the corp store and SQLite via the injected ``DatabasePlugin``).
    """

    @abstractmethod
    def insert_channel(
        self,
        *,
        type: str,
        description: str | None,
        identity_id: str,
        bind_bot_id: str,
        config: dict[str, Any],
        status: str,
        stage: str | None = None,
    ) -> int:
        """Insert a new channel record and return its ID."""
        ...

    @abstractmethod
    def get_by_type_and_identity_ids(
        self,
        *,
        type: str,
        identity_ids: list[str],
        bind_bot_id: str,
    ) -> list[ChannelRecord]:
        """Get channels by type, identity_ids and bind_bot_id (deleted=0)."""
        ...

    @abstractmethod
    def get_by_id(self, channel_id: int) -> ChannelRecord | None:
        """Get channel by id."""
        ...

    @abstractmethod
    def update_by_id(
        self,
        *,
        channel_id: int,
        type: str,
        description: str | None,
        identity_id: str,
        bind_bot_id: str,
        config: dict[str, Any],
        status: str,
        stage: str | None = None,
    ) -> None:
        """Update all fields of a channel record by id."""
        ...

    @abstractmethod
    def update_status_by_id(self, *, channel_id: int, status: str) -> None:
        """Update status of a channel record by id."""
        ...

    @abstractmethod
    def delete_by_id(self, *, channel_id: int) -> None:
        """Logical delete a channel record by id (set deleted=1)."""
        ...


@runtime_checkable
class OpenBotChatRepositoryProtocol(Protocol):
    """Reads owned exclusively by the Bot Chat OpenAPI surface."""

    @abstractmethod
    def list_scope_traces(
        self,
        *,
        from_ms: int,
        to_ms: int,
        page: int,
        limit: int,
        session_key: str | None = None,
        biz_scene: str | None = None,
        biz_task_id: str | None = None,
        group_id: str | None = None,
    ) -> SessionListResponse:
        """List one exact Session, Task, or Group scope without product policy."""
        ...

    @abstractmethod
    def get_trace_detail(self, trace_id: str) -> ConversationDetail:
        """Return one exact Trace and observations without product authorization."""
        ...

    @abstractmethod
    def list_user_bot_traces(
        self,
        *,
        user_id: str,
        bot_id: str,
        from_ms: int,
        to_ms: int,
        page: int,
        limit: int,
    ) -> SessionListResponse:
        """Return one atomic page for the exact user-and-Bot pair."""
        ...


@runtime_checkable
class BotChatDbRepositoryProtocol(Protocol):
    """Product-facing bot-chat queries and OTLP ingestion."""

    @abstractmethod
    def owns_bot(self, owner_id: str, bot_id: str) -> bool:
        """Check if owner owns the specified bot via ac_bots table."""
        ...

    @abstractmethod
    def is_bot_owner(self, owner_id: str, bot_id: str) -> bool:
        """Check if owner_id is the owner of bot_id via ac_bots table."""
        ...

    @abstractmethod
    def is_bot_collaborator(self, user_id: str, bot_id: str) -> bool:
        """Check if user_id is a collaborator of bot_id via ac_bot_collaborator."""
        ...

    @abstractmethod
    def has_bot_access(self, user_id: str, bot_id: str) -> bool:
        """Check if user_id is either owner or collaborator of bot_id."""
        ...

    @abstractmethod
    def enrich_labels(
        self,
        rows: list[Any],
        preferred_biz_scene: str | None = None,
        preferred_biz_task_id: str | None = None,
    ) -> None:
        """Batch-fill display labels for one final response page."""
        ...

    @abstractmethod
    def list_traces(
        self,
        owner_id: str | None,
        from_ms: int,
        to_ms: int,
        page: int,
        limit: int,
        bot_id: str | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
        session_key: str | None = None,
        query: str | None = None,
        biz_scene: str | None = None,
        biz_task_id: str | None = None,
        group_id: str | None = None,
        match_mode: str = "exact",
        include_output_match: bool = False,
        query_scope: QueryScope = ...,   # default: QueryScope.OWNER
    ) -> tuple[list[ConversationSession], int]:
        """List traces from DB with pagination."""
        ...

    @abstractmethod
    def list_ocb_traces(
        self,
        owner_id: str | None,
        from_ms: int,
        to_ms: int,
        page: int,
        limit: int,
        bot_id: str | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
        session_key: str | None = None,
        query: str | None = None,
        biz_scene: str | None = None,
        biz_task_id: str | None = None,
        group_id: str | None = None,
        match_mode: str = "exact",
        include_output_match: bool = False,
        query_scope: QueryScope = ...,   # default: QueryScope.OWNER
    ) -> tuple[list[ConversationSession], int]:
        ...

    @abstractmethod
    def get_trace(self, trace_id: str) -> Any | None:
        """Get single trace by ID."""
        ...

    @abstractmethod
    def get_ocb_trace(self, trace_id: str) -> Any | None:
        ...

    @abstractmethod
    def list_ocb_observations(self, trace_id: str) -> list[ConversationObservation]:
        ...

    @abstractmethod
    def list_legacy_observations(self, trace_id: str) -> list[ConversationObservation]:
        ...

    @abstractmethod
    def upsert_ocb_trace(self, trace: dict[str, Any], source: dict[str, Any] | None = None) -> str:
        ...

    @abstractmethod
    def upsert_ocb_observation(self, observation: dict[str, Any]) -> str:
        ...

    @abstractmethod
    def upsert_biz_refs(self, relation: dict[str, Any]) -> dict[str, int]:
        ...
