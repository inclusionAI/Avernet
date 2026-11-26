"""Acceptance bridge contract: real consumer shape, no invented v2 guarantees."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from unittest.mock import AsyncMock

import pytest

from agentclaw.community.api.engine_runtime_service import BotFacts, EngineResult
from agentclaw.community.core.bot_genome.models import BotRef
from agentclaw.community.core.engine_runtime.session_key import (
    PassThroughSessionKeyCodec,
    SessionKeyCodecRegistry,
)
from agentclaw.community.plugins.evolution.session_experience import (
    SessionSnapshotExperience,
    SOURCE_CONTRACT,
)
from agentclaw.community.plugins.evolution.experience_redaction import redact_text

BOT = BotRef("entity", "bot", "owner", "openclaw", "personal")


@pytest.fixture
def bridge(tmp_path):
    provider = SessionSnapshotExperience(bot=BOT, database=tmp_path / "experience.db")
    yield provider
    provider.close()


def source(*, started=None, content="Keep answers short", bot=BOT):
    at = started or datetime.now(timezone.utc).isoformat()
    relay = AsyncMock()
    relay.resolve_bot_off_loop.return_value = BotFacts(
        bot.bot_id,
        bot.bot_type,
        bot.engine_type,
        bot.owner_id,
    )
    relay.call.side_effect = [
        EngineResult(
            {"id": "session:test:user:owner", "gmt_created": at, "gmt_modified": at}
        ),
        EngineResult(
            [
                {
                    "role": "user",
                    "content": content,
                    "metadata": {"private": "not persisted"},
                }
            ]
        ),
    ]
    return relay


async def ingest(bridge, relay):
    return await bridge.ingest(
        relay=relay,
        codecs=SessionKeyCodecRegistry(PassThroughSessionKeyCodec()),
        session_id="session:test:user:owner",
        runtime_stage="draft",
    )


@pytest.mark.asyncio
async def test_real_snapshot_query_and_restart_are_scoped_redacted_idempotent(
    bridge, tmp_path
):
    at = datetime.now(timezone.utc).isoformat()
    email = "person" + chr(64) + "example.org"
    text = f"email me at {email}; password=do-not-store-this"
    episode = await ingest(bridge, source(started=at, content=text))
    assert episode == await ingest(bridge, source(started=at, content=text))
    assert (
        bridge.conn.execute("SELECT COUNT(*) FROM session_snapshots").fetchone()[0] == 1
    )
    assert await bridge.sessions(days=7) == []  # draft is not online experience
    bridge.include_draft = True
    records = await bridge.for_bot(BOT).sessions(days=7)
    record = records[0]
    assert record["episode_id"] == episode
    assert record["revision_id"] is None
    assert record["stage"] == "draft"
    assert record["source"]["export_contract"] == SOURCE_CONTRACT
    assert record["source"]["history_complete"] is False
    assert email not in json.dumps(record)
    assert "do-not-store-this" not in json.dumps(record)
    assert "metadata" not in json.dumps(record)
    assert await bridge.sessions(days=7, revision="current-active-is-not-history") == []
    disk = (tmp_path / "experience.db").read_bytes()
    assert b"do-not-store-this" not in disk and email.encode() not in disk
    reopened = SessionSnapshotExperience(
        bot=BOT, database=tmp_path / "experience.db", include_draft=True
    )
    try:
        assert await reopened.sessions(days=7) == records
    finally:
        reopened.close()
    assert (tmp_path / "experience.db").stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_revised_session_has_immutable_evidence_and_latest_snapshot_only(bridge):
    bridge.include_draft = True
    at = datetime.now(timezone.utc).isoformat()
    old = await ingest(bridge, source(started=at, content="old"))
    new = await ingest(bridge, source(started=at, content="new"))
    assert old != new
    assert (
        bridge.conn.execute("SELECT COUNT(*) FROM session_snapshots").fetchone()[0] == 2
    )
    assert [e["episode_id"] for e in await bridge.sessions(days=7)] == [new]
    # Retry an older immutable snapshot must not make it the latest snapshot.
    await ingest(bridge, source(started=at, content="old"))
    assert [e["episode_id"] for e in await bridge.sessions(days=7)] == [new]


@pytest.mark.parametrize(
    "field", ["bot_id", "entity_id", "owner_id", "entity_type", "engine_type"]
)
def test_factory_refuses_other_bot_identity_before_read(bridge, field):
    with pytest.raises(PermissionError):
        bridge.for_bot(replace(BOT, **{field: "another"}))


@pytest.mark.asyncio
async def test_persisted_database_also_scopes_entity_and_owner(bridge, tmp_path):
    await ingest(bridge, source())
    other = SessionSnapshotExperience(
        bot=replace(BOT, entity_id="another"),
        database=tmp_path / "experience.db",
        include_draft=True,
    )
    try:
        assert await other.sessions(days=7) == []
    finally:
        other.close()


@pytest.mark.asyncio
async def test_resolved_facts_must_match_before_reading_messages(bridge):
    relay = source(bot=replace(BOT, owner_id="another"))
    with pytest.raises(PermissionError):
        await ingest(bridge, relay)
    relay.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_failure_and_provider_failure_propagate(bridge):
    bridge.conn.execute("PRAGMA query_only = ON")
    with pytest.raises(Exception, match="readonly"):
        await ingest(bridge, source())
    relay = source()
    relay.call.side_effect = RuntimeError("engine unavailable")
    with pytest.raises(RuntimeError, match="engine unavailable"):
        await ingest(bridge, relay)


@pytest.mark.asyncio
async def test_unknown_feedback_is_not_reported_as_empty(bridge):
    with pytest.raises(NotImplementedError):
        await bridge.feedback(days=7)


@pytest.mark.asyncio
@pytest.mark.parametrize("days,limit", [(0, 1), (True, 1), (1, 0), (1, 501)])
async def test_query_bounds(bridge, days, limit):
    with pytest.raises(ValueError):
        await bridge.sessions(days=days, limit=limit)


@pytest.mark.asyncio
async def test_timestamp_window_and_timezone_validation(bridge):
    bridge.include_draft = True
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    await ingest(bridge, source(started=old))
    assert await bridge.sessions(days=7) == []
    assert len(await bridge.sessions(days=9)) == 1
    with pytest.raises(ValueError, match="timezone"):
        await ingest(bridge, source(started="2026-11-17T01:00:00"))


def test_redaction_covers_all_hook_known_families_and_private_key_body():
    # Synthetic values built at runtime, never checked-in usable credentials.
    values = [
        "LTAI" + "a" * 20,
        "STS." + "a" * 20,
        "CAIS" + "a" * 25,
        "AKIA" + "A" * 16,
        "ghp_" + "a" * 25,
        "github_pat_" + "a" * 25,
        "glpat-" + "a" * 25,
        "xoxb-" + "a" * 20,
        "AIza" + "a" * 31,
        "sk-" + "a" * 25,
        "sk-proj-" + "a" * 25,
        "sk-ant-api03-" + "a" * 25,
        "npm_" + "a" * 25,
        "pypi-" + "a" * 25,
        "eyJ" + "a" * 10 + "." + "b" * 10 + "." + "c" * 10,
        "Bearer " + "a" * 25,
        "aliyunAccessKeySecret='do-not-store'",
        "person" + chr(64) + "example.org",
        "13800138000",
    ]
    for value in values:
        assert value not in redact_text(value)
    pem = "-----BEGIN " + "PRIVATE KEY-----\nprivate-material\n-----END PRIVATE KEY-----"
    assert "private-material" not in redact_text(pem)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["verify", "unknown", "online"])
async def test_personal_bot_rejects_unsupported_stage_before_engine_read(bridge, stage):
    relay = source()
    with pytest.raises(ValueError):
        await bridge.ingest(
            relay=relay,
            codecs=SessionKeyCodecRegistry(PassThroughSessionKeyCodec()),
            session_id="session:test:user:owner",
            runtime_stage=stage,
        )
    relay.resolve_bot_off_loop.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_online_is_queryable_by_default(tmp_path):
    bot = replace(BOT, bot_type="service")
    provider = SessionSnapshotExperience(bot=bot, database=tmp_path / "service.db")
    try:
        episode = await provider.ingest(
            relay=source(bot=bot),
            codecs=SessionKeyCodecRegistry(PassThroughSessionKeyCodec()),
            session_id="session:test:user:owner",
            runtime_stage="online",
        )
        records = await provider.sessions(days=7)
        assert [r["episode_id"] for r in records] == [episode]
        assert records[0]["stage"] == "service"
    finally:
        provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("part", ["metadata", "message"])
async def test_mismatched_session_response_is_not_persisted(bridge, part):
    relay = source()
    replies = list(relay.call.side_effect)
    if part == "metadata":
        replies[0].data["id"] = "other-session"
    else:
        replies[1].data[0]["session_id"] = "other-session"
    relay.call.side_effect = replies
    with pytest.raises(ValueError, match="session identity"):
        await ingest(bridge, relay)
    assert (
        bridge.conn.execute("SELECT COUNT(*) FROM session_snapshots").fetchone()[0] == 0
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role", ["user", "assistant", "system", "tool", "tool_use", "tool_result"]
)
async def test_engine_message_roles_preserved_with_sanitized_text(bridge, role):
    relay = source(content="password=do-not-store")
    replies = list(relay.call.side_effect)
    replies[1].data[0]["role"] = role
    relay.call.side_effect = replies
    await ingest(bridge, relay)
    bridge.include_draft = True
    turns = (await bridge.sessions(days=7))[0]["turns"]
    assert turns[0]["role"] == role
    assert "do-not-store" not in turns[0]["text"]
    assert set(turns[0]) == {"role", "text"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,content", [("unknown", "text"), ("tool_result", {"text": "nested"})]
)
async def test_unsupported_message_shape_fails_without_partial_snapshot(
    bridge, role, content
):
    relay = source()
    replies = list(relay.call.side_effect)
    replies[1].data.append({"role": role, "content": content})
    relay.call.side_effect = replies
    with pytest.raises(ValueError, match="unsupported Engine message shape"):
        await ingest(bridge, relay)
    assert (
        bridge.conn.execute("SELECT COUNT(*) FROM session_snapshots").fetchone()[0] == 0
    )
