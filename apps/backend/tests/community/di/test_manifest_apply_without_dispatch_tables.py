"""Backend assembly on a newly bootstrapped schema."""

import pytest
from sqlalchemy import inspect

from agentclaw.community.core.apply_engine.run.service import ApplyRunService
from agentclaw.community.core.mcp.scoped_config_flow import MCPScopedConfigService
from agentclaw.community.core.task_queue.services.worker import TaskWorker
from agentclaw.community.plugin_api.database import DatabasePlugin


@pytest.mark.asyncio
async def test_backend_bootstraps_without_manifest_dispatch_tables(test_injector):
    database = test_injector.get(DatabasePlugin)
    await database.bootstrap()
    with database.orm_session() as session:
        tables = set(inspect(session.get_bind()).get_table_names())
    assert "ac_bot_config_manifest_apply" in tables
    assert tables.isdisjoint({"ac_bot_capability_projection", "ac_local_skill_package_dispatch"})
    assert test_injector.get(ApplyRunService)
    assert test_injector.get(MCPScopedConfigService)
    assert await test_injector.get(TaskWorker).run_once() == 0
