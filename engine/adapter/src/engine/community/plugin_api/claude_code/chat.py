"""ClaudeCodeChatPort — native port for chat streaming, abort, inject, and HITL resolution.

Chat is pooled (client + relay), so port methods take ``token: str | None = None``
for per-token routing. ``chat_stream`` returns an ``AsyncGenerator[EventFrame, None]``
— the relay-frame → EventFrame translation, event-name derivation, and sessionKey
injection all happen inside the impl, so the adapter sees fully-formed
EventFrames. ``chat_abort`` / ``chat_inject`` / the three ``resolve_*`` methods
return raw dicts (``{success, error, payload}``); the adapter builds the core
DTOs.

Relay RPC mapping (teamclaw-aicoding-relay v3 protocol):

==========================  ================================================
Port method                 Relay RPC (method name on the wire)
==========================  ================================================
``chat_stream``             ``chat.send`` (streaming events)
``chat_abort``              ``chat.abort``
``chat_inject``             ``chat.inject``
``resolve_exec_approval``   ``exec.approval.resolve``
``resolve_interaction``     ``interaction.resolve`` (AskUserQuestion)
``resolve_mode_transition`` ``mode_transition.resolve`` (ExitPlanMode)
==========================  ================================================
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, Protocol

from engine.community.kernel.frames import EventFrame


class ClaudeCodeChatPort(Protocol):
    """Native chat operations over the claude_code gateway (vendored Node relay)."""

    async def chat_stream(
        self,
        session_key: str,
        message: str,
        timeout_ms: int | None = None,
        cwd: str | None = None,
        model: str | None = None,
        permission_mode: str | None = None,
        attachments: list[Any] | None = None,
        token: str | None = None,
    ) -> AsyncGenerator[EventFrame, None]:
        """Stream chat events from the relay as EventFrames.

        Relocates the relay-loop + frame-translation from
        ``engines/claude_code/chat.py`` — sessionKey injection, event-name
        derivation (``_source_event`` hint / legacy ``state`` mapping),
        stop-state break, and the ``_derive_event_name`` fallback all happen
        inside the impl. Transport errors are NOT caught here — they propagate
        to the adapter, which converts them to error EventFrames.

        Args:
            session_key: Pre-composed relay sessionKey (``agent:*:session:*:user:*``).
            message: The user's message text.
            timeout_ms: Optional alive-time override forwarded to the relay.
            cwd: Optional working directory for the Claude subprocess.
            model: Optional model id (routed via the relay's provider map).
            permission_mode: Optional permission mode (e.g. ``plan``).
            attachments: Optional attachments list extracted from extraParams.
            token: MCP token for per-token pool routing; None → default client.

        Yields:
            EventFrame instances matching the relay event vocabulary.
        """
        ...

    async def chat_abort(
        self,
        session_key: str,
        run_id: str | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Cancel an in-flight chat run via ``chat.abort``.

        Returns the raw ``{success, error, payload}`` dict. On connection/RPC
        exceptions the impl returns a synthetic failure dict so the adapter
        always receives a uniform dict rather than an exception.

        Args:
            session_key: The session whose run should be aborted.
            run_id: Optional run id to cancel; None → abort the session's run.
            token: MCP token for per-token pool routing; None → default client.

        Returns:
            Raw dict with keys: ``success`` (bool), ``error`` (dict|None),
            ``payload`` (dict|None).
        """
        ...

    async def chat_inject(
        self,
        session_key: str,
        message: str,
        label: str | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Inject a chat message into session history without triggering an agent run (``chat.inject``).

        Returns the raw ``{success, error, payload}`` dict (same shape as
        ``chat_abort``). The relay is responsible for updating its SessionStore,
        appending to ``.claude/projects`` JSONL, and broadcasting the chat
        event to the frontend.

        Args:
            session_key: The session to inject into.
            message: The message text to append.
            label: Optional label for the injected message.
            token: MCP token for per-token pool routing; None → default client.
        """
        ...

    async def resolve_exec_approval(
        self,
        session_key: str,
        run_id: str,
        decision: str,
        message: str | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a pending exec approval (shell/file tool) via ``exec.approval.resolve``.

        Args:
            session_key: The session with the pending approval.
            run_id: The run id awaiting approval.
            decision: ``allow`` / ``deny`` (the relay translates to its wire form).
            message: Optional human rationale.
            token: MCP token for per-token pool routing; None → default client.

        Returns:
            Raw ``{success, error, payload}`` dict.
        """
        ...

    async def resolve_interaction(
        self,
        session_key: str,
        run_id: str,
        response: str | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a pending AskUserQuestion interaction via ``interaction.resolve``.

        Args:
            session_key: The session with the pending interaction.
            run_id: The run id awaiting the user's answer.
            response: The user's free-text answer (None for "no response").
            token: MCP token for per-token pool routing; None → default client.

        Returns:
            Raw ``{success, error, payload}`` dict.
        """
        ...

    async def resolve_mode_transition(
        self,
        session_key: str,
        run_id: str,
        decision: str,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a pending ExitPlanMode transition via ``mode_transition.resolve``.

        Args:
            session_key: The session with the pending transition.
            run_id: The run id awaiting the decision.
            decision: ``accept`` / ``reject`` (the relay translates to its wire form).
            token: MCP token for per-token pool routing; None → default client.

        Returns:
            Raw ``{success, error, payload}`` dict.
        """
        ...


__all__ = ["ClaudeCodeChatPort"]
