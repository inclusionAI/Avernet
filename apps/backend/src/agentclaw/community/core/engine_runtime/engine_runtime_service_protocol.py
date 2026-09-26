"""Service API Protocol for the engine-runtime relay.

Declares **real signatures**, not ``*args/**kwargs``, so
``tests/community/architecture/test_service_api_conformance.py`` can assert full
signature equality against ``EngineRuntimeRelay`` — parameter names, kinds,
defaults, and coroutine status. Keep the two in step: a single changed default
fails that gate.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agentclaw.community.core.engine_runtime.models import BotFacts, EngineResult


@runtime_checkable
class EngineRuntimeRelayProtocol(Protocol):
    """Forward one public runtime request to a bot's engine adapter."""

    def resolve_bot(self, bot_id: str, owner_id: str, caller_id: str) -> BotFacts:
        """Return the addressed bot's facts, or raise ``BotNotFoundError``.

        Resolves ``(bot_id, owner_id)``, then adjudicates whether
        ``caller_id`` may operate the resolved bot — its owner, or a
        collaborator at member level or above; anyone else gets the same
        masked ``BotNotFoundError`` an absent bot raises. ``caller_id`` must
        come from the authenticated principal; ``owner_id`` is the owner the
        request addresses and may name someone else.

        Exposed because handlers must branch on bot facts *before* deciding
        whether to forward at all — the sessions group reads ``bot_type``, the
        connection endpoint reads ``active_engine``. Returns a narrow value
        object, not the raw record, so device binding internals cannot reach a
        public handler by accident.
        """
        ...

    async def resolve_bot_off_loop(
        self, bot_id: str, owner_id: str, caller_id: str
    ) -> BotFacts:
        """:meth:`resolve_bot`, run in a worker thread.

        The form handlers should use: the resolve is synchronous database work
        and would otherwise block the event loop. Hand the result to
        :meth:`call` as ``facts`` so a gated route resolves the bot once.
        """
        ...

    async def call(
        self,
        *,
        bot_id: str,
        owner_id: str,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
        enveloped: bool = True,
        facts: BotFacts | None = None,
        stage: str,
    ) -> EngineResult:
        """Issue ``method path`` against the addressed bot's engine adapter.

        ``enveloped=False`` for the one engine route that answers with a raw
        payload instead of the standard envelope (``GET /api/engine/status``).

        ``facts`` reuses a resolve a gating handler already paid for; ``None``
        resolves here with the owner as the caller. Only a value this relay
        returned for the same ``bot_id``/``owner_id`` is safe — it stands in
        for the ownership proof.

        ``stage`` names which of a ``service`` bot's runtimes this call
        addresses: the draft workspace (``ac_bots.binding_id``) or a published
        stage's live binding. Required with no default, so the stage a
        handler gated on and the stage it forwards to cannot silently
        diverge. Ignored by a personal bot — refusing a published stage on
        one is the gate's job, before any device work.
        """
        ...


__all__ = ["EngineRuntimeRelayProtocol"]
