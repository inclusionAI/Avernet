"""The engine's read port onto the content store."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

__all__ = ["ContentReader"]


@runtime_checkable
class ContentReader(Protocol):
    """Bytes by digest, hash-verified. ``ContentStoreProtocol.read`` satisfies it structurally.

    Raises the store's ``ContentMissingError`` when the address names nothing:
    a materialiser handed a digest that is not in the store has met a compiler
    bug, and reports the entry failed rather than guessing.
    """

    @abstractmethod
    def read(self, digest: str) -> bytes: ...
