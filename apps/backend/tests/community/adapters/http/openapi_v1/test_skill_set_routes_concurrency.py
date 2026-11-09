"""Slow SkillSet reads must not occupy the event loop."""

import asyncio
import importlib
import threading
from types import SimpleNamespace

import pytest
from starlette.requests import Request

route_module = importlib.import_module(
    "agentclaw.community.adapters.http.openapi_v1.skill_sets.router"
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "method", "extra", "result"),
    [
        ("list_skill_sets", "list_sets", {}, []),
        ("get_skill_set", "get_set", {"set_id": "1"}, {"id": "1", "name": "New"}),
        ("list_set_skills", "list_skills", {"set_id": "1"}, []),
        ("list_set_mcps", "list_mcps", {"set_id": "1"}, []),
        ("mcp_permissions", "list_mcp_permissions", {"set_id": "1"}, []),
    ],
)
async def test_slow_read_leaves_event_loop_available(handler, method, extra, result):
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()

    def slow_service(**_kwargs):
        loop.call_soon_threadsafe(started.set)
        release.wait(timeout=3)
        return result

    request = Request({"type": "http", "headers": [], "method": "GET", "path": "/"})
    task = asyncio.create_task(
        getattr(route_module, handler)(
            bot_id="bot",
            owner_id="owner",
            user_id="actor",
            request=request,
            service=SimpleNamespace(**{method: slow_service}),
            **extra,
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        assert not task.done(), f"{handler} blocked the event loop"
    finally:
        release.set()
        await task
