"""Both projection clients belong to the existing worker loop, including cleanup."""
import asyncio
import threading
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_collaborator.manager_sync.dispatcher import BotManagerSyncDispatcher
from agentclaw.community.core.bot_collaborator.manager_sync.lifecycle import BotManagerSyncLifecycle
from agentclaw.community.kernel.bot_manager_sync import SubmissionResult, SyncOutcome, SyncReason
from agentclaw.community.plugins.local.bcs_managers import RecordingBcsManagersPlugin
from agentclaw.community.plugins.local.bcs_ownership import RecordingBcsOwnershipPlugin


class Managers(RecordingBcsManagersPlugin):
    def __init__(self, *, fail=False):
        super().__init__()
        self.loops = []
        self.fail = fail

    async def aclose(self):
        self.loops.append(asyncio.get_running_loop())
        if self.fail:
            raise RuntimeError("fixture close failure")


class Ownership(RecordingBcsOwnershipPlugin):
    def __init__(self, *, fail=False):
        super().__init__()
        self.loops = []
        self.fail = fail

    async def aclose(self):
        self.loops.append(asyncio.get_running_loop())
        if self.fail:
            raise RuntimeError("fixture close failure")


class Service:
    def __init__(self):
        self.loops = []

    async def sync_once(self, key):
        self.loops.append(asyncio.get_running_loop())
        return SyncOutcome.CONFIRMED


def make_lifecycle(*, manager_fail=False, owner_fail=False):
    manager, owner = Managers(fail=manager_fail), Ownership(fail=owner_fail)
    managers_service, owner_service = Service(), Service()
    dispatcher = BotManagerSyncDispatcher(
        managers_service, Mock(), ownership_service=owner_service,
    )
    lifecycle = BotManagerSyncLifecycle(dispatcher, manager, ownership=owner)
    return lifecycle, dispatcher, manager, owner, managers_service, owner_service


def assert_closed(lifecycle, dispatcher, manager, owner):
    assert manager.loops == owner.loops and len(owner.loops) == 1
    assert owner.loops[0] is not asyncio.get_running_loop()
    assert owner.loops[0].is_closed()
    assert lifecycle._thread is None
    assert not any(t.name == "manager-sync-loop" for t in threading.enumerate())
    assert dispatcher.stats.closed and not dispatcher.stats.attached


@pytest.mark.asyncio
@pytest.mark.parametrize("manager_fail,owner_fail", [(False, False), (True, False), (False, True), (True, True)])
@pytest.mark.parametrize("separate_rounds", [False, True], ids=["coalesced", "separate"])
async def test_both_clients_close_on_delivery_loop_even_if_one_fails(
    manager_fail, owner_fail, separate_rounds, caplog,
):
    lifecycle, dispatcher, manager, owner, managers_service, owner_service = make_lifecycle(
        manager_fail=manager_fail, owner_fail=owner_fail,
    )
    args = dict(bot_pk=7, env="dev", avernet_tenant="a", reason=SyncReason.OWNER_HANDOFF)
    assert dispatcher.request_sync(**args) is SubmissionResult.QUEUED
    if not separate_rounds:
        assert dispatcher.request_ownership_sync(**args) is SubmissionResult.COALESCED
    await lifecycle.startup()
    try:
        if separate_rounds:
            assert await dispatcher.drain(5)
            assert dispatcher.request_ownership_sync(**args) is SubmissionResult.QUEUED
        assert await dispatcher.drain(5)
    finally:
        await lifecycle.shutdown()
    assert_closed(lifecycle, dispatcher, manager, owner)
    assert owner_service.loops == owner.loops
    # Confirmed ownership refreshes managers even if their initial round finished.
    assert managers_service.loops == owner.loops * (2 if separate_rounds else 1)
    assert ("worker_loop_stop_failed" in caplog.text) is (manager_fail or owner_fail)
    await lifecycle.shutdown()
    assert len(owner.loops) == 1


