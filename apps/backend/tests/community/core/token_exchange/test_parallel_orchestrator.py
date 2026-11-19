"""Concurrency is verified with barriers rather than elapsed-time assertions."""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.caller_identity.contracts import CallerIamTokenOutcome, CallerIdentityStage
from agentclaw.community.core.token_exchange import TokenExchangeOrchestrator, TokenPluginPipeline
from agentclaw.community.plugin_api.auth import AuthRequestContext
from tests.community.core.token_exchange.test_pipeline import _Appender, _Plugin, _context


@pytest.fixture
def setup():
    caller = SimpleNamespace(get_iam_token=AsyncMock(return_value=CallerIamTokenOutcome(iam_token="original")))
    pipeline = SimpleNamespace(run=AsyncMock(return_value=[]))
    bots = MagicMock()
    bots.get_by_id_and_entity.return_value = {"owner_id": "owner", "bot_type": "service", "env": "pre"}
    auth = SimpleNamespace(resolve_user_from_request=AsyncMock(return_value=SimpleNamespace(staffId="actor")))
    service = TokenExchangeOrchestrator(
        caller_service=caller, token_pipeline=pipeline, bot_repository=bots, auth_plugin=auth,
    )
    request = dict(
        iam_token="original", auth_request=AuthRequestContext(), bot_id="bot",
        stage=CallerIdentityStage.ONLINE, publish_id=12, entity_id="entity", is_test_exchange=False,
    )
    return service, caller, pipeline, bots, auth, request


@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["caller", "plugin"])
@pytest.mark.parametrize("caller_fails", [False, True])
async def test_branches_overlap_and_both_finish_before_response(setup, first, caller_fails):
    service, caller, pipeline, _, _, request = setup
    entered = {name: asyncio.Event() for name in ("caller", "plugin")}
    release = {name: asyncio.Event() for name in entered}
    finished = {name: asyncio.Event() for name in entered}
    error = RuntimeError("caller unavailable")
    outcome = CallerIamTokenOutcome(iam_token="unchanged")

    async def run(name):
        entered[name].set()
        await release[name].wait()
        finished[name].set()
        if name == "caller":
            if caller_fails:
                raise error
            return outcome
        return []

    async def run_caller(**kwargs):
        return await run("caller")

    async def run_plugin(context):
        return await run("plugin")

    caller.get_iam_token.side_effect = run_caller
    pipeline.run.side_effect = run_plugin
    task = asyncio.create_task(service.get_iam_token(**request))
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered.values())), 5)
        release[first].set()
        await asyncio.wait_for(finished[first].wait(), 5)
        assert not task.done(), "response must wait for the other branch"
        for event in release.values():
            event.set()
        if caller_fails:
            with pytest.raises(RuntimeError) as raised:
                await task
            assert raised.value is error
        else:
            assert await task is outcome
    finally:
        for event in release.values():
            event.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_request_cancellation_joins_both_branches(setup):
    service, caller, pipeline, _, _, request = setup
    entered = [asyncio.Event(), asyncio.Event()]
    cancelled = [asyncio.Event(), asyncio.Event()]

    async def block(index):
        entered[index].set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled[index].set()

    async def run_caller(**kwargs):
        await block(0)

    async def run_plugin(context):
        await block(1)

    caller.get_iam_token.side_effect = run_caller
    pipeline.run.side_effect = run_plugin
    task = asyncio.create_task(service.get_iam_token(**request))
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered)), 5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert all(event.is_set() for event in cancelled)


@pytest.mark.asyncio
async def test_blocking_context_lookup_leaves_caller_available(setup):
    service, caller, _, bots, _, request = setup
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    bot = bots.get_by_id_and_entity.return_value

    def lookup(*args):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5), "Caller could not progress during the Bot read"
        return bot

    async def run_caller(**kwargs):
        await entered.wait()
        release.set()
        return CallerIamTokenOutcome(iam_token="original")

    bots.get_by_id_and_entity.side_effect = lookup
    caller.get_iam_token.side_effect = run_caller
    try:
        result = await asyncio.wait_for(service.get_iam_token(**request), 10)
    finally:
        release.set()
    assert result.iam_token == "original"


@pytest.mark.asyncio
async def test_blocking_append_leaves_event_loop_available():
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    plugin = _Plugin([])
    append = plugin.append_to_outbound

    def blocking_append(**kwargs):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5), "event loop could not progress during append"
        append(**kwargs)

    plugin.append_to_outbound = blocking_append
    task = asyncio.create_task(TokenPluginPipeline(
        plugins=(plugin,), outbound_appender=_Appender(),
    ).run(_context()))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert not task.done()
    finally:
        release.set()
        results = await task
    assert results[0].status == "succeeded"
