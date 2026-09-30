"""Ensure LOCAL SQLite bootstrap registers the frontend user-list table."""

from __future__ import annotations

import asyncio

from sqlalchemy import inspect

from agentclaw.community.di import DeployProfile, build_injector
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugins.local import database as db_mod


def test_bootstrap_creates_entity_user_list_table():
    """The read-only rollout check must not fail on an unregistered table."""
    injector = build_injector(profile=DeployProfile.TEST)
    plugin = injector.get(DatabasePlugin)

    with plugin.session() as _session:
        pass
    asyncio.run(plugin.bootstrap())

    engine = db_mod._engine
    assert engine is not None
    assert "ac_entity_user_list" in inspect(engine).get_table_names()
