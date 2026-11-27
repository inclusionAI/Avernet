"""Opt-in A2A completion for native asynchronous exec approval followups."""
from __future__ import annotations

import asyncio
import math
import time
from typing import Any, Callable

from engine.community.openclaw.client.chat_dispatch import ChatDispatch
from engine.community.openclaw.protocol import EventFrame


def _session(value: Any) -> str:
    return value.strip().lower().removeprefix("agent:main:") if isinstance(value, str) else ""


class ExecApprovalDispatch(ChatDispatch):
    """Keep the session slot until the original run and its exact followups end.

    OpenClaw 2026.5.12 uses exec-approval-followup:<id>[:nonce:<nonce>]
    as the followup agent runId. A same-session unrelated run is never adopted.
    Neither resolution ACK nor the original chat dedupe is execution completion.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pending: dict[str, float] = {}
        self.followups: dict[str, ChatDispatch] = {}
        self.approval_runs: dict[str, str] = {}
        self.results: dict[str, EventFrame] = {}
        self.ends: dict[str, EventFrame] = {}
        self.changed = asyncio.Event()
        self.monitors: list[asyncio.Task] = []
        self.watching = False

    def approval(self, event: EventFrame, state: str) -> bool:
        payload = event.payload or {}
        iid = payload.get("approvalId") or payload.get("id")
        if not isinstance(iid, str) or not iid:
            return False
        if state == "approval_resolved":
            return iid in self.pending or iid in self.approval_runs
        request = payload.get("request")
        expiry = payload.get("expiresAtMs")
        if (not isinstance(request, dict) or _session(request.get("sessionKey")) != _session(self.session)
                or type(expiry) not in (int, float) or not math.isfinite(expiry)
                or expiry <= time.time() * 1000 or len(self.pending) + len(self.approval_runs) >= 1024):
            return False
        source = request.get("runId") or payload.get("runId")
        if source and source not in self.identities:
            return False
        if iid not in self.approval_runs:
            self.pending.setdefault(iid, expiry)
        self.changed.set()
        return True

    def correlate(self, event: EventFrame) -> bool:
        payload = event.payload or {}
        run = payload.get("runId")
        if _session(payload.get("sessionKey")) != _session(self.session) or not isinstance(run, str):
            return False
        if run in self.followups:
            return True
        prefix = "exec-approval-followup:"
        if not run.startswith(prefix):
            return False
        iid, marker, nonce = run[len(prefix):].partition(":nonce:")
        if iid not in self.pending or (marker and not nonce):
            return False
        self.pending.pop(iid)
        self.approval_runs[iid] = run
        part = ChatDispatch(self.client, self.session, run, "")
        part.sent = True
        self.followups[run] = part
        self.identities.add(run)
        if self.watching:
            self._start_part(part)
        self.changed.set()
        return True

    def hold(self, event: EventFrame) -> bool:
        run = (event.payload or {}).get("runId")
        part = self.followups.get(run)
        return part.hold(event) if part else super().hold(event)

    def _collect(self, event: EventFrame) -> None:
        payload = event.payload or {}
        if event.event == "agent" and payload.get("stream") == "lifecycle":
            self.ends[payload["runId"]] = event
        if event.event == "chat" and payload.get("state") in ("final", "error", "aborted"):
            self.results[payload["runId"]] = event
            self.changed.set()

    def _start_part(self, part: ChatDispatch) -> None:
        self.monitors.append(asyncio.create_task(part.watch(self._collect, self.budget)))

    async def watch(self, publish: Callable[[EventFrame], None], timeout: float) -> None:
        self.budget = timeout
        self.watching = True
        # Use the original dispatch monitor, but withhold its intermediate final.
        self.monitors.append(asyncio.create_task(super().watch(self._collect, timeout)))
        for part in self.followups.values():
            self._start_part(part)
        success = False
        try:
            async with asyncio.timeout(timeout):
                while True:
                    self.changed.clear()
                    failed = next((event for event in self.results.values()
                                   if event.payload.get("state") != "final"), None)
                    if failed is not None:
                        publish(failed)
                        return
                    if not self.pending and self.run in self.results and all(
                            run in self.results for run in self.followups):
                        last_run = next(reversed(self.followups)) if self.followups else self.run
                        if last_run in self.ends:
                            publish(self.ends[last_run])
                        publish(self.results[last_run])
                        success = True
                        return
                    await self.changed.wait()
        except TimeoutError:
            publish(EventFrame(event="chat", payload={
                "runId": self.run, "sessionKey": self.session, "state": "error",
                "errorCode": "EXEC_APPROVAL_CONTINUATION_UNCONFIRMED",
                "errorMessage": "Exec approval or its continuation was not confirmed; do not automatically retry.",
            }))
        finally:
            self.watching = False
            for task in self.monitors:
                task.cancel()
            await asyncio.gather(*self.monitors, return_exceptions=True)
            self.confirmed = success or (not self.pending and self.confirmed
                                         and all(part.confirmed for part in self.followups.values()))
            self.finished.set()
