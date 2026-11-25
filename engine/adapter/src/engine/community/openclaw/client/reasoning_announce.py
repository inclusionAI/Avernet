"""Recover one yielded child's reasoning-only announce, never from unrelated history."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
from pathlib import Path
from uuid import UUID

from engine.community.openclaw.protocol import ErrorCodes, EventFrame

from .merged_announce import text_of

_MAX_BYTES = 512 * 1024
RETRY_TIMEOUT = 120.0
log = logging.getLogger("openclaw-client")


def empty_announce(payload: dict) -> bool:
    msg = payload.get("message") or {}
    if not isinstance(msg, dict) or msg.get("text") or payload.get("text"):
        return False
    blocks = msg.get("content", [])
    return isinstance(blocks, list) and all(
        isinstance(b, dict) and b.get("type") in ("thinking", "reasoning")
        for b in blocks
    )


class ReasoningAnnounce:
    def __init__(self, event: EventFrame, child_run: str, child_key: str):
        self.event = event
        self.run = event.payload["runId"]
        self.parent = event.payload["sessionKey"]
        self.child_run = child_run
        self.child_key = child_key
        self.text = ""
        self.end = asyncio.Event()
        self.failed = False

    def observe(self, event: EventFrame) -> None:
        p = event.payload or {}
        # The Gateway can omit the session key on the retry. Such frames may
        # corroborate this already bound run, but cannot themselves be forwarded.
        if (event.event != "agent" or p.get("runId") != self.run
                or (p.get("sessionKey") and p["sessionKey"] != self.parent)):
            return
        data = p.get("data") or {}
        if not isinstance(data, dict):
            return
        stream, phase = p.get("stream"), data.get("phase")
        if stream == "assistant" and isinstance(data.get("text"), str):
            self.text = data["text"].strip()
            self.end.clear()
        elif stream == "lifecycle" and phase == "end" and self.text:
            self.end.set()
        elif stream == "lifecycle" and phase == "error":
            self.failed = True
            self.end.set()
        elif stream in ("tool", "compaction") or (stream == "lifecycle" and phase == "start"):
            self.text = ""
            self.end.clear()

    def inspect(self, directory: str, sid: str, anchor: str = "") -> str:
        """Identify the exact stopped thinking row, or verify its direct reply."""
        if not isinstance(sid, str) or str(UUID(sid)) != sid:
            return ""
        path = Path(directory).expanduser() / f"{sid}.jsonl"
        with open(path, "rb", opener=lambda p, f: os.open(p, f | os.O_NOFOLLOW | os.O_NONBLOCK)) as f:
            info = os.fstat(f.fileno())
            if not stat.S_ISREG(info.st_mode):
                return ""
            start = max(0, info.st_size - _MAX_BYTES)
            header = json.loads(f.readline(8192)) if start else None
            f.seek(start)
            lines = f.read(_MAX_BYTES).splitlines()[int(start > 0):]
        rows = [json.loads(line) for line in lines]
        by_id = {r["id"]: r for r in rows if r.get("id")}
        if not rows:
            return ""
        row = rows[-1]
        msg = row.get("message") or {}
        header = header if start else rows[0]
        header_matches = header.get("type") == "session" and header.get("id") == sid

        def same_assistant(m):
            # Stock OpenClaw doesn't write the enterprise historyMeta marker.
            # sessions.describe binds the parent key to sid; require that exact
            # file header when the message itself doesn't attest the key.
            key = (m.get("historyMeta") or {}).get("sessionKey")
            return (m.get("role") == "assistant" and m.get("stopReason") == "stop"
                    and (key == self.parent or (key is None and header_matches)))

        def thinking_only(m):
            return same_assistant(m) and m.get("content") and all(b.get("type") == "thinking" for b in m["content"])

        if anchor:
            if not same_assistant(msg):
                return ""
            blocks = msg.get("content") or []
            if not all(b.get("type") in ("text", "thinking") for b in blocks):
                return ""
            # OpenClaw may use both retries. Never cross a tool, user turn,
            # branch change or another assistant reply to reach the anchor.
            parent = row.get("parentId")
            if parent != anchor:
                previous = by_id.get(parent, {})
                if not thinking_only(previous.get("message") or {}) or previous.get("parentId") != anchor:
                    return ""
            return text_of(msg)
        # The follow-up may already have been persisted before sessions.describe returns.
        if same_assistant(msg) and text_of(msg):
            row = by_id.get(row.get("parentId"), {})
            msg = row.get("message") or {}
        if not thinking_only(msg):
            return ""
        previous = by_id.get(row.get("parentId"), {})
        if thinking_only(previous.get("message") or {}):
            row = previous
        anchor = row["id"]
        row = by_id.get(row.get("parentId"), {})
        if row.get("customType") != "openclaw.sessions_yield":
            return ""
        # Follow the active branch, not the last receipt in the file.
        for _ in range(32):
            row = by_id.get(row.get("parentId"), {})
            m = row.get("message") or {}
            if not row or m.get("role") == "user":
                break
            if m.get("role") == "toolResult" and m.get("toolName") == "sessions_spawn":
                receipt = m.get("details") or json.loads(text_of(m))
                if (not m.get("isError") and receipt.get("status") == "accepted"
                        and receipt.get("runId") == self.child_run
                        and receipt.get("childSessionKey") == self.child_key):
                    return anchor
                break
        return ""

    async def lookup(self, directory: str, sid: str, anchor: str = "") -> str:
        for attempt in range(2):
            try:
                value = await asyncio.to_thread(self.inspect, directory, sid, anchor)
                if value:
                    return value
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                pass
            if not attempt:
                await asyncio.sleep(0.1)
        return ""

    async def resolve(self, client, directory: str, timeout: float) -> EventFrame:
        payload = dict(self.event.payload)
        anchor = ""
        try:
            response = await client.send_request("sessions.describe", {"key": self.parent}, timeout=2)
            session = (response.payload or {}).get("session") or {}
            if response.ok and session.get("key") == self.parent:
                sid = session["sessionId"]
                anchor = await self.lookup(directory, sid)
            if anchor:
                log.info("[chat_stream][reasoning_announce_pending] runId=%s", self.run)
                await asyncio.wait_for(self.end.wait(), timeout=min(timeout, RETRY_TIMEOUT))
                text = await self.lookup(directory, sid, anchor)
                if self.failed or not (text and self.end.is_set() and text == self.text):
                    raise ValueError("unverified continuation")
                if text != "NO_REPLY":
                    payload["message"] = {"role": "assistant", "content": [{"type": "text", "text": text}]}
                log.info("[chat_stream][reasoning_announce_verified] runId=%s", self.run)
        except Exception as exc:
            log.info("[chat_stream][reasoning_announce_unverified] confirmed=%s reason=%s",
                     bool(anchor), type(exc).__name__)
            if anchor:
                payload.update(state="error", errorMessage="OpenClaw continuation not confirmed",
                               errorCode=ErrorCodes.AGENT_TIMEOUT if isinstance(exc, TimeoutError)
                               else ErrorCodes.UNAVAILABLE)
        return EventFrame(event="chat", payload=payload)
