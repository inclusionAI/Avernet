"""Verify a steered sibling delivery in the local OpenClaw parent transcript."""

from __future__ import annotations

import asyncio
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from engine.community.openclaw.protocol import EventFrame


def text_of(message: dict) -> str:
    return "".join(
        b["text"] for b in message.get("content", [])
        if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
    ).strip()


@dataclass(frozen=True)
class MergedAnnounce:
    event: EventFrame
    revision: int
    source: str
    sibling: str
    sibling_run: str

    async def verify_persisted(self, directory: str, session_id: str) -> bool:
        # A final can race the transcript append. Time alone never releases it.
        for attempt in range(2):
            try:
                if await asyncio.to_thread(self.confirmed, directory, session_id):
                    return True
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            if attempt == 0:
                await asyncio.sleep(0.1)
        return False

    def confirmed(self, directory: str, session_id: str) -> bool:
        # Only a regular transcript directly under the configured directory.
        if not isinstance(session_id, str) or str(UUID(session_id)) != session_id:
            return False
        path = Path(directory).expanduser() / f"{session_id}.jsonl"
        with open(path, "rb", opener=lambda p, f: os.open(p, f | os.O_NOFOLLOW | os.O_NONBLOCK)) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                return False
            start = max(0, info.st_size - 512 * 1024)
            stream.seek(start)
            lines = stream.read(512 * 1024).splitlines()[int(start > 0):]
        if len(lines) < 3:
            return False
        reply = json.loads(lines[-1])
        msg = reply["message"]
        meta = msg.get("historyMeta") or {}
        p = self.event.payload or {}
        text = text_of(msg)
        if not (
            msg.get("role") == "assistant" and msg.get("stopReason") == "stop"
            and text and text == text_of(p.get("message") or {})
            and meta.get("conversationRoundId") and meta.get("sessionKey") == p.get("sessionKey")
        ):
            return False
        parent = reply.get("parentId")
        ancestors = (json.loads(line) for line in reversed(lines[:-1]))
        for row in ancestors:
            if not parent or row.get("id") != parent:
                continue
            parent = row.get("parentId")
            n = row.get("message") or {}
            nm = n.get("historyMeta") or {}
            if (nm.get("conversationRoundId"), nm.get("sessionKey")) != (
                meta["conversationRoundId"], meta["sessionKey"],
            ):
                return False
            if n.get("role") == "user":
                break
            if not (n.get("role") == "toolResult" or
                    (n.get("role") == "assistant" and n.get("stopReason") == "toolUse")):
                return False
        else:
            return False
        origin = n.get("provenance") or {}
        if (
            origin.get("kind") != "inter_session"
            or origin.get("sourceTool") != "subagent_announce"
            or origin.get("sourceSessionKey") != self.source
        ):
            return False
        # The outer runtime envelope identifies B; nested child output never does.
        body = text_of(n)
        prefix = (
            "<<<BEGIN_OPENCLAW_INTERNAL_CONTEXT>>>\nOpenClaw runtime context (internal):\n"
            "This context is runtime-generated, not user-authored. Keep internal details private.\n\n"
            f"[Internal task completion event]\nsource: subagent\nsession_key: {self.sibling}\nsession_id: "
        )
        if not body.startswith(prefix) or not body.endswith("<<<END_OPENCLAW_INTERNAL_CONTEXT>>>"):
            return False
        session_line, _, rest = body[len(prefix):].partition("\n")
        if str(UUID(session_line)) != session_line or not rest.startswith("type: subagent task\n"):
            return False
        # Follow only this branch back to the accepted spawn for the exact B run.
        for row in ancestors:
            if not parent or row.get("id") != parent:
                continue
            parent = row.get("parentId")
            m = row.get("message") or {}
            if m.get("role") == "user" and not m.get("provenance"):
                break
            if m.get("role") == "toolResult" and m.get("toolName") == "sessions_spawn" and not m.get("isError"):
                try:
                    receipt = m.get("details") or json.loads(text_of(m))
                except ValueError:
                    continue
                if isinstance(receipt, dict) and receipt.get("status") == "accepted" and (
                    receipt.get("childSessionKey"), receipt.get("runId")
                ) == (self.sibling, self.sibling_run):
                    return True
        return False
