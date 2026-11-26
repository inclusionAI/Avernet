"""Public service protocols for personal Bot dormant lifecycle operations."""
from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from agentclaw.community.core.bot_dormant.types import BotLifecycleResult


@runtime_checkable
class BotDormantActivateServiceProtocol(Protocol):
    """User-scoped service that reactivates a recycled personal cloud Bot.

    The concrete service owns validation plus Passport unfreeze and background
    resource start orchestration.
    """

    def activate(
        self,
        *,
        bot_id: str,
        owner_id: str,
        owner_name: str | None = None,
    ) -> BotLifecycleResult: ...


@runtime_checkable
class BotDormantRecycleServiceProtocol(Protocol):
    """Owner-scoped service that recycles a personal managed-cloud Bot."""

    def recycle(
        self,
        *,
        bot_id: str,
        owner_id: str,
        owner_name: str | None = None,
    ) -> BotLifecycleResult: ...


@runtime_checkable
class BotDormantAuditServiceProtocol(Protocol):
    """Best-effort audit writer for explicit public lifecycle changes."""

    def record_openapi_recycle(
        self,
        *,
        request_id: str,
        bot_id: str,
        owner_id: str,
    ) -> None: ...


@runtime_checkable
class BotDormantRecycleOperationProtocol(Protocol):
    """Engine-selected recycle and OWNER-scoped read-only operation query.

    Confirmed-fastdisk AICoding Bots return a durable operation handle. Other
    supported personal managed-cloud Bots use the original synchronous path and
    return a null operation handle. An already RECYCLED Bot without an unresolved
    operation is idempotent.
    get_operation returns only the current/latest matching handle, hiding others as not-found.
    Backup/release errors after asynchronous acceptance are reported in the operation result.
    source/reason are trusted adapter metadata, never OpenAPI request fields.
    First admission owns them: duplicate submissions do not replace metadata.
    manual_ops retains its existing success audit/notification at completion.
    """
    def submit(self, *, bot_id: str, owner_id: str, owner_name: str | None = None,
               request_id: str = "", source: Literal["openapi", "manual_ops"] = "openapi",
               reason: str | None = None) -> dict: ...
    def get_operation(self, *, bot_id: str, owner_id: str, operation_id: str) -> dict: ...
