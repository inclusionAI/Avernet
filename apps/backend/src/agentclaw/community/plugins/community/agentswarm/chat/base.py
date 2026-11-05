"""The chat transport seam: one contract, one vocabulary, two implementations.

A chat leg is what agentswarm asks of a bot: send one message, wait a bounded
time, get the reply back as text — or fail in a way the caller can classify.
*How* that travels (a direct engine WebSocket, or the platform's own chat API)
is the implementation's business; the orchestration layer only ever sees this
module's vocabulary.

The abstract base class is the contract. ``agentclaw...agentswarm.chat.engine_ws``
implements it over the engine's WebSocket, and
``agentclaw...agentswarm.chat.baas_gateway`` over the platform API. Because both
inherit this class, a contract test can run the same scenarios against either
one and prove they promise the same thing.

Transport-agnostic by design: no framework, no HTTP client, no configuration
read here.
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from enum import StrEnum

from agentclaw.community.core.agentswarm.domain.models import BotId


class SessionMode(StrEnum):
    """How a chat leg relates to prior turns on the same bot.

    ``FRESH`` — the leg must not inherit history. The engine channel expresses
    this by resetting the session before every send (its existing behaviour,
    unchanged); the platform channel by opening a new session.

    ``CONTINUE`` — the leg continues the conversation named by
    ``session_key``. A channel that cannot continue a conversation still
    behaves as ``FRESH`` rather than failing: the caller's contract is "a leg
    ran", and history isolation is the weaker of the two guarantees.
    """

    FRESH = "fresh"
    CONTINUE = "continue"


class ChatError(RuntimeError):
    """Base of every chat-transport failure.

    Deriving from ``RuntimeError`` keeps every existing ``except RuntimeError``
    around a chat leg working unchanged.
    """


class ChatTimeoutError(ChatError):
    """The leg did not finish within the caller's deadline.

    The deadline is timed by the transport itself, never delegated to the
    engine or the platform: a hung channel must surface as this error rather
    than blocking the caller's worker forever.
    """


class ChatRefusedError(ChatError):
    """The channel was reachable but refused this leg.

    Permission problems and malformed requests land here: retrying the same
    call unchanged will fail the same way.
    """


class ChatUnavailableError(ChatError, ConnectionError):
    """The bot has no reachable runtime, or the service behind it is down.

    Distinct from :class:`ChatRefusedError` because it is worth retrying later
    rather than fixing the request.

    Also a ``ConnectionError`` because that is what a dead transport has always
    surfaced as on the engine channel, and existing callers catch it that way.
    Multiple inheritance here keeps both readings true instead of forcing every
    caller to relearn the type.
    """


class ChatRateLimitedError(ChatError):
    """The bot is over its concurrency ceiling; retry after a delay."""


class ChatTransport(ABC):
    """One bot chat leg, synchronous by the BotPlatform SPI contract.

    Implementations are responsible for their own blocking and for keeping
    their own wait bounded; callers may run them on a worker thread.
    """

    @property
    @abstractmethod
    def chat_timeout_seconds(self) -> float:
        """The deployment-configured deadline a leg falls back to when the
        caller supplies none."""

    @abstractmethod
    def chat(
        self,
        bot_id: BotId,
        message: str,
        owner_id: str = "",
        session_mode: SessionMode = SessionMode.FRESH,
        session_key: str | None = None,
        cancel_event: threading.Event | None = None,
        timeout_seconds: float | None = None,
        tenant: str = "",
    ) -> str:
        """Run one chat leg and return the bot's reply text.

        Args:
            bot_id: The bot to talk to.
            message: The objective to send.
            owner_id: The authenticated caller this leg acts under. Required —
                there is no ambient identity to fall back to.
            session_mode: Whether the leg inherits history (see
                :class:`SessionMode`).
            session_key: The conversation to continue, when the mode is
                ``CONTINUE`` and the caller has a known one.
            cancel_event: Checked between receive slices; once set, the leg
                unwinds promptly and returns an empty string.
            timeout_seconds: The caller's wait limit. Absent, the transport's
                own :attr:`chat_timeout_seconds` governs.
            tenant: The tenant this execution acts under. Only channels that
                select platform credentials per tenant read it.

        Returns:
            The bot's reply as text. An empty string is returned when the leg
            was cancelled — callers distinguish that case with their cancel
            event, not with the text.

        Raises:
            ChatError: A subclass of it, per the failure classification.
        """
        ...


__all__ = [
    "ChatError",
    "ChatRateLimitedError",
    "ChatRefusedError",
    "ChatTimeoutError",
    "ChatTransport",
    "ChatUnavailableError",
    "SessionMode",
]