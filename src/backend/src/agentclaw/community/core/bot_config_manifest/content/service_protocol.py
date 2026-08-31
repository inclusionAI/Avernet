"""Manifest content store service contract (issue #1510, W11).

Every member is ``@abstractmethod``: an implementation that omits one fails
at construction naming the missing member, instead of raising
``AttributeError`` at the call site. Domain imports are ``TYPE_CHECKING``
-only where possible — see ``core/repository/README.md`` for why that
direction is load-bearing; ``FetchedObject`` is imported only under
``TYPE_CHECKING`` for the same reason (this protocol must not drag the
transport stack into importers that only want to store).
"""
from __future__ import annotations

from abc import abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.bot_config_manifest.content.models import (
        ContentScope,
        StoredContentRecord,
    )
    from agentclaw.community.core.bot_config_manifest.fetch.guarded_fetcher import (
        FetchedObject,
    )
    from agentclaw.community.core.repository.protocols.bot import (
        ManifestContentRepositoryProtocol,
    )


@runtime_checkable
class ManifestContentServiceProtocol(Protocol):
    """内容存储服务接口(§2.8).

    One mechanism behind three consumers — audit, delivery, ``keep_last`` —
    each of which must read the *same* platform copy rather than re-derive
    it. The digest is the address; the service never re-fetches, and the
    bytes it returns are always the bytes it can prove.
    """

    @abstractmethod
    def __init__(
        self,
        repository: ManifestContentRepositoryProtocol,
        root: Path,
    ) -> None:
        """The blob tree root is a constructor value — a config-borne
        deployment decision handed in by the composition root, never an
        environment read inside core.
        """
        ...

    @abstractmethod
    def store(
        self,
        fetched: FetchedObject,
        *,
        scope: ContentScope,
        source_url: str,
        credential_name: Optional[str] = None,
        modifier: str = "",
    ) -> StoredContentRecord:
        """Persist one fetched object as the platform's own copy.

        ``source_url`` is the manifest entry's source after ``${BOT_*}``
        substitution; the receipt is verified against the bytes before
        anything is written. ``credential_name`` is a name (W3's
        identifier), never a value.
        """
        ...

    @abstractmethod
    def read(self, digest: str) -> bytes:
        """Read the platform copy by content address, verifying it.

        Raises ``ContentMissingError`` when the address names nothing here
        — the store never re-fetches. Delivery and audit share this path.
        """
        ...

    @abstractmethod
    def records(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        limit: int = 50,  # == repository protocol's DEFAULT_RECORD_LIMIT; a literal keeps this protocol file domain-import-free at runtime
    ) -> list[StoredContentRecord]:
        """The audit read: one bot's receipts, newest first."""
        ...
