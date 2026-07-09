"""bot_chat e2e business flows (real endpoints, LOCAL+SQLITE, no mocks for DB source).

BOT_CHAT_LIFECYCLE_FLOWS (tests/_flows/bot_chat/api_lifecycle.py) is the single
source of truth for this runner and the E3 coverage guard.

bot_chat is read-only; we seed aw_langfuse_traces + ac_bots rows directly via
the DatabasePlugin session BEFORE flows that expect data run. The empty flow
runs with no seed, proving the API returns truly empty (not seed leaking from
a prior test).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from agentclaw.community.core.bot_chat.models import Base as _BotChatBase
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from tests.community._flows.bot_chat.api_lifecycle import BOT_CHAT_LIFECYCLE_FLOWS
from tests.community.framework.flow_runner import run_flow


def _ensure_bot_chat_tables(world) -> None:
    """Materialize bot_chat's private-Base tables on the per-test engine.

    bot_chat declares aw_langfuse_traces (and a duplicate private AcBot) on a
    PRIVATE Base in core/bot_chat/models.py; the per-test fixture's
    create_all() only covers the canonical Base. SqliteDB.bootstrap() would
    create both, but FastAPI lifespan only fires under `with TestClient(app)`
    and the flow runner uses bare TestClient. So we run private-Base
    create_all() here, idempotently (skips already-existing tables). MUST
    run for every flow (including the empty one) — list queries hit the table
    even with no rows.
    """
    plugin = world.get(DatabasePlugin)
    with plugin.session() as s:
        _BotChatBase.metadata.create_all(s.get_bind())


def _seed_trace_and_bot(world, *, owner_id: str, bot_id: str, trace_id: str) -> None:
    """Seed one trace + one bot row so the flow's list/detail can find them.

    aw_langfuse_traces is seeded via raw SQL (simple flat model, only the
    private bot_chat Base declares it). ac_bots is seeded via the canonical
    BotModel ORM, because its non-null columns (engine_types, active_engine,
    status, gmt_create, ...) have Python-side defaults that raw INSERT bypasses.

    The trace's gmt_trace is set to "now" so the default 72-hour window query
    in list_sessions surfaces it. user_id matches the caller's x-user-id so
    repository.list_sessions(user_id=owner_id) filter passes. bot_id links the
    trace to the ac_bots row; the row's entity_id == owner_id satisfies the
    ownership predicate in BotChatDbRepository.owns_bot (entity_id, bot_id).
    """
    plugin = world.get(DatabasePlugin)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    with plugin.session() as s:
        # aw_langfuse_traces — bot_chat's private-Base table; columns are as
        # declared in core/bot_chat/models.py:AwLangfuseTrace.
        # Explicit id: AwLangfuseTrace.id is BigInteger which SQLite renders as
        # BIGINT (not "INTEGER PRIMARY KEY"), so autoincrement is disabled and
        # the INSERT would fail NOT NULL on id. Hardcoding id=1 is safe within
        # a per-test in-memory DB. (BotModel uses AutoIncrementBigInteger to
        # dodge this on the canonical Base; the private bot_chat Base predates
        # that workaround.)
        s.execute(text("""
            INSERT INTO aw_langfuse_traces
              (id, trace_id, gmt_trace, name, input, output, session_id,
               user_id, metadata, bot_id, device_id, real_session_id)
            VALUES (:id, :trace_id, :gmt_trace, :name, :input, :output, :session_id,
                    :user_id, :metadata, :bot_id, :device_id, :real_session_id)
        """), {
            "id": 1,
            "trace_id": trace_id,
            "gmt_trace": now_ms,
            "name": "E2E seeded trace",
            "input": json.dumps([{"role": "user", "content": "hi"}]),
            "output": json.dumps({"role": "assistant", "content": "ok"}),
            "session_id": "sess_e2e",
            "user_id": owner_id,
            "metadata": json.dumps({"attributes": {"session_id": "sess_e2e"}}),
            "bot_id": bot_id,
            "device_id": "dev_e2e",
            "real_session_id": "sess_e2e",
        })
        # ac_bots via BotModel ORM — non-null columns (engine_types,
        # active_engine, status, gmt_create, gmt_modified, is_delete, public,
        # env) are populated by SQLAlchemy default= callables on add/flush;
        # raw INSERT would bypass them and trip NOT NULL constraints.
        # entity_id == owner_id satisfies BotChatDbRepository.owns_bot predicate.
        s.add(BotModel(
            bot_id=bot_id,
            entity_id=owner_id,
            entity_type="staff",
            creator_id=owner_id,
            owner_id=owner_id,
        ))
        s.commit()


@pytest.mark.e2e
@pytest.mark.parametrize("case", BOT_CHAT_LIFECYCLE_FLOWS, ids=lambda c: c.name)
def test_bot_chat_flow(case, app_with_testing_modules, world):
    # Materialize private-Base tables for every flow (list queries hit them
    # even when seeding nothing). Idempotent.
    _ensure_bot_chat_tables(world)
    # Seed for non-empty flows. The empty flow doesn't seed — proves the API
    # returns truly empty (not seed leaking from a prior test). Per-test
    # injector + per-test SQLite engine (fixtures.py) means no cross-test bleed.
    if case.name != "bot_chat-list-empty":
        _seed_trace_and_bot(
            world, owner_id="e2e_user", bot_id="bot_e2e", trace_id="trace_e2e_001",
        )
    ctx = run_flow(case, app_with_testing_modules, world)
    assert ctx is not None
    # Belt-and-suspenders: when extract keys are present, the trace_id observed
    # must equal the seeded value. The chain Flow2.extract → Flow3.path is
    # already proven by the runner (broken extract would KeyError, not 200).
    if "first_trace_id" in ctx:
        assert ctx["first_trace_id"] == "trace_e2e_001"
    if "trace_id_for_detail" in ctx:
        assert ctx["trace_id_for_detail"] == "trace_e2e_001"
