"""Service API Protocol for bot publish CRUD."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class BotPublishServiceProtocol(Protocol):
    """Service API for bot-publish record management.

    Opted-in knowledge completion requires observed_workflow_id from a backend
    deployment observer. Generic status callers cannot synthesize SUCCESS;
    DRAFT -> BUILDING freezes the working selection on every write entrance.
    """

    def create_first_publish_for_bot(self, *args: Any, **kwargs: Any) -> Any: ...

    def delete_service_bot(self, *args: Any, **kwargs: Any) -> Any: ...

    def get_publish_by_id(self, *args: Any, **kwargs: Any) -> Any: ...

    def get_bot_stage_binding_info(self, *args: Any, **kwargs: Any) -> Any: ...

    def upgrade_bot_to_service(self, *args: Any, **kwargs: Any) -> Any: ...

    def can_delete_bot(self, *args: Any, **kwargs: Any) -> Any: ...

    def offline_publish(self, *args: Any, **kwargs: Any) -> Any: ...

    def can_restore_draft(self, *args: Any, **kwargs: Any) -> Any: ...

    def get_draft_restore_status(self, *args: Any, **kwargs: Any) -> Any: ...

    async def restore_draft(self, *args: Any, **kwargs: Any) -> Any: ...

    def restore_failed_online_restart(
        self, *, publish_id: int, expected_ext: dict[str, Any] | None,
    ) -> Any:
        """Internal native retry admission; not a generic SUCCESS write."""
        ...
