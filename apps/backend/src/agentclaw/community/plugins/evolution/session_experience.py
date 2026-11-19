"""Explicit acceptance-only bridge from Engine session API snapshots to RSI 02.

NOT session-export/v2: that export, historical revision attribution and complete
history detection are unavailable here. No production DI binding is installed.
The caller resolves a disposable BotRef from the authoritative Bot repository.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.parse import quote

from agentclaw.community.core.engine_runtime.engine_runtime_service_protocol import (
    EngineRuntimeRelayProtocol,
)
from agentclaw.community.core.bot_genome.models import BotRef
from agentclaw.community.core.engine_runtime.session_key import SessionKeyCodecRegistry

from .experience_redaction import redact_text

SOURCE_CONTRACT = "engine-session-api/snapshot@1"


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _time(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("session snapshot requires source timestamps")
    at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if at.tzinfo is None:
        raise ValueError("session snapshot timestamps must include a timezone")
    return at.astimezone(timezone.utc).isoformat()


class SessionSnapshotExperience:
    """Single authorized Bot, immutable sanitized snapshots in a private SQLite DB.

    ``include_draft`` is an explicit test override; default queries expose only
    service episodes. Feedback is unsupported, not a fabricated empty dataset.
    History may be bounded by Engine, even when no limit is requested. Every
    snapshot carries ``history_complete=False`` so callers cannot mistake it for
    a complete export. Cost, outcome and historical revision remain unknown.
    """

    def __init__(
        self,
        *,
        bot: BotRef,
        database: Path,
        include_draft: bool = False,
    ):
        self.bot = bot
        self.include_draft = include_draft
        self.scope = _json(asdict(bot))
        # Transcript-derived data is restricted local runtime state, not a repo asset.
        database.touch(mode=0o600, exist_ok=True)
        database.chmod(0o600)
        self.conn = sqlite3.connect(database)
        self.conn.execute("""CREATE TABLE IF NOT EXISTS session_snapshots (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL, stage TEXT NOT NULL, session_id TEXT NOT NULL,
            episode_id TEXT NOT NULL UNIQUE, started_at TEXT NOT NULL,
            body TEXT NOT NULL
        )""")
        self.reads = 0

    def close(self) -> None:
        self.conn.close()

    def for_bot(self, bot: BotRef):
        if bot != self.bot:
            raise PermissionError("experience belongs to another Bot")
        self.reads += 1
        return self

    async def ingest(
        self,
        *,
        relay: EngineRuntimeRelayProtocol,
        codecs: SessionKeyCodecRegistry,
        session_id: str,
        runtime_stage: str,
    ) -> str:
        if runtime_stage not in ("draft", "online"):
            raise ValueError("snapshot bridge supports draft or online, never verify")
        if runtime_stage == "online" and self.bot.bot_type != "service":
            raise ValueError("online experience requires a service Bot")
        if not isinstance(session_id, str) or not session_id or len(session_id) > 512:
            raise ValueError("a bounded explicit session id is required")
        facts = await relay.resolve_bot_off_loop(
            self.bot.bot_id,
            self.bot.owner_id,
            self.bot.owner_id,
        )
        if (facts.bot_id, facts.owner_id, facts.active_engine, facts.bot_type) != (
            self.bot.bot_id,
            self.bot.owner_id,
            self.bot.engine_type,
            self.bot.bot_type,
        ):
            raise PermissionError("resolved Bot no longer matches snapshot scope")
        encoded = quote(
            codecs.resolve(self.bot.engine_type).encode(session_id), safe=""
        )
        kwargs = dict(
            bot_id=self.bot.bot_id,
            owner_id=self.bot.owner_id,
            facts=facts,
            method="GET",
            stage=runtime_stage,
        )
        meta = (await relay.call(**kwargs, path=f"/api/sessions/{encoded}")).data
        # No explicit limit: OpenClaw currently bounds history upstream anyway.
        history = (
            await relay.call(
                **kwargs,
                path=f"/api/sessions/{encoded}/messages",
            )
        ).data
        if not isinstance(meta, dict) or not isinstance(history, list):
            raise ValueError("unexpected Engine session snapshot shape")
        if meta.get("id") != session_id:
            raise ValueError("Engine session identity mismatch")
        if not history or len(history) > 10000:
            raise ValueError("snapshot must contain 1..10000 messages")
        started = _time(meta.get("gmt_created"))
        ended = _time(meta.get("gmt_modified"))
        if ended < started:
            raise ValueError("session ends before it starts")
        turns = []
        text_size = 0
        for message in history:
            if (
                not isinstance(message, dict)
                or message.get("role")
                not in (
                    "user",
                    "assistant",
                    "tool",  # Legacy snapshots; current Engine uses the roles below.
                    "tool_use",
                    "tool_result",
                    "system",
                )
                or not isinstance(message.get("content"), str)
            ):
                raise ValueError("unsupported Engine message shape")
            if message.get("session_id", session_id) != session_id:
                raise ValueError("Engine message session identity mismatch")
            text_size += len(message["content"])
            if text_size > 2_000_000:
                raise ValueError("snapshot exceeds text size bound")
            turns.append(
                {"role": message["role"], "text": redact_text(message["content"])}
            )
        stage = "service" if runtime_stage == "online" else "draft"
        digest = hashlib.sha256(_json(history).encode()).hexdigest()
        episode_id = (
            "ep_"
            + hashlib.sha256(
                _json([self.scope, stage, session_id, started, ended, digest]).encode(),
            ).hexdigest()
        )
        body = {
            "episode_id": episode_id,
            "bot": asdict(self.bot),
            "revision_id": None,
            "stage": stage,
            "started_at": started,
            "ended_at": ended,
            "turns": turns,
            "outcome": {"status": "unknown", "feedback_ids": []},
            "cost": {"usd": None, "input_tokens": None, "output_tokens": None},
            "source": {
                "engine": self.bot.engine_type,
                "export_contract": SOURCE_CONTRACT,
                "session_id": session_id,
                "content_digest": digest,
                "history_complete": False,
                "redaction_policy": "snapshot-credentials-pii@1",
            },
        }
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO session_snapshots "
                "(scope,stage,session_id,episode_id,started_at,body) VALUES (?,?,?,?,?,?)",
                (self.scope, stage, session_id, episode_id, started, _json(body)),
            )
        return episode_id

    async def sessions(
        self, *, days: int, limit: int = 500, revision: str | None = None
    ):
        if type(days) is not int or not 1 <= days <= 3650:
            raise ValueError("days must be an integer in 1..3650")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer in 1..500")
        if revision is not None and (not isinstance(revision, str) or not revision):
            raise ValueError("revision must be a nonempty identifier")
        if revision is not None:
            return []  # Historical revision is honestly unknown; never use active now.
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self.conn.execute(
            "SELECT body FROM session_snapshots WHERE scope=? AND seq IN "
            "(SELECT MAX(seq) FROM session_snapshots WHERE scope=? GROUP BY stage,session_id) "
            "AND started_at>=? AND (? OR stage='service') "
            "ORDER BY started_at DESC, episode_id LIMIT ?",
            (self.scope, self.scope, since, int(self.include_draft), limit),
        ).fetchall()
        return [json.loads(row[0]) for row in rows]

    async def feedback(self, *, days: int, kinds=None, limit: int = 500):
        raise NotImplementedError(
            "feedback ingestion is not supported by session snapshots"
        )
