"""Transport-independent API for the service-Bot evaluation domain."""

from __future__ import annotations

from typing import Any, Protocol, Sequence, runtime_checkable

from agentclaw.community.plugin_api.service_bot_eval import ServiceBotEvalFile


@runtime_checkable
class ServiceBotEvalServiceProtocol(Protocol):
    """Authorize, resolve release context, and dispatch evaluation operations."""

    async def precheck(
        self,
        bot_id: str,
        bot_version: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> dict[str, Any]: ...

    async def detail(
        self,
        bot_id: str,
        bot_version: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> dict[str, Any]: ...

    async def submit(
        self,
        bot_id: str,
        bot_version: str,
        *,
        actor_id: str,
        owner_id: str,
        files: Sequence[ServiceBotEvalFile],
        trigger_source: str,
    ) -> dict[str, Any]: ...

    async def get_submission(
        self,
        bot_id: str,
        submission_id: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> dict[str, Any]: ...

    async def history(
        self,
        bot_id: str,
        bot_version: str,
        *,
        actor_id: str,
        owner_id: str,
        page_no: int = 1,
        page_size: int = 10,
    ) -> dict[str, Any]: ...

    async def sync(
        self,
        bot_id: str,
        bot_version: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> dict[str, Any]: ...
