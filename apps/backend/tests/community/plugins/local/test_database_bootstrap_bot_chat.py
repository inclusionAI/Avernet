"""Pin LOCAL SQLite bootstrap creates bot_chat private-Base tables.

Without this, bot_chat list/get endpoints fail with 'no such table' at runtime
even though tests/core/bot_chat/* pass (those build their own engine).
"""
from __future__ import annotations

import asyncio

from sqlalchemy import inspect

from agentclaw.community.di import DeployProfile, build_injector
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugins.local import database as db_mod


def test_bootstrap_creates_bot_chat_private_base_tables():
    """SqliteDB.bootstrap() must register the bot_chat private-Base tables.

    ``core/bot_chat/models.py`` uses its own ``declarative_base`` instead
    of the canonical ``agentclaw.community.core.base.Base``. Bootstrap historically
    only called ``create_all`` on the canonical Base, so
    ``aw_langfuse_traces`` was never created — runtime list/get endpoints
    crashed with ``sqlite3.OperationalError: no such table:
    aw_langfuse_traces`` even though the unit tests under
    ``tests/core/bot_chat/`` passed because they build their own engine.
    The new AC OTEL tables live on the same private Base and must be
    created through the same path for sandbox/local OTEL ingest.
    """
    injector = build_injector(profile=DeployProfile.TEST)
    plugin = injector.get(DatabasePlugin)

    # Trigger engine creation, then run the lifecycle bootstrap hook the
    # same way ``adapters/http/app.py`` does at app startup.
    with plugin.session() as _s:
        pass
    asyncio.run(plugin.bootstrap())

    engine = db_mod._engine
    assert engine is not None
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    assert "aw_langfuse_traces" in tables, (
        f"aw_langfuse_traces missing from LOCAL SQLite bootstrap; "
        f"tables present: {sorted(tables)[:30]}..."
    )
    assert "aw_langfuse_observation" in tables, (
        f"aw_langfuse_observation missing from LOCAL SQLite bootstrap; "
        f"tables present: {sorted(tables)[:30]}..."
    )
    assert "ac_otel_log_trace" in tables, (
        f"ac_otel_log_trace missing from LOCAL SQLite bootstrap; "
        f"tables present: {sorted(tables)[:30]}..."
    )
    assert "ac_otel_log_observation" in tables, (
        f"ac_otel_log_observation missing from LOCAL SQLite bootstrap; "
        f"tables present: {sorted(tables)[:30]}..."
    )
    # ac_bots must remain present (BotModel, canonical Base).
    assert "ac_bots" in tables, (
        f"ac_bots missing from LOCAL SQLite bootstrap; "
        f"tables present: {sorted(tables)[:30]}..."
    )
