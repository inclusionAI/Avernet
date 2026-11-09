"""Collaborators passed by shared recycling to an engine-owned execution policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from agentclaw.community.core.bot_dormant.bot_dormant_service_protocol import (
        BotDormantRecycleOperationProtocol,
    )


@dataclass(frozen=True)
class RecycleServices:
    # A factory so default engines never resolve a queue, backup runtime or provider.
    operations: Callable[[], BotDormantRecycleOperationProtocol]