@pytest.mark.asyncio
async def test_dispatcher_stop_failure_still_closes_both_clients(monkeypatch, caplog):
    lifecycle, dispatcher, manager, owner, _, _ = make_lifecycle()
    await lifecycle.startup()
    original = dispatcher.stop

    async def fail_after_stop():
        await original()
        raise RuntimeError("fixture stop failure")

    monkeypatch.setattr(dispatcher, "stop", fail_after_stop)
    await lifecycle.shutdown()
    assert_closed(lifecycle, dispatcher, manager, owner)
    assert "worker_loop_stop_failed" in caplog.text


@pytest.mark.asyncio
async def test_partial_attach_failure_closes_both_without_leaking_thread(monkeypatch):
    lifecycle, dispatcher, manager, owner, _, _ = make_lifecycle()
    original = dispatcher.attach

    async def fail_after_attach():
        await original()
        raise RuntimeError("fixture partial attach failure")

    monkeypatch.setattr(dispatcher, "attach", fail_after_attach)
    await lifecycle.startup()
    assert_closed(lifecycle, dispatcher, manager, owner)
    await lifecycle.shutdown()
    assert len(owner.loops) == 1


@pytest.mark.asyncio
async def test_startup_race_keeps_one_loop_and_waits_before_closing(monkeypatch):
    lifecycle, dispatcher, manager, owner, _, _ = make_lifecycle()
    original = lifecycle._start_background
    entered, release = threading.Event(), threading.Event()
    starts = []

    def gated_start():
        starts.append(1)
        entered.set()
        assert release.wait(5)
        original()

    monkeypatch.setattr(lifecycle, "_start_background", gated_start)
    startup = asyncio.create_task(lifecycle.startup())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        await lifecycle.startup()
        shutdown = asyncio.create_task(lifecycle.shutdown())
        await asyncio.sleep(0)
        assert not shutdown.done()
    finally:
        release.set()
        await startup
        await lifecycle.shutdown()
    await shutdown
    assert starts == [1]
    assert_closed(lifecycle, dispatcher, manager, owner)


@pytest.mark.asyncio
@pytest.mark.parametrize("separate_rounds", [False, True], ids=["coalesced", "separate"])
async def test_real_http_pools_are_created_and_closed_only_on_shared_worker_loop(
    monkeypatch, separate_rounds,
):
    import httpx

    from agentclaw.community.core.bot_collaborator.manager_sync.service import BotManagerSyncService
    from agentclaw.community.core.bot_management.ownership_sync.service import BotOwnershipSyncService
    from agentclaw.community.kernel.bot_manager_sync import SyncFacts, SyncKey
    from agentclaw.community.kernel.bot_ownership_sync import OwnershipFacts
    from agentclaw.community.plugins.community.bcs_managers import HttpBcsManagersPlugin
    from agentclaw.community.plugins.community.bcs_ownership import HttpBcsOwnershipPlugin

    clients, created, closed, delivered, delivered_paths = [], [], [], [], []

    def respond(request):
        delivered.append(asyncio.get_running_loop())
        delivered_paths.append(request.url.path.rsplit("/", 1)[-1])
        data = (
            {"bot_id": "bot:entity", "owner_user_id": "owner",
             "previous_owner_user_id": None, "changed": False}
            if request.url.path.endswith("/ownership")
            else {"added": 0, "removed": 0, "total": 0}
        )
        return httpx.Response(200, json={
            "code": 20000, "message": "ok", "request_id": "fixture", "data": data,
        })

    class CapturingClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            created.append(asyncio.get_running_loop())
            super().__init__(transport=httpx.MockTransport(respond), **kwargs)
            clients.append(self)

        async def aclose(self):
            closed.append(asyncio.get_running_loop())
            await super().aclose()

    monkeypatch.setattr(httpx, "AsyncClient", CapturingClient)
    kwargs = dict(base_url="http://bcs.example.test", mint_principal=lambda tenant: "fixture")
    managers, owner = HttpBcsManagersPlugin(**kwargs), HttpBcsOwnershipPlugin(**kwargs)
    key = SyncKey("a", "dev", 7)
    manager_reader, owner_reader = Mock(), Mock()
    manager_reader.read_facts.return_value = SyncFacts(key, "bot", "entity", "owner", (), bot_type="service")
    owner_reader.read_facts.return_value = OwnershipFacts(key, "bot", "entity", "owner", bot_type="service")
    dispatcher = BotManagerSyncDispatcher(
        BotManagerSyncService(manager_reader, managers), manager_reader,
        ownership_service=BotOwnershipSyncService(owner_reader, owner),
    )
    lifecycle = BotManagerSyncLifecycle(dispatcher, managers, ownership=owner)
    assert clients == []
    args = dict(bot_pk=7, env="dev", avernet_tenant="a", reason=SyncReason.OWNER_HANDOFF)
    if not separate_rounds:
        assert dispatcher.request_sync(**args) is SubmissionResult.QUEUED
        assert dispatcher.request_ownership_sync(**args) is SubmissionResult.COALESCED
    await lifecycle.startup()
    try:
        if separate_rounds:
            assert clients == []  # startup alone does not allocate an HTTP pool
            assert dispatcher.request_sync(**args) is SubmissionResult.QUEUED
            assert await dispatcher.drain(5)
            assert dispatcher.request_ownership_sync(**args) is SubmissionResult.QUEUED
        assert await dispatcher.drain(5)
    finally:
        await lifecycle.shutdown()
    assert len(clients) == 2 and all(client.is_closed for client in clients)
    assert created == closed
    assert delivered_paths == (
        ["managers", "ownership", "managers"]
        if separate_rounds else ["ownership", "managers"]
    )
    assert all(loop is created[0] for loop in delivered)
    assert created[0] is created[1] and created[0] is not asyncio.get_running_loop()
    assert created[0].is_closed()
    assert managers._client is None and owner._client is None
    assert not any(t.name == "manager-sync-loop" for t in threading.enumerate())


