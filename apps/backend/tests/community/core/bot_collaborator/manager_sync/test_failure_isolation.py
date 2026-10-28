"""Non-blocking failure isolation for the manager-sync side channel.

Task 11's evidence suite: every infrastructure failure the syncer
depends on must stay *the syncer's* problem — the business operation
answers regardless, and a business failure must never look like a
notified success. Independent tests, one failure class each:

- **hung BCS delivery** — the business commit returns while the real
  ``HttpBcsManagersPlugin`` PUT is parked on a transport barrier; the
  delivery completes later on the worker, exactly once;
- **coordination (cache) outage** — every round is FAILED with a
  claim-failure alert, nothing is sent, the queue drains, and the
  business call still returns its record;
- **notifier raise** — an admission that raises changes nothing about
  the business result: the record is returned, the write committed;
- **worker crash** (a contract violation inside a round) is contained:
  the round is FAILED, the workers survive and the next round confirms;
- **queue full** — admission answers REJECTED from memory while
  deliveries are parked; the caller blocks on nothing;
- **business DB write error** — the exception propagates out of the
  business write and NO admission happened: a failed write never leaves
  the side-channel a queued "success";
- **framework shutdown ordering** — a case that starts the syncer's
  worker loop cannot leak it past the endpoint framework's teardown:
  the lifecycle is shut down before the per-test engine is disposed.

No millisecond thresholds anywhere: every overlap is proven with
threading/asyncio Events and barriers, never with sleeps.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime
from unittest.mock import Mock

import httpx
import pytest

from tests.community.bot_address_fakes import support_legacy_locator
from tests.community.framework.fixtures import app_with_testing_modules  # noqa: F401

from agentclaw.community.core.bot_collaborator.manager_sync.diagnostics import (
    SyncDiagnostics,
)
from agentclaw.community.core.bot_collaborator.manager_sync.dispatcher import (
    BotManagerSyncDispatcher,
    DispatcherPolicy,
)
from agentclaw.community.core.bot_collaborator.manager_sync.lifecycle import (
    BotManagerSyncLifecycle,
)
from agentclaw.community.core.bot_collaborator.manager_sync.service import (
    BotManagerSyncService,
)
from agentclaw.community.core.bot_collaborator.models import (
    CollaboratorRecord,
    CollaboratorRole,
)
from agentclaw.community.core.bot_collaborator.services.collaborator_service import (
    CollaboratorService,
)
from agentclaw.community.di.bot_manager_sync_config import CONFIG_BLOCK_NAME
from agentclaw.community.di.modules import bot_manager_sync_module as composition
from agentclaw.community.kernel.bot_manager_sync import (
    CollaboratorFact,
    SubmissionResult,
    SyncFacts,
    SyncKey,
    SyncOutcome,
    SyncReason,
)
from agentclaw.community.plugins.community.bcs_managers import HttpBcsManagersPlugin
from agentclaw.community.plugins.local.bcs_managers import (
    RecordingBcsManagersPlugin,
)
from agentclaw.community.plugins.local.manager_sync_coordination import (
    MemoryManagerSyncCoordinator,
)

OWNER = "owner-1"
MEMBER = "member-1"
BOT_PK = 10


# -- shared doubles -----------------------------------------------------------


class FailingNotifier:
    """A ``BotManagerSyncProtocol`` stand-in whose admission raises."""

    def __init__(self) -> None:
        self.request_sync_calls = 0

    def request_sync(self, *, bot_pk, env, avernet_tenant, reason):
        self.request_sync_calls += 1
        raise RuntimeError("side channel unavailable")

    def request_space_sync(self, change):
        raise RuntimeError("side channel unavailable")


def _facts_key() -> SyncKey:
    return SyncKey("teamclaw", "dev", BOT_PK)


def _facts_for(key: SyncKey) -> SyncFacts:
    """One eligible collaborator under whatever key was admitted."""
    return SyncFacts(
        key=key,
        bot_id=f"bot-{key.bot_pk}",
        entity_id=f"entity-{key.bot_pk}",
        owner_id=OWNER,
        collaborators=(CollaboratorFact(MEMBER, "admin", True),),
    )


def make_stack(
    *,
    bcs=None,
    coordinator=None,
    diagnostics=None,
    dispatcher_policy=None,
):
    """A real service over doubles + a real dispatcher over the service."""
    bcs = bcs if bcs is not None else RecordingBcsManagersPlugin()
    coordinator = (
        coordinator if coordinator is not None else MemoryManagerSyncCoordinator()
    )
    diagnostics = diagnostics if diagnostics is not None else SyncDiagnostics()
    reader = Mock()
    reader.read_facts.side_effect = lambda key: _facts_for(key)
    service = BotManagerSyncService(
        reader=reader, bcs=bcs, coordinator=coordinator, diagnostics=diagnostics
    )
    dispatcher = BotManagerSyncDispatcher(
        service, reader, policy=dispatcher_policy, env="dev"
    )
    return service, dispatcher, bcs, diagnostics


def _bot() -> dict:
    return {
        "id": BOT_PK,
        "bot_id": "bot-1",
        "owner_id": OWNER,
        "bot_type": "service",
        "space_id": None,
    }


def _record(user_id: str = MEMBER) -> CollaboratorRecord:
    now = datetime(2026, 10, 27, 10, 0, 0)
    return CollaboratorRecord(
        id=7,
        bot_pk=BOT_PK,
        bot_id="bot-1",
        owner_id=OWNER,
        user_id=user_id,
        role=CollaboratorRole.MEMBER,
        operator_id=OWNER,
        env="dev",
        gmt_create=now,
        gmt_modified=now,
    )


def make_collaborator_case(manager_sync, repository: Mock):
    """The minimal real CollaboratorService over Mock persistence."""
    repository.get_by_bot_and_user.return_value = None
    repository.get_user_role.return_value = None
    repository.list_by_bot.return_value = []
    repository.insert.return_value = _record()
    bot_repo = support_legacy_locator(Mock())
    bot_repo.get_by_id_and_owner.return_value = _bot()
    service = CollaboratorService(
        collaborator_repo=repository,
        bot_repo=bot_repo,
        passport_plugin=Mock(),
        credentials_admins_writer=Mock(),
        space_access_service=Mock(),
        manager_sync=manager_sync,
    )
    return service


async def _drain(dispatcher: BotManagerSyncDispatcher) -> None:
    assert await dispatcher.drain(10), "workers never drained the queue"


# -- 1. the business answer returns while a real HTTP PUT is hung ---------------


@pytest.mark.asyncio
async def test_business_result_returns_while_bcs_http_request_hangs():
    """The commit answers while the real prod delivery sits on the wire.

    The delivery plugin is the real ``HttpBcsManagersPlugin`` over a
    barrier-gated httpx transport that parks the PUT after it was sent;
    the business ``add_collaborator`` must have returned by then, and the
    parked delivery must finish exactly once afterwards.
    """
    entered = asyncio.Event()
    release = asyncio.Event()
    seen_requests: list[httpx.Request] = []

    async def _park(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        entered.set()
        await release.wait()
        return httpx.Response(
            200,
            json={
                "code": 20000,
                "message": "ok",
                "request_id": "bcs-hang-1",
                "data": {"added": 1, "removed": 0, "total": 1},
            },
        )

    bcs = HttpBcsManagersPlugin(
        base_url="http://bcs.test",
        app_credentials={"teamclaw": "trusted-app-token"},
        client=httpx.AsyncClient(
            base_url="http://bcs.test", transport=httpx.MockTransport(_park)
        ),
    )
    _, dispatcher, _, diagnostics = make_stack(bcs=bcs)
    await dispatcher.attach()
    repository = Mock()
    business = make_collaborator_case(manager_sync=dispatcher, repository=repository)

    try:
        # The business commit answered, on the caller's thread...
        record = await asyncio.to_thread(
            business.add_collaborator,
            "bot-1",
            OWNER,
            MEMBER,
            OWNER,
            role="member",
            env="dev",
        )
        assert record.user_id == MEMBER
        repository.insert.assert_called_once()
        # ...while the worker's PUT provably never left the transport.
        await entered.wait()
        assert len(seen_requests) == 1
        assert (
            seen_requests[0].url.path
            == "/api/v1/collaboration/bots/bot-10:entity-10/managers"
        )
        assert seen_requests[0].headers["Authorization"] == "Bearer trusted-app-token"

        release.set()
        await _drain(dispatcher)
        # Exactly one delivery ran; the round confirmed end to end.
        assert len(seen_requests) == 1
        assert diagnostics.counted[("send_confirmed", "confirmed")] == 1
        assert dispatcher.stats.entries == 0
    finally:
        release.set()
        await dispatcher.stop()
        await bcs.aclose()


# -- 2. coordination substrate outage ------------------------------------------


@pytest.mark.asyncio
async def test_coordination_outage_fails_rounds_without_blocking_business():
    """A dead coordination substrate is the syncer's outage, not the caller's.

    ``MemoryManagerSyncCoordinator(generation=None)`` models exactly the
    shared-cache loss the Redis backend reports as
    ``CoordinationUnavailable`` — every round must end FAILED with the
    claim-failure alert, deliver nothing, and leave the worker queue
    empty while the business call still returns its record.
    """
    _, dispatcher, bcs, diagnostics = make_stack(
        coordinator=MemoryManagerSyncCoordinator(generation=None)
    )
    await dispatcher.attach()
    repository = Mock()
    business = make_collaborator_case(manager_sync=dispatcher, repository=repository)
    try:
        record = await asyncio.to_thread(
            business.add_collaborator,
            "bot-1",
            OWNER,
            MEMBER,
            OWNER,
            role="member",
            env="dev",
        )
        assert record.user_id == MEMBER
        repository.insert.assert_called_once()

        await _drain(dispatcher)
        # The outage was reported where it belongs, and nothing was sent.
        assert diagnostics.counted[("claim_failed", "failed")] == 1
        assert bcs.calls == []
        assert dispatcher.stats.entries == 0
    finally:
        await dispatcher.stop()


# -- 3. a notifier failure never changes the business result --------------------


def test_notifier_raise_leaves_business_result_intact():
    """An admission that raises is isolated from the business write."""
    repository = Mock()
    notifier = FailingNotifier()
    business = make_collaborator_case(manager_sync=notifier, repository=repository)

    record = business.add_collaborator(
        "bot-1", OWNER, MEMBER, OWNER, role="member", env="dev"
    )

    assert record.user_id == MEMBER
    repository.insert.assert_called_once()
    assert notifier.request_sync_calls == 1


# -- 4. a crash inside a round is contained --------------------------------------


class _CrashingOnceService:
    """A consumer that violates its own contract on the first round.

    The dispatcher must treat a non-``SyncOutcome`` raise as the round's
    FAILED, never let it kill the worker, and the next round over the
    same real consumer must confirm normally.
    """

    def __init__(self, inner: BotManagerSyncService) -> None:
        self._inner = inner
        self._crashed = False

    async def sync_once(self, key: SyncKey) -> SyncOutcome:
        if not self._crashed:
            self._crashed = True
            raise RuntimeError("contract violation inside the round")
        return await self._inner.sync_once(key)

    async def hold_owner_check(self, key: SyncKey) -> SyncOutcome:
        return await self._inner.hold_owner_check(key)


@pytest.mark.asyncio
async def test_worker_contract_violation_is_isolated_and_survived():
    service, _, bcs, _ = make_stack()
    dispatcher = BotManagerSyncDispatcher(
        _CrashingOnceService(service), Mock(), env="dev"
    )
    await dispatcher.attach()
    try:
        key = _facts_key()
        assert (
            dispatcher.request_sync(
                bot_pk=key.bot_pk,
                env=key.env,
                avernet_tenant=key.avernet_tenant,
                reason=SyncReason.REPAIR,
            )
            is SubmissionResult.QUEUED
        )
        await _drain(dispatcher)
        assert dispatcher.stats.entries == 0  # crashed round cleaned up
        assert bcs.calls == []

        # The worker survived: the next round confirms through the plugin.
        assert (
            dispatcher.request_sync(
                bot_pk=key.bot_pk,
                env=key.env,
                avernet_tenant=key.avernet_tenant,
                reason=SyncReason.REPAIR,
            )
            is SubmissionResult.QUEUED
        )
        await _drain(dispatcher)
        assert len(bcs.calls) == 1
        assert dispatcher.stats.attached
    finally:
        await dispatcher.stop()


# -- 5. a full queue answers from memory -----------------------------------------


@pytest.mark.asyncio
async def test_full_queue_rejects_admission_without_blocking_business(monkeypatch):
    """Capacity counts the parked round: further asks get REJECTED.

    The parked BCS delivery never parked the *callers* — admissions keep
    answering synchronously from memory, the third one as REJECTED once
    the two capacity slots are held, and both admitted rounds finish
    after the barrier lifts.
    """
    _, dispatcher, bcs, _ = make_stack(dispatcher_policy=DispatcherPolicy(max_keys=2))
    entered = asyncio.Event()
    release = asyncio.Event()
    real_replace = bcs.replace_managers

    async def _hanging_replace(snapshot, *, timeout_seconds):
        entered.set()
        await release.wait()
        return await real_replace(snapshot, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(bcs, "replace_managers", _hanging_replace)
    await dispatcher.attach()

    def admit(bot_pk):
        return dispatcher.request_sync(
            bot_pk=bot_pk,
            env="dev",
            avernet_tenant="teamclaw",
            reason=SyncReason.COLLABORATOR_CHANGED,
        )

    try:
        # Two capacity slots: the parked/queued keys occupy both.
        assert admit(11) is SubmissionResult.QUEUED
        assert admit(12) is SubmissionResult.QUEUED
        await entered.wait()
        # The third ask is refused from memory — the caller runs on.
        assert admit(13) is SubmissionResult.REJECTED

        release.set()
        await _drain(dispatcher)
        assert len(bcs.calls) == 2
        assert dispatcher.stats.entries == 0
        assert {call.key.bot_pk for call in bcs.calls} == {11, 12}
    finally:
        release.set()
        await dispatcher.stop()


# -- 6. a failed business write never notifies ------------------------------------


@pytest.mark.asyncio
async def test_business_db_write_error_raises_without_success_notification():
    """The write error surfaces and the side channel stays silent.

    The repository insert fails with the business write itself; the
    exception must propagate to the caller (never swallowed by the
    side-channel's read), and no admission may exist afterwards — a
    failed write must never look like a notified success.
    """
    _, dispatcher, bcs, _ = make_stack()
    await dispatcher.attach()
    repository = Mock()
    repository.get_by_bot_and_user.return_value = None
    repository.get_user_role.return_value = None
    repository.list_by_bot.return_value = []
    repository.insert.side_effect = RuntimeError("db write failed")
    bot_repo = support_legacy_locator(Mock())
    bot_repo.get_by_id_and_owner.return_value = _bot()
    business = CollaboratorService(
        collaborator_repo=repository,
        bot_repo=bot_repo,
        passport_plugin=Mock(),
        credentials_admins_writer=Mock(),
        space_access_service=Mock(),
        manager_sync=dispatcher,
    )
    try:
        with pytest.raises(RuntimeError, match="db write failed"):
            business.add_collaborator(
                "bot-1", OWNER, MEMBER, OWNER, role="member", env="dev"
            )
        # No admission survived the failed write, and nothing was sent.
        assert dispatcher.stats.entries == 0
        assert bcs.calls == []
    finally:
        await dispatcher.stop()


# -- 7. the framework stops the syncer before the engine is disposed ---------------


@pytest.fixture
def dispose_order_probe(monkeypatch):
    """Record what a test-DB disposal observes, assert it during teardown.

    The probe patches ``Engine.dispose`` for the duration of this test
    and asserts — in ITS own teardown, which runs after
    ``app_with_testing_modules``' — that the disposal the framework
    performs saw zero manager-sync worker threads. Together with the
    fixture's in-teardown lifecycle shutdown, that is the ordering
    proof: syncer down first, engine disposed second.
    """
    import sqlalchemy

    recorded: dict[str, list[str]] = {}

    def _probing_dispose(engine_self):
        recorded["syncer_threads_at_dispose"] = [
            thread.name
            for thread in threading.enumerate()
            if thread.name.startswith("manager-sync")
        ]

    monkeypatch.setattr(sqlalchemy.engine.Engine, "dispose", _probing_dispose)
    yield recorded
    assert recorded["syncer_threads_at_dispose"] == [], (
        "the test engine was disposed while manager-sync workers were"
        " still alive — teardown ordering is broken"
    )


@pytest.fixture
def enabled_manager_sync_block(monkeypatch):
    """Install the enabled syncer config for THIS case's app build.

    ``build_injector`` resolves ``ManagerSyncConfig`` eagerly while the
    ``app_with_testing_modules`` fixture assembles, so the enabled block
    must be visible to ``read_user_config`` from fixture-setup time — a
    test-body patch would arrive after the eager read and the app would
    silently keep the disabled posture. The overlay preserves every
    other config block verbatim, so unrelated eager checks see the same
    deployment they always see.
    """
    real_read = composition.read_user_config
    config = dict(real_read())
    config[CONFIG_BLOCK_NAME] = {
        "enabled": True,
        "target_id": "bcs-singlebox",
        "env": "dev",
        "gateway_base_url": "http://127.0.0.1:21000",
        "app_secret_names_by_tenant": {"teamclaw": "bcs-app-secret"},
        "coordination_generation": "gen-ordering-test",
    }
    monkeypatch.setattr(composition, "read_user_config", lambda: config)


def test_framework_shuts_manager_sync_workers_before_database_dispose(
    dispose_order_probe,
    enabled_manager_sync_block,
    app_with_testing_modules,  # noqa: F811 — the fixture imported at module top
):
    """A case that starts the syncer's loop is torn down before dispose."""
    from agentclaw.community.core.bot_collaborator.manager_sync.lifecycle import (
        DisabledBotManagerSyncLifecycle,
    )

    lifecycle = app_with_testing_modules.state.injector.get(BotManagerSyncLifecycle)
    assert isinstance(lifecycle, BotManagerSyncLifecycle)
    assert not isinstance(lifecycle, DisabledBotManagerSyncLifecycle), (
        "the enabled block must bind the loop-owning lifecycle, not its"
        " zero-resource twin"
    )
    assert not any(
        thread.name.startswith("manager-sync") for thread in threading.enumerate()
    ), "sanity: no syncer workers before startup"
    asyncio.run(lifecycle.startup())
    assert any(
        thread.name.startswith("manager-sync") for thread in threading.enumerate()
    ), "sanity: the enabled syncer owns a live worker loop"
