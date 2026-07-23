"""LOCAL SQLite 启动必须注册 Skills Pool 布局状态表。"""

from __future__ import annotations

import asyncio

from sqlalchemy import inspect

from agentclaw.community.di import DeployProfile, build_injector
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugins.local import database as db_mod


def test_bootstrap_creates_bot_skill_layout_state_table() -> None:
    injector = build_injector(profile=DeployProfile.TEST)
    plugin = injector.get(DatabasePlugin)

    with plugin.session():
        pass
    asyncio.run(plugin.bootstrap())

    engine = db_mod._engine
    assert engine is not None
    assert "ac_bot_skill_layout_state" in inspect(engine).get_table_names()
