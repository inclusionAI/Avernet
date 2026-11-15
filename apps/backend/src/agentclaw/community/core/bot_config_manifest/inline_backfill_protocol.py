"""Service API contract for the Manifest inline-content backfill (transitional).

The contract only; the implementation is ``services/inline_backfill.py``.
Remove both, with ``ContentStoreProtocol.ensure_stored``, once the content
rows filed before the bytes moved to object storage have been backfilled.
"""
from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class InlineBackfillRowNotFound(LookupError):
    """This bot holds no Manifest-produced row for the digest in the running environment."""


@dataclass(frozen=True)
class InlineBackfillEntry:
    """One inline body of the document, as stored: e.g. ``identity`` / ``SOUL.md`` / ``sha256:…`` / 1204."""

    category: str
    identity: str
    digest: str
    size_bytes: int


@dataclass(frozen=True)
class InlineBackfillReport:
    """What a backfill did for one row.

    ``document_found`` is ``False`` when the bot no longer has a stored
    Manifest. ``row_digest_restored`` says whether the row's own digest is
    among the bodies stored. ``False`` with a document present means either
    that the Manifest no longer declares that inline content (nothing is left
    to restore it from), or that the row is not an inline one
    (``manifest/tree``, ``manifest/receipt``): a re-apply restores those.
    """

    entity_id: str
    bot_id: str
    digest: str
    document_found: bool
    row_digest_restored: bool
    entries: tuple[InlineBackfillEntry, ...]


@runtime_checkable
class ManifestInlineBackfillProtocol(Protocol):
    """Store the inline bodies of the Manifest a content row came from."""

    @abstractmethod
    def backfill(self, *, entity_id: str, bot_id: str, digest: str) -> InlineBackfillReport:
        """Repair one ``ac_manifest_content`` row, named by its ``entity_id``, ``bot_id`` and ``digest``.

        Raises:
            InlineBackfillRowNotFound: no Manifest-produced row for this bot and digest.
            ContentStoreError: the digest is not a well-formed ``sha256:<hex64>``.
            ContentStoreFault: the bytes could not be stored.
        """
        ...
