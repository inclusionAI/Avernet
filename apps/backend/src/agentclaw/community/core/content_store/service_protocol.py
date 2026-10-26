"""Content store service contract (issue #1510, W11; the Genome Registry's produced-bytes path, RSI-03).

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

from agentclaw.community.core.content_store.reader import ContentReader

if TYPE_CHECKING:
    from agentclaw.community.core.content_store.models import (
        ContentScope,
        StoredContentRecord,
    )
    from agentclaw.community.core.bot_config_manifest.fetch.errors import (
        FetchedObject,
    )
    from agentclaw.community.core.repository.protocols.bot import (
        ContentStoreRepositoryProtocol,
    )


@runtime_checkable
class ContentStoreProtocol(ContentReader, Protocol):
    """内容存储服务接口(§2.8).

    One mechanism behind three consumers — audit, delivery, ``keep_last`` —
    each of which must read the *same* platform copy rather than re-derive
    it. The digest is the address; the service never re-fetches, and the
    bytes it returns are always the bytes it can prove.
    """

    @abstractmethod
    def __init__(
        self,
        repository: ContentStoreRepositoryProtocol,
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
        apply_id: Optional[str] = None,
        category: Optional[str] = None,
        entry_identity: Optional[str] = None,
    ) -> StoredContentRecord:
        """Persist one fetched object as the platform's own copy.

        ``source_url`` is the manifest entry's source after ``${BOT_*}``
        substitution; the receipt is verified against the bytes before
        anything is written. ``credential_name`` is a name (W3's
        identifier), never a value.

        The ``apply_id``/``category``/``entry_identity`` trio links the row
        back to the apply and the entry the fetch served — optional because
        this layer must not require what its caller may not know (keep_last
        reuse, hand-driven fetches); when a caller supplies them, "what did
        apply X fetch" and "what was fetched for this entry" are indexed
        reads instead of ``source_url`` approximation.

        ``content_type`` is advisory: an over-wide header stores ``NULL``
        plus a log line rather than refusing the receipt.
        """
        ...

    @abstractmethod
    def store_produced(
        self,
        data: bytes,
        *,
        scope: ContentScope,
        origin: str,
        modifier: str = "",
        category: Optional[str] = None,
        entry_identity: Optional[str] = None,
    ) -> StoredContentRecord:
        """Persist bytes the platform produced rather than fetched (the Genome Registry's path).

        A captured persona file, a file a patch wrote, an upload for a
        patch: no source URL exists, so the provenance row records
        ``produced://<origin>`` as both URLs — ``origin`` is a short label
        such as ``genome/capture``, ``genome/patch`` or ``genome/upload``.
        The blob write is the same content-addressed, idempotent write
        ``store`` makes. One row per bot and digest, best effort: when this
        bot already holds a row for the digest, no second row is filed and
        the returned record carries ``id=None`` (``is_reachable`` says which
        case the caller is in). Two concurrent stores of the same bytes can
        both file a row; the log has no unique key, and a second row changes
        no answer.
        """
        ...

    @abstractmethod
    def is_reachable(self, scope: ContentScope, digest: str, *, produced_by: Optional[str] = None) -> bool:
        """Whether this bot holds a provenance row for the digest.

        With ``produced_by`` (an origin's first segment, ``"genome"``), only
        rows that producer filed count (``produced://genome/…``): a digest the
        Manifest fetched for the bot is not thereby the registry's.

        Reachability is what lets the registry hand bytes to a caller: a
        digest that exists in the blob tree but was never stored *for this
        bot* is not this bot's to read.
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
        scope: ContentScope,
        *,
        limit: Optional[int] = None,
    ) -> list[StoredContentRecord]:
        """The audit read: one bot's receipts, newest first.

        Same scope shape as ``store`` — the bot a store event was on behalf
        of. ``limit=None`` means the repository protocol's
        ``DEFAULT_RECORD_LIMIT``; that constant stays the one definition, so
        this file carries no drifting copy of its value.
        """
        ...

    @abstractmethod
    def latest_receipt(
        self,
        scope: ContentScope,
        *,
        source_url: str,
    ) -> Optional[StoredContentRecord]:
        """The bot's newest receipt for one source URL, or ``None``.

        The per-lookup twin of ``records`` the fetch pipeline needs: that one
        bounds at ``DEFAULT_RECORD_LIMIT`` and returns every source, so a busy
        bot can evict the very row a category asks about. Same exact-equality
        source matching as the repository beneath; never re-fetches.
        """
        ...
