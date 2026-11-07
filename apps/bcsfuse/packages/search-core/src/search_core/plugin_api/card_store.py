"""Durable Card storage consumed by the management service.

Writes commit before returning; failed persistence must raise. Implementations
own internal conflict retries. Callers do not supply revisions.
"""

from typing import Any, Protocol

from search_core.api.cards import CardRecord


class CardStore(Protocol):
    def read_card(self, agent_id: str) -> CardRecord | None: ...
    def upsert(
        self,
        agent_id: str,
        fields: dict[str, Any],
        resource_type: str | None,
        search_config: dict[str, Any],
        *,
        labels: dict[str, list[str]] | None = None,
    ) -> CardRecord: ...
    def remove(self, agent_id: str) -> None: ...
