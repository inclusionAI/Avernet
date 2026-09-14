"""Public service protocols for personal Bot dormant lifecycle operations."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

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
