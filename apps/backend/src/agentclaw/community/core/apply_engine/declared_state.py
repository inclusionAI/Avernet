"""Whether the platform holds a declared state for a bot: the one question ownership turns on.

A bot whose state the platform declared (today: a bot that carries a stored
configuration document; the creation job applied it before the container was
provisioned) gets its first artifact from the platform; a bot created without
one keeps the engine's own files. The service that owns that declaration
answers with its ``get``; the engine names no such service, only this port.

Its own module, with no imports of its own, so the service that implements it
(``BotConfigManifestServiceProtocol``) can inherit it explicitly without
pulling the compose reader's graph.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable


@runtime_checkable
class DeclaredStateSource(Protocol):
    """The declared state of a bot, or ``None`` when the platform holds none for it."""

    @abstractmethod
    def get(self, *, entity_id: str, bot_id: str) -> object | None: ...


__all__ = ["DeclaredStateSource"]
