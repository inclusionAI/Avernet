"""Service API Protocol for bot publish CRUD."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.service_bot.repository.models import BotPublishRecord


@runtime_checkable
class BotPublishServiceProtocol(Protocol):
    """Service API for bot-publish record management."""

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

    def upgrade_publish(self, publish_id: int, owner_id: str) -> BotPublishRecord:
        """Create/reuse a successor draft after caller authorization and locking.

        A newly created successor submits the source Bot's ordinary draft
        restart with its current owner. Teclaw skips this unsupported operation.
        Existing successors never restart again. UpgradeDraftRestartError keeps
        the persisted draft when submission fails; use manual draft restart to
        recover. Returning a draft does not prove runtime health.
        """
        ...
