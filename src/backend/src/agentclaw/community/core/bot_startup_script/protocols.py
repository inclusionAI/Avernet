"""Core-owned view of the startup-script store (issue #926).

``BaasService`` composes the container start command and needs to read a bot's
script while doing it. It cannot depend on the Service API Protocol in ``api/``
— ``core`` must not import that layer — so the read side is declared here, in
the feature's own domain, and the DI modules pass the concrete service, which
satisfies it structurally.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Protocol


class StartupScriptReaderProtocol(Protocol):
    """Read a bot's stored startup script at start-command composition time.

    Deliberately narrower than the service: composing a start command is a read,
    so the write side stays out of reach of the code that builds shell strings.
    """

    @abstractmethod
    def get_body(self, *, entity_id: str, bot_id: str) -> str:
        """Return the bot's script body, or ``""`` when it has none."""
        ...
