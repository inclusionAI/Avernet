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
    def get_body(self, *, entity_id: str, bot_id: str, bot_incarnation: int) -> str:
        """Return the bot's script body, or ``""`` when it has none.

        ``bot_incarnation`` is the ``ac_bots.id`` of the bot being started. A
        stored row names the incarnation that wrote it, and one written for an
        earlier holder of the same identifier reads as ``""`` — the caller is
        composing a command that will execute this body, and it must only ever
        execute the body this bot stored.
        """
        ...


class TeclawEngineTestProtocol(Protocol):
    """The one question this feature asks of teclaw provisioning.

    Declared here, narrow, rather than depending on ``TeclawProvisionService``
    itself — and that is not tidiness. Importing that class into this service
    closes a real import cycle: ``teclaw_provision_service`` reaches
    ``service_bot`` → ``bot_service`` → ``default_image_policy_listener``, which
    imports ``teclaw_provision_service`` back while it is still initialising.
    The whole test suite hid it, because something else always imported that
    chain first; importing this service on its own raised ImportError.

    ``TeclawProvisionService`` satisfies this structurally, so the composition
    root still hands over the canonical implementation and the single definition
    of "runs in a teclaw container" stays where it is.
    """

    @abstractmethod
    def is_teclaw(self, active_engine: str | None) -> bool:
        """Whether a bot with this engine runs in a teclaw container."""
        ...


class StartupScriptPurgeProtocol(Protocol):
    """Drop a bot's stored script when the bot itself is deleted.

    Separate from the reader for the same reason the reader is separate from the
    service: the bot-deletion path needs to *remove* a script, never read one or
    write one, so that is all it is handed.

    This exists because a stored script is the only per-bot row this feature
    adds, and bot deletion is a soft update — no cascade reaches it. Without a
    sweep the row outlives its bot indefinitely, which is both plaintext
    executable content retained past its owner and, if a bot id is ever reused,
    a script the new bot's owner never wrote.
    """

    @abstractmethod
    def delete(self, *, entity_id: str, bot_id: str) -> bool:
        """Remove the bot's script. Idempotent — absent is success.

        Unconditional: used before the soft delete, while the bot is still
        alive and the identifier cannot belong to anyone else.
        """
        ...

    @abstractmethod
    def delete_written_by(
        self, *, entity_id: str, bot_id: str, bot_incarnation: int
    ) -> bool:
        """Remove the script only if that incarnation still owns it.

        Used *after* the soft delete, where the identifier is free again and a
        recreated bot may already have stored a script of its own.
        """
        ...
