"""``ContentReader``: the content store's read port — bytes by digest, hash-verified.

The apply engine's materialisers and the genome's compiler read stored bytes
through this and nothing else; ``ContentStoreProtocol`` inherits it, so the
service that files content is, by declaration, the one that reads it back.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

__all__ = ["ContentReader"]


@runtime_checkable
class ContentReader(Protocol):
    """Bytes by digest, hash-verified.

    Raises the store's ``ContentMissingError`` when the address names nothing:
    a materialiser handed a digest that is not in the store has met a compiler
    bug, and reports the entry failed rather than guessing.
    """

    @abstractmethod
    def read(self, digest: str) -> bytes: ...
