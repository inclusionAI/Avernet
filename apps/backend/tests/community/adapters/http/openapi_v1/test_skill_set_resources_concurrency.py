"""A slow resource read must not block other work on the event loop."""

import asyncio
import threading
from contextvars import ContextVar

import pytest
from starlette.requests import Request

from agentclaw.community.adapters.http.openapi_v1.skill_sets.router import resources


@pytest.mark.asyncio
async def test_resources_keeps_event_loop_available_during_sync_read():
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    tenant = ContextVar("tenant", default="missing")
    tenant.set("request-tenant")
    observed_tenants = []

    class SlowService:
        def list_resources(self, **kwargs):
            observed_tenants.append(tenant.get())
            loop.call_soon_threadsafe(started.set)
            release.wait(timeout=3)
            return []

    request = Request({"type": "http", "headers": [], "method": "GET", "path": "/"})
    task = asyncio.create_task(
        resources(
            bot_id="bot-1",
            owner_id="owner",
            user_id="actor",
            request=request,
            service=SlowService(),
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        assert not task.done(), "synchronous resource read blocked the event loop"
    finally:
        release.set()
        result = await task
    assert result.data == []
    assert observed_tenants == ["request-tenant"]