@pytest.mark.asyncio
async def test_shutdown_cancels_hung_ownership_round_and_closes_both(monkeypatch):
    from agentclaw.community.core.bot_collaborator.manager_sync import lifecycle as lifecycle_module

    entered, cancelled = threading.Event(), threading.Event()

    class HungOwnerService:
        async def sync_once(self, key):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    manager, owner = Managers(), Ownership()
    dispatcher = BotManagerSyncDispatcher(Service(), Mock(), ownership_service=HungOwnerService())
    lifecycle = BotManagerSyncLifecycle(dispatcher, manager, ownership=owner)
    monkeypatch.setattr(lifecycle_module, "_SHUTDOWN_DRAIN_SECONDS", 0.01)
    await lifecycle.startup()
    try:
        assert dispatcher.request_ownership_sync(
            bot_pk=7, env="dev", avernet_tenant="a", reason=SyncReason.OWNER_HANDOFF,
        ) is SubmissionResult.QUEUED
        assert await asyncio.to_thread(entered.wait, 5)
    finally:
        await asyncio.wait_for(lifecycle.shutdown(), 5)
    assert cancelled.is_set()
    assert_closed(lifecycle, dispatcher, manager, owner)


@pytest.mark.asyncio
async def test_cancelled_startup_still_waits_for_shared_loop_cleanup(monkeypatch):
    lifecycle, dispatcher, manager, owner, _, _ = make_lifecycle()
    original = lifecycle._start_background
    entered, release = threading.Event(), threading.Event()

    def gated_start():
        entered.set()
        assert release.wait(5)
        original()

    monkeypatch.setattr(lifecycle, "_start_background", gated_start)
    startup = asyncio.create_task(lifecycle.startup())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        startup.cancel()
        with pytest.raises(asyncio.CancelledError):
            await startup
        shutdown = asyncio.create_task(lifecycle.shutdown())
        await asyncio.sleep(0)
        assert not shutdown.done()
    finally:
        release.set()
        await lifecycle.shutdown()
    await shutdown
    assert_closed(lifecycle, dispatcher, manager, owner)
