"""Manifest content provenance-log repository contract (issue #1510, W11).

Every member is ``@abstractmethod``: an implementation that omits one fails at
construction naming the missing member, instead of raising ``AttributeError`` at
the call site. Domain imports are ``TYPE_CHECKING``-only — see
``core/repository/README.md`` for why that direction is load-bearing.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Optional, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.bot_config_manifest.content.models import (
        StoredContentRecord,
    )


#: Default bound for one audit read. The log is append-only and unbounded,
#: so an unbounded query is a footgun, not a convenience; callers that need
#: everything paginate by asking for the tail.
DEFAULT_RECORD_LIMIT = 50


@runtime_checkable
class ManifestContentRepositoryProtocol(Protocol):
    """manifest 内容溯源日志仓库接口.

    Backed by ``ac_manifest_content`` — an append-only log, one row per store
    event. There is deliberately no upsert and no delete: the same digest
    stored again is a new row (that repetition is the audit fact "when, from
    where, on whose behalf"), and row deletion would breach the stated
    retention policy (see the DDL). The bytes are not here — they live in the
    content-addressed blob directory the service owns; this repository answers
    provenance questions only.
    """

    @abstractmethod
    def add(self, record: StoredContentRecord) -> StoredContentRecord:
        """Append one provenance row; returns the stored record with its id.

        The record is inserted exactly as handed over — this layer never
        derives, normalises or trims (the service did that before it got
        here), so what the audit log holds is what the service decided.
        """
        ...

    @abstractmethod
    def records_for(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        limit: int = DEFAULT_RECORD_LIMIT,
    ) -> list[StoredContentRecord]:
        """One bot's receipts, newest first — the audit read.

        ``limit`` must be positive and is **refused otherwise** (raises
        ``ValueError``): an empty answer in this context would be read as
        "this bot has no receipts" — a claim, not a page — and a negative
        LIMIT changes meaning per dialect (SQLite reads it as unbounded),
        so clamping it to nothing would merely be the quieter wrong answer.
        """
        ...

    @abstractmethod
    def latest_for(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        source_url: str,
    ) -> Optional[StoredContentRecord]:
        """The newest provenance row for one bot and one source URL, or ``None``.

        The per-source lookup the fetch pipeline asks ("does this bot's newest
        receipt for *this* URL hold these bytes?"): newest-first on the same
        ordering ``records_for`` uses, filtered to one source, bounded by no
        ``DEFAULT_RECORD_LIMIT`` — a busy bot cannot evict from the answer the
        one row a category wants. ``source_url`` is compared by exact string
        equality, never ``startswith``: a sibling path is a different source.
        """
        ...
