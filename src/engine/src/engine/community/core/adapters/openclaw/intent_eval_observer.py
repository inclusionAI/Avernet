"""IntentEvalObserver — state machine that emits Langfuse signals from a
stream of OpenClaw gateway events.

Owned by `OpenClawChatService.stream` so the chat plugin keeps the event-shape-
dependent accumulation + fire-and-forget `asyncio.create_task(emit_*)` logic.
The generic web server (`api/transport/ws_server.py`) stays shape-agnostic and simply
relays EventFrames — it has no view into which frames trigger which Langfuse
signal.

Zero new functionality: the emitted Langfuse signals (emit_dialog_event,
emit_agent_response, emit_agent_complete) are the exact same ones the
legacy server emitted, in the same order, with the same metadata.

## Caller contract

```
obs = IntentEvalObserver(session_key, user_message, token)
try:
    async for event in upstream:
        obs.observe(event)
        yield event
    # normal completion only — raises skip finalize, matching legacy
    obs.finalize()
except ...:
    ...
```

`observe()` is synchronous and does not await. All emissions go through
`asyncio.create_task(...)` so the observer never blocks the stream.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from engine.community.kernel.frames import EventFrame

try:
    # intent_eval is corp-only tooling, excluded from the community/OSS build.
    # When it is absent the observer degrades to a no-op, so the community
    # engine boots and streams without it. Corp ships intent_eval and hits the
    # real emitters below, so its behaviour is unchanged.
    from intent_eval.core.event_handler import (
        emit_agent_complete,
        emit_agent_response,
        emit_dialog_event,
    )
except ModuleNotFoundError:
    async def emit_dialog_event(*args: Any, **kwargs: Any) -> None:
        return None

    async def emit_agent_response(*args: Any, **kwargs: Any) -> None:
        return None

    async def emit_agent_complete(*args: Any, **kwargs: Any) -> None:
        return None

log = logging.getLogger("openclaw-intent-eval")


class IntentEvalObserver:
    """Per-stream state machine that emits intent-eval signals.

    One instance per chat stream. Not thread-safe (driven from a single
    async iterator in a single event loop).
    """

    def __init__(
        self,
        session_key: str,
        user_message: str,
        token: str | None,
    ) -> None:
        self._session_key = session_key
        self._user_message = user_message
        self._token = token
        # Accumulators for text fragments and thinking output. Cleared at
        # each tool-call boundary (so the "text before this tool" gets
        # emitted as its own agent_response).
        self._accumulated_text = ""
        self._accumulated_thinking = ""
        # Final-state tracking so `finalize()` knows whether the stream
        # ended with a terminal state worth reporting to intent-eval.
        self._final_state: str | None = None
        self._final_event_data: dict[str, Any] = {}
        self._agent_response_emitted = False
        # Fire user-turn signal immediately — by the time this observer is
        # constructed the stream has already committed to running.
        self._emit_dialog_event()

    # ── Public surface ────────────────────────────────────────────────────

    def observe(self, event_frame: EventFrame) -> None:
        """Process one upstream event. May create asyncio tasks."""
        event_data = event_frame.payload or {}
        stream = event_data.get("stream", "")
        data = event_data.get("data") or {}
        state = event_data.get("state", "")

        # OpenClaw gateway uses the `stream` field for some state names;
        # treat `state` as authoritative when present, fall back to stream.
        effective_state = state if state else stream

        self._update_accumulators(effective_state, event_data, data)
        self._maybe_emit_thinking(effective_state)
        self._maybe_emit_moltis_tool(effective_state, event_data)
        self._maybe_emit_openclaw_tool(stream, event_data, data)
        self._maybe_emit_final(effective_state, stream, event_data, data)

        # Track a terminal state for `finalize()` to pick up.
        # `state=final|error|aborted` wins over `stream=lifecycle phase=end`.
        is_lifecycle_end = stream == "lifecycle" and (
            data.get("phase") == "end" if isinstance(data, dict) else False
        )
        if is_lifecycle_end and self._final_state is None:
            self._final_state = "lifecycle_end"
            self._final_event_data = event_data
        elif state in ("final", "error", "aborted"):
            self._final_state = state
            self._final_event_data = event_data

    def finalize(self) -> None:
        """Called after the stream ends normally. Emits `emit_agent_complete`.

        Skipped on error paths, matching the legacy server's behaviour (the
        legacy emit-agent-complete block lived outside the async-for but
        inside the outer try, so raises bypassed it).
        """
        if self._final_state not in ("final", "aborted", "lifecycle_end"):
            return

        final_text = self._final_event_data.get("text", "")
        data = self._final_event_data.get("data")
        if not final_text and isinstance(data, dict):
            final_text = data.get("text", "")
        if not final_text:
            final_text = self._accumulated_text.strip()
        else:
            final_text = final_text.strip()

        if not final_text:
            return

        model_id = None
        if isinstance(data, dict):
            model_id = data.get("model")
        if not model_id:
            model_id = self._final_event_data.get("model")

        try:
            asyncio.create_task(
                emit_agent_complete(
                    session_id=self._session_key,
                    user_id="",
                    user_message=self._user_message,
                    agent_message=final_text,
                    metadata={"model": model_id} if model_id else None,
                )
            )
        except Exception as e:
            log.warning(f"Failed to emit agent complete: {e}")

    # ── Internal helpers ──────────────────────────────────────────────────

    def _emit_dialog_event(self) -> None:
        try:
            asyncio.create_task(
                emit_dialog_event(
                    session_id=self._session_key,
                    user_id="",
                    message=self._user_message,
                    source="openclawserver-websocket",
                    token=self._token,
                )
            )
        except Exception as e:
            log.warning(f"Failed to emit dialog event: {e}")

    def _update_accumulators(
        self,
        effective_state: str,
        event_data: dict[str, Any],
        data: Any,
    ) -> None:
        """Buffer text fragments for downstream flushing."""
        if effective_state == "delta":
            self._accumulated_text = event_data.get("text", self._accumulated_text)
            if not self._accumulated_text and isinstance(data, dict):
                self._accumulated_text = data.get("text", self._accumulated_text)
        elif effective_state == "assistant":
            # `stream=assistant` carries the full running text so far —
            # replace rather than append.
            text = event_data.get("text", "")
            if not text and isinstance(data, dict):
                text = data.get("text", "")
            if text:
                self._accumulated_text = text
        elif effective_state in ("thinking_delta", "thinking_text"):
            text = event_data.get("text", "")
            if not text and isinstance(data, dict):
                text = data.get("text", self._accumulated_thinking)
            if text:
                self._accumulated_thinking = text

    def _maybe_emit_thinking(self, effective_state: str) -> None:
        if effective_state != "thinking_done":
            return
        final_thinking = (
            self._accumulated_thinking.strip() if self._accumulated_thinking else ""
        )
        if final_thinking:
            try:
                asyncio.create_task(
                    emit_agent_response(
                        session_id=self._session_key,
                        user_id="",
                        message=final_thinking,
                        metadata={"type": "thinking"},
                    )
                )
            except Exception as e:
                log.warning(f"Failed to emit thinking: {e}")
        self._accumulated_thinking = ""

    def _maybe_emit_moltis_tool(
        self,
        effective_state: str,
        event_data: dict[str, Any],
    ) -> None:
        """Handle the legacy Moltis tool-call shape (state=tool_call_end)."""
        if effective_state != "tool_call_end":
            return
        tool_name = event_data.get("toolName", "")
        if not tool_name:
            return
        tool_result = event_data.get("result", "")
        is_error = not event_data.get("success")
        try:
            result_info = (
                f"Tool: {tool_name}\nResult: {tool_result}\nSuccess: {not is_error}"
            )
            asyncio.create_task(
                emit_agent_response(
                    session_id=self._session_key,
                    user_id="",
                    message=result_info,
                    metadata={
                        "type": "tool_call",
                        "toolName": tool_name,
                        "phase": "end",
                        "isError": is_error,
                    },
                )
            )
        except Exception as e:
            log.warning(f"Failed to emit tool_call_end: {e}")

    def _maybe_emit_openclaw_tool(
        self,
        stream: str,
        event_data: dict[str, Any],
        data: Any,
    ) -> None:
        """Handle the OpenClaw tool-call shape (stream=tool, data.phase=*)."""
        if stream != "tool":
            return
        tool_phase = data.get("phase", "") if isinstance(data, dict) else ""
        tool_name = data.get("name", "") if isinstance(data, dict) else ""
        tool_result = event_data.get("text", "") or (
            data.get("text", "") if isinstance(data, dict) else ""
        )
        is_error = event_data.get("isError", False) or (
            data.get("isError", False) if isinstance(data, dict) else False
        )

        # A tool call boundary flushes any buffered assistant text as its
        # own agent_response — that text logically belongs before the tool
        # call started.
        if tool_phase in ("start", ""):
            self._flush_accumulated_text()

        if tool_phase == "result" and tool_name:
            # Flush again in case more text arrived between start and result.
            self._flush_accumulated_text()
            try:
                result_info = (
                    f"Tool: {tool_name}\nResult: {tool_result}\nSuccess: {not is_error}"
                )
                asyncio.create_task(
                    emit_agent_response(
                        session_id=self._session_key,
                        user_id="",
                        message=result_info,
                        metadata={
                            "type": "tool_call",
                            "toolName": tool_name,
                            "phase": "end",
                            "isError": is_error,
                        },
                    )
                )
            except Exception as e:
                log.warning(f"Failed to emit openclaw tool event: {e}")

    def _maybe_emit_final(
        self,
        effective_state: str,
        stream: str,
        event_data: dict[str, Any],
        data: Any,
    ) -> None:
        """Emit final agent-response on state=final or lifecycle.end.

        Deduped via `_agent_response_emitted` — if both fire, only the
        first triggers emission.
        """
        is_final = effective_state == "final" or (
            stream == "lifecycle"
            and (data.get("phase") == "end" if isinstance(data, dict) else False)
        )
        if not is_final or self._agent_response_emitted:
            return
        self._agent_response_emitted = True

        final_text = event_data.get("text", "")
        if not final_text and isinstance(data, dict):
            final_text = data.get("text", "")
        if not final_text:
            final_text = self._accumulated_text
        final_text = final_text.strip() if final_text else ""
        if not final_text:
            return

        tokens_info = {
            "inputTokens": event_data.get("inputTokens")
            or (data.get("inputTokens") if isinstance(data, dict) else None),
            "outputTokens": event_data.get("outputTokens")
            or (data.get("outputTokens") if isinstance(data, dict) else None),
            "model": event_data.get("model")
            or (data.get("model") if isinstance(data, dict) else None),
            "provider": event_data.get("provider")
            or (data.get("provider") if isinstance(data, dict) else None),
        }
        try:
            asyncio.create_task(
                emit_agent_response(
                    session_id=self._session_key,
                    user_id="",
                    message=final_text,
                    metadata=tokens_info,
                )
            )
        except Exception as e:
            log.warning(f"Failed to emit agent response: {e}")

    def _flush_accumulated_text(self) -> None:
        """Emit buffered text as an agent_response and reset the buffer."""
        text = self._accumulated_text.strip() if self._accumulated_text else ""
        if not text:
            return
        try:
            asyncio.create_task(
                emit_agent_response(
                    session_id=self._session_key,
                    user_id="",
                    message=text,
                    metadata={},
                )
            )
        except Exception as e:
            log.warning(f"Failed to flush accumulated text: {e}")
        self._accumulated_text = ""


__all__ = ["IntentEvalObserver"]
