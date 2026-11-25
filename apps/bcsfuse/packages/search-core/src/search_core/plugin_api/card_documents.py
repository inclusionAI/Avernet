"""Business Card-to-document projection, selected by application composition."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from search_core.plugin_api.index_documents import DocumentText

if TYPE_CHECKING:
    from search_core.catalog.cards import CardSnapshot


@dataclass(frozen=True)
class CardProjection:
    """Retrieval text and declared filter labels; never changes source data."""

    documents: Sequence[DocumentText]
    filters: dict[str, tuple[str, ...]]


class CardDocumentConverter(Protocol):
    version: str

    def convert(self, card: CardSnapshot) -> CardProjection: ...
