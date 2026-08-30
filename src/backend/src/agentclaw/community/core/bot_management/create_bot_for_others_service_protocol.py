"""Service API for provisioning another user's default bot."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CreateBotForOthersServiceProtocol(Protocol):
    """Create or repair a default bot before any runtime allocation occurs."""

    def execute(
        self,
        *,
        target_user_id: str,
        target_nick_name: str,
        bot_type: str | None,
        operator_user_id: str,
        operator_name: str,
        cookie: str,
    ) -> dict[str, Any]: ...
