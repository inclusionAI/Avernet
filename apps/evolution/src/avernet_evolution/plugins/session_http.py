"""Bounded, redacted real session snapshots over the public operator API.

This is not the complete 02 event pipeline: no historical revision attribution,
feedback, costs, outcome inference or completeness claim. Bodies are durable
private artifacts, not log lines. Each episode's id addresses its redacted bytes.
"""

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from .experience_redaction import redact_text
from .platform_http import PlatformHttpError


class HttpSessionExperience:
    def __init__(self, http, path, *, stage: str, session_limit: int = 20, message_limit: int = 100):
        if stage not in ("draft", "online") or not 1 <= session_limit <= 20 or not 1 <= message_limit <= 100:
            raise ValueError("invalid session snapshot bounds")
        self.http, self.stage = http, stage
        self.session_limit, self.message_limit = session_limit, message_limit
        self.conn = sqlite3.connect(str(path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS session_snapshot "
            "(scope TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL, "
            "PRIMARY KEY(scope,id))"
        )
        self.conn.commit()

    def for_bot(self, bot):
        return BoundSessions(self, bot)

    def close(self):
        self.conn.close()


class BoundSessions:
    def __init__(self, factory, bot):
        self.factory, self.bot = factory, bot

    async def sessions(self, *, days, limit=500, revision=None):
        if not isinstance(days, int) or not 1 <= days <= 365 or not 1 <= limit <= 500:
            raise ValueError("invalid experience bounds")
        if revision is not None:
            # Session APIs cannot attest historical Genome identity. Do not
            # falsely label observations with the run's frozen parent revision.
            return []
        f, bot = self.factory, self.bot
        page = await f.http.request(
            bot,
            "GET",
            "/sessions",
            params={
                "stage": f.stage,
                "page": 1,
                "page_size": min(limit, f.session_limit),
            },
        )
        cutoff, now = datetime.now(UTC) - timedelta(days=days), datetime.now(UTC)
        episodes = []
        for session in page["items"][: min(limit, f.session_limit)]:
            sid = session["session_id"]
            stamp = session["gmt_modified"] or session["gmt_create"]
            try:
                modified = datetime.fromisoformat(stamp)
                if modified.tzinfo is None:
                    raise ValueError("missing timezone")
            except (ValueError, TypeError):
                raise PlatformHttpError("session timestamp cannot be attributed to query window") from None
            if modified < cutoff or modified > now + timedelta(minutes=5):
                continue
            messages = await f.http.request(
                bot,
                "GET",
                "/sessions/" + quote(sid, safe="") + "/messages",
                params={"stage": f.stage, "page": 1, "page_size": f.message_limit},
            )
            normalized = []
            for message in messages["items"][: f.message_limit]:
                if message["session_id"] != sid:
                    raise PlatformHttpError("session message identity mismatch")
                normalized.append(
                    {
                        "role": message["role"],
                        "content": redact_text(message["content"]),
                        "at": message["gmt_create"],
                    }
                )
            episode = {
                "source": "public-session-api/snapshot@1",
                "session_id": sid,
                "bot_id": bot.bot_id,
                "owner_id": bot.owner_id,
                "stage": f.stage,
                "revision": None,
                "history_complete": False,
                "messages": normalized,
                "observed_modified_at": modified.isoformat(),
                "bounds": {"sessions": f.session_limit, "messages": f.message_limit, "pages": 1},
            }
            body = json.dumps(episode, sort_keys=True, ensure_ascii=False)
            if len(body.encode()) > 2 * 1024 * 1024:
                raise PlatformHttpError("session snapshot exceeds bound")
            episode["episode_id"] = "ep_" + hashlib.sha256(body.encode()).hexdigest()
            scope = json.dumps([bot.entity_id, bot.bot_id, bot.owner_id, bot.engine_type, f.stage])
            # Persist before returning; write failures must escape, not become an
            # empty experience result. Body is normalized and contains no metadata.
            with f.conn:
                f.conn.execute(
                    "INSERT OR IGNORE INTO session_snapshot VALUES (?, ?, ?)",
                    (scope, episode["episode_id"], json.dumps(episode, ensure_ascii=False)),
                )
            episodes.append(episode)
        return episodes

    async def feedback(self, **kwargs):
        raise NotImplementedError("public session snapshots do not provide feedback")
