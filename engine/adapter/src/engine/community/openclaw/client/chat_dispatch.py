"""Confirm a chat.send dispatch, not an intermediate agent lifecycle end."""
from __future__ import annotations

import asyncio
import logging
import math
import re
from typing import TYPE_CHECKING, Any, Callable

from engine.community.openclaw.protocol import EventFrame, ResponseFrame

if TYPE_CHECKING:
    from engine.community.openclaw.client.gateway_client import OpenClawGatewayClient

log = logging.getLogger("openclaw-client")
POLL_SECONDS = 0.5
QUERY_TIMEOUT = 5.0


def supports_dispatch_wait(client: OpenClawGatewayClient) -> bool:
    """Do not infer completion semantics from the presence of agent.wait alone."""
    hello = client.hello
    return bool(hello and re.fullmatch(r"2026\.5\.12(?:[-+].*)?", hello.server.version)
                and "agent.wait" in hello.features.methods)


class ChatDispatch:
    def __init__(self, client: OpenClawGatewayClient, session: str, run: str, message: str):
        self.client = client
        self.session = session
        self.run = run
        self.identities = {run}
        self.message = message
        self.sent = False
        self.confirmed = False
        self.cached_confirmed = False
        self.started = False
        self.terminal_seen = asyncio.Event()
        self.finished = asyncio.Event()
        self.terminal: EventFrame | None = None
        self.lifecycle: EventFrame | None = None
        self.answer: dict[str, Any] | None = None
        self.task: asyncio.Task[None] | None = None

    def accept(self, response: ResponseFrame) -> None:
        if not response.ok:
            self.confirmed = True  # The gateway rejected this dispatch.
            return
        run = (response.payload or {}).get("runId")
        if isinstance(run, str) and run:
            self.run = run
            self.identities.add(run)
        else:
            raise RuntimeError("OpenClaw chat ACK has no run identity")

    def hold(self, event: EventFrame) -> bool:
        payload = event.payload or {}
        if payload.get("runId") not in self.identities:
            return False
        data = payload.get("data")
        data = data if isinstance(data, dict) else {}
        stream, phase = payload.get("stream"), data.get("phase")
        if event.event == "agent":
            if stream == "lifecycle" and phase == "start":
                self.started = True
                # A replay is a new attempt of this run, not the previous answer.
                self.answer = None
                self.terminal = None
            if stream == "assistant" and isinstance(data.get("text"), str):
                self.started = True
                self.answer = {"role": "assistant", "content": [{"type": "text", "text": data["text"]}]}
            if stream == "lifecycle" and phase in ("end", "error"):
                self.lifecycle = event
                self.terminal_seen.set()
                return True
        if event.event == "chat" and payload.get("state") in ("final", "error", "aborted"):
            self.terminal = event
            if isinstance(payload.get("message"), dict):
                self.answer = payload["message"]
            self.terminal_seen.set()
            return True
        return False

    async def watch(self, publish: Callable[[EventFrame], None], timeout: float) -> None:
        try:
            async with asyncio.timeout(timeout):
                await self.terminal_seen.wait()
                while True:
                    await asyncio.sleep(POLL_SECONDS)
                    # A positive wait races the intermediate lifecycle snapshot.
                    # In 2026.5.12, zero waits use chat dedupe while the chat is active.
                    try:
                        response = await self.client.send_request(
                            "agent.wait", {"runId": self.run, "timeoutMs": 0}, timeout=QUERY_TIMEOUT,
                        )
                    except (TimeoutError, ConnectionError):
                        # A busy/reconnecting Gateway can delay this read-only
                        # probe. Keep the slot, bounded by the dispatch deadline.
                        continue
                    payload = response.payload or {}
                    if not response.ok or payload.get("runId") != self.run:
                        raise RuntimeError("OpenClaw dispatch completion query failed")
                    status = payload.get("status")
                    if status == "timeout" and not payload.get("endedAt"):
                        continue
                    if status not in ("ok", "error", "timeout"):
                        raise RuntimeError("OpenClaw dispatch completion is unavailable")
                    ended_at = payload.get("endedAt")
                    if (type(ended_at) not in (int, float) or not math.isfinite(ended_at) or ended_at <= 0):
                        raise RuntimeError("OpenClaw completion has no valid end timestamp")
                    # Empty queued dispatches also enter chat dedupe as 'ok'.
                    # Never attach an arbitrary subsequent run on the same session.
                    command_reply = self.message.lstrip().startswith("/") and self.terminal is not None
                    if not self.started and status == "ok" and not (command_reply and self.answer):
                        raise RuntimeError("OpenClaw request completed without a correlated execution")
                    self.confirmed = True
                    self._publish_terminal(publish, status)
                    return
        except Exception as exc:
            log.warning("[chat_stream][dispatch_unconfirmed] runId=%s reason=%s", self.run, type(exc).__name__)
            publish(EventFrame(event="chat", payload={
                "runId": self.run, "sessionKey": self.session, "state": "error",
                "errorCode": "DISPATCH_UNCONFIRMED",
                "errorMessage": "OpenClaw execution completion could not be confirmed; do not automatically retry.",
            }))
        finally:
            self.finished.set()

    def _publish_terminal(self, publish: Callable[[EventFrame], None], status: str) -> None:
        if self.lifecycle is not None and status == "ok":
            payload = dict(self.lifecycle.payload or {})
            payload["data"] = {**payload.get("data", {}), "phase": "end"}
            publish(EventFrame(event="agent", payload=payload))
        payload = dict(self.terminal.payload or {}) if self.terminal else {}
        payload.update(runId=self.run, sessionKey=self.session)
        if status != "ok":
            payload.update(state="error", errorCode="AGENT_TIMEOUT" if status == "timeout" else "AGENT_ERROR",
                           errorMessage="OpenClaw execution failed" if status == "error" else "OpenClaw execution timed out")
        else:
            # A retry can recover from an intermediate lifecycle/chat error.
            payload["state"] = "aborted" if payload.get("state") == "aborted" else "final"
            payload.pop("errorCode", None)
            payload.pop("errorMessage", None)
            if payload["state"] == "final" and self.answer is not None:
                payload["message"] = self.answer
        log.info("[chat_stream][dispatch_confirmed] runId=%s status=%s", self.run, status)
        publish(EventFrame(event="chat", payload=payload))

    async def close(self) -> None:
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
