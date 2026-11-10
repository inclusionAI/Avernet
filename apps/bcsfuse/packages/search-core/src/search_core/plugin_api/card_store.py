"""Durable Card storage consumed by the management service.

Writes commit before returning; failed persistence must raise. Implementations
own internal conflict retries. Callers do not supply revisions.
"""

from collections.abc import Iterator
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


class CardChangeSource(Protocol):
    """Lightweight source discovery, including tombstones and all revisions.

    iter_changes yields (ID/revision page, fixed database-time upper bound).
    Consumers advance only after the iterator completes, and retain unfinished
    work separately. Inclusive timestamp replay does not replace reconciliation
    for transactions committed behind the timestamp checkpoint.
    """

    def iter_changes(
        self, checkpoint: float, batch_size: int
    ) -> Iterator[tuple[list[tuple[str, int]], float]]: ...

    def page(self, after: str, limit: int) -> list[tuple[str, int]]: ...
