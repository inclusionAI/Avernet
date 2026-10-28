"""``AcliCliService`` — the spec's acceptance criteria, run through the real task handlers.

The service enqueues; the fake worker runs the real ``AcliOperationTaskHandler``
and ``AcliCancelTaskHandler`` against an in-memory engine, Passport and store.
Most tests assert the grant list and the container together, because the
invariant is that anything installed in any stage is granted.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agentclaw.community.core.acli_clis import service as service_module
from agentclaw.community.core.acli_clis.errors import (
    AcliBotNotRunningError,
    AcliCliNameMismatchError,
    AcliDefaultCliError,
    AcliEngineOutdatedError,
    AcliEngineUnsupportedError,
    AcliGrantSyncError,
    AcliMarketUnavailableError,
    AcliOperationCancelledError,
    AcliOperationFailedError,
    AcliOperationInProgressError,
    AcliOperationNotActiveError,
    AcliOperationNotFoundError,
    AcliRequestRejectedError,
    AcliStageNotAllowedError,
)
from agentclaw.community.core.acli_clis.operations import OperationStep
from agentclaw.community.core.acli_clis.port import AcliPortError
from agentclaw.community.core.acli_clis.service import AcliCliService
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.caller_identity.contracts import (
    CallerIdentityPermissionError,
    CallerLockEpochError,
)
from agentclaw.community.core.task_queue.types import TaskStatus

from .fakes import (
    OWNER,
    AgentPass,
    Bots,
    Collaborators,
    Engine,
    Identities,
    Locks,
    Manifest,
    Market,
    Queue,
    Store,
    Worker,
    bot,
    handlers,
)

pytestmark = pytest.mark.asyncio


class _World:
    def __init__(
        self,
        *,
        record: dict[str, Any] | None = None,
        granted: list[str] | None = None,
        installed: set[str] | None = None,
        identities: Identities | None = None,
        lock: Any = None,
        collaborators: Collaborators | None = None,
    ) -> None:
        self.bots = Bots(bot() if record is None else record)
        self.agentpass = AgentPass(["dataphin"] if granted is None else granted)
        self.engine = Engine(installed)
        self.queue = Queue()
        self.store = Store()
        self.market = Market()
        self.service = AcliCliService(
            bot_repository=self.bots,  # type: ignore[arg-type]
            lock_repository=Locks(lock),  # type: ignore[arg-type]
            identity_repository=identities or Identities(),  # type: ignore[arg-type]
            scope_reconciler=self.agentpass,  # type: ignore[arg-type]
            manifest_resolver=Manifest(),  # type: ignore[arg-type]
            port=self.engine,  # type: ignore[arg-type]
            market=self.market,  # type: ignore[arg-type]
            collaborators=collaborators or Collaborators(),  # type: ignore[arg-type]
            task_queue=self.queue,  # type: ignore[arg-type]
            task_repository=self.queue,  # type: ignore[arg-type]
            store=self.store,  # type: ignore[arg-type]
        )
        self.worker = Worker(self.queue, *handlers(
            store=self.store, engine=self.engine, bots=self.bots,
            agentpass=self.agentpass, queue=self.queue,
        ))

    def call(self, method: str, **kwargs: Any):
        args = {"bot_id": "bot-1", "entity_id": OWNER, "actor_id": OWNER, **kwargs}
        if method in ("add", "remove", "cancel"):
            args.setdefault("lock_epoch", None)
        return getattr(self.service, method)(**args)

    async def run(self, method: str, **kwargs: Any):
        """A sync call, with the worker running beside it."""
        return await self.worker.run_while(self.call(method, **kwargs))


@pytest.fixture(autouse=True)
def _fast_waits(monkeypatch):
    monkeypatch.setattr(service_module, "WAIT_POLL_SECONDS", 0.01)


# ── search ───────────────────────────────────────────────────────────────


async def test_search_asks_the_market_and_touches_no_bot_or_container() -> None:
    world = _World()

    page = await world.service.search_market(keyword="data", page_no=2, page_size=5)

    assert [t.name for t in page.items] == ["dataphin"]
    assert world.market.calls == [("data", 2, 5)]
    assert world.engine.calls == []


# ── list ─────────────────────────────────────────────────────────────────


async def test_list_reads_the_container_and_adds_the_defaults() -> None:
    world = _World(
        granted=["dataphin", "adev-cli"], installed={"adev-cli", "yuntu-cli"},
        identities=Identities({"adev-cli": "caller"}),
    )

    listed = await world.call("list_bot_clis")

    assert [(c.cli_code, c.identity_mode, c.default) for c in listed] == [
        ("adev-cli", "caller", False),
        ("yuntu-cli", "owner", False),
        ("dataphin", "owner", True),
    ]
    # The grant's own text wins; an ungranted tool shows the engine's.
    assert listed[0].cli_desc == "adev-cli desc"
    assert listed[1].cli_desc == "yuntu-cli engine desc"
    assert world.engine.calls == [("list", "draft")]


async def test_list_addresses_the_named_stage() -> None:
    world = _World()

    await world.call("list_bot_clis", stage="online")

    assert world.engine.calls == [("list", "online")]


async def test_a_stage_on_a_personal_bot_is_refused() -> None:
    world = _World(record=bot(bot_type="personal"))

    with pytest.raises(AcliStageNotAllowedError):
        await world.call("list_bot_clis", stage="draft")
    assert world.engine.calls == []
    assert len(await world.call("list_bot_clis")) == 1


async def test_a_member_may_list_and_a_stranger_may_not() -> None:
    world = _World(collaborators=Collaborators({"member-1": PermissionLevel.MEMBER}))

    assert await world.call("list_bot_clis", actor_id="member-1")
    with pytest.raises(CallerIdentityPermissionError):
        await world.call("list_bot_clis", actor_id="stranger")


async def test_a_runtime_without_a_container_lists_as_not_running() -> None:
    world = _World()
    world.engine.errors["list"] = [AcliPortError("not_running", "no container")]

    with pytest.raises(AcliBotNotRunningError):
        await world.call("list_bot_clis")


async def test_an_outdated_engine_lists_as_outdated() -> None:
    world = _World()
    world.engine.errors["list"] = [AcliPortError("engine_outdated", "old")]

    with pytest.raises(AcliEngineOutdatedError):
        await world.call("list_bot_clis")


async def test_an_engine_without_acli_is_refused() -> None:
    world = _World(record=bot(active_engine="hermes"))

    with pytest.raises(AcliEngineUnsupportedError):
        await world.call("list_bot_clis")


# ── add ──────────────────────────────────────────────────────────────────


async def test_add_grants_then_installs_and_answers_with_the_tool() -> None:
    world = _World()

    outcome = await world.run("add", cli_code="deepinsight-cli")

    assert outcome.operation.status == "succeeded"
    assert (outcome.tool.name, outcome.tool.command, outcome.tool.version) == (
        "deepinsight-cli", "de", "1.0.0",
    )
    assert "deepinsight-cli" in world.agentpass.codes
    granted = next(i for i in world.agentpass.items if i["cli_code"] == "deepinsight-cli")
    assert granted["cli_desc"] == "DeepInsight"
    assert world.engine.installed["draft"] == {"deepinsight-cli"}
    assert [c[:3] for c in world.engine.calls if c[0] == "submit"] == [
        ("submit", "draft", "install"),
    ]


async def test_a_teclaw_bot_installs_through_its_own_engine() -> None:
    world = _World(record=bot(active_engine="teclaw"))

    outcome = await world.run("add", cli_code="deepinsight-cli")

    assert outcome.operation.status == "succeeded"
    assert world.engine.installed["draft"] == {"deepinsight-cli"}
    # The task payload carries the engine, so every call picks teclaw's prefix.
    assert world.engine.engines and set(world.engine.engines) == {"teclaw"}


async def test_add_keeps_the_identity_the_owner_chose_before() -> None:
    world = _World(identities=Identities({"yuque-cli": "caller"}))

    await world.run("add", cli_code="yuque-cli")

    granted = next(i for i in world.agentpass.items if i["cli_code"] == "yuque-cli")
    assert granted["identity_mode"] == "caller"


async def test_add_of_a_granted_cli_installs_without_granting_again() -> None:
    world = _World(granted=["dataphin", "yuque-cli"])

    await world.run("add", cli_code="yuque-cli", stage="verify")

    assert world.agentpass.codes.count("yuque-cli") == 1
    assert world.engine.installed["verify"] == {"yuque-cli"}


async def test_add_async_answers_at_once_and_the_operation_reads_back() -> None:
    world = _World()
    world.engine.autorun = None

    outcome = await world.call("add", cli_code="yuque-cli", run_async=True)

    assert (outcome.operation.status, outcome.operation.step) == ("running", "granting")
    await world.worker.step()
    op = await world.call("get_operation", operation_id=outcome.operation.id)
    assert (op.status, op.step) == ("running", "installing")
    [engine_op] = world.engine.ops
    world.engine.finish(engine_op, "succeeded")
    await world.worker.drain()
    op = await world.call("get_operation", operation_id=outcome.operation.id)
    assert (op.status, op.step) == ("succeeded", None)


async def test_a_sync_add_still_running_after_its_wait_is_returned_running(monkeypatch) -> None:
    monkeypatch.setattr(service_module, "SYNC_WAIT_SECONDS", 0.05)
    world = _World()
    world.engine.autorun = None

    outcome = await world.run("add", cli_code="yuque-cli")

    assert outcome.operation.running and outcome.tool is None


async def test_a_second_operation_while_one_runs_is_refused_with_the_live_one() -> None:
    world = _World()
    world.engine.autorun = None
    first = await world.call("add", cli_code="yuque-cli", run_async=True)

    with pytest.raises(AcliOperationInProgressError) as refused:
        await world.call("remove", cli_code="adev-cli", run_async=True)

    assert refused.value.operation.id == first.operation.id
    assert refused.value.operation.cli_code == "yuque-cli"
    # Another stage is another runtime.
    other = await world.call("add", cli_code="yuque-cli", stage="online", run_async=True)
    assert other.operation.stage == "online"


async def test_an_alias_is_refused_before_anything_is_enqueued() -> None:
    world = _World()

    with pytest.raises(AcliCliNameMismatchError) as caught:
        await world.call("add", cli_code="di")

    assert caught.value.market_name == "deepinsight-cli"
    assert world.queue.tasks == {}


async def test_an_unreachable_market_leaves_the_check_to_the_engine() -> None:
    world = _World()
    world.market.error = AcliMarketUnavailableError()

    outcome = await world.run("add", cli_code="yuque-cli")

    assert outcome.operation.status == "succeeded"


async def test_a_failed_install_is_a_rejected_request() -> None:
    world = _World()
    world.engine.autorun = "failed"

    with pytest.raises(AcliRequestRejectedError) as caught:
        await world.run("add", cli_code="nope-cli")

    assert caught.value.code == "install_failed"
    # Granted first, and never revoked (D9).
    assert "nope-cli" in world.agentpass.codes


async def test_a_grant_that_does_not_hold_installs_nothing() -> None:
    world = _World()
    world.agentpass.drops_writes = True

    with pytest.raises(AcliGrantSyncError):
        await world.run("add", cli_code="yuque-cli")

    assert not [c for c in world.engine.calls if c[0] == "submit"]


async def test_a_grant_write_that_raises_but_lands_still_installs() -> None:
    world = _World()

    class _Flaky(AgentPass):
        def add_cli(self, *, bot, item):
            self.items.append(dict(item))
            raise RuntimeError("timeout after write")

    world.agentpass.__class__ = _Flaky
    outcome = await world.run("add", cli_code="yuque-cli")

    assert outcome.operation.status == "succeeded"


async def test_a_runtime_without_a_container_fails_the_add() -> None:
    world = _World()
    world.engine.errors["submit"] = [AcliPortError("not_running", "no container")]

    with pytest.raises(AcliBotNotRunningError):
        await world.run("add", cli_code="yuque-cli")


async def test_a_flaky_hop_is_retried() -> None:
    world = _World()
    world.engine.errors["submit"] = [AcliPortError("timeout", "slow")]

    outcome = await world.run("add", cli_code="yuque-cli")

    assert outcome.operation.status == "succeeded"


# ── remove ───────────────────────────────────────────────────────────────


async def test_remove_uninstalls_and_keeps_the_grant() -> None:
    world = _World(granted=["dataphin", "yuque-cli"], installed={"yuque-cli"})

    outcome = await world.run("remove", cli_code="yuque-cli")

    assert outcome.operation.status == "succeeded"
    assert world.engine.installed["draft"] == set()
    assert "yuque-cli" in world.agentpass.codes


async def test_a_default_cli_cannot_be_removed() -> None:
    world = _World()

    with pytest.raises(AcliDefaultCliError):
        await world.call("remove", cli_code="dataphin")
    assert world.queue.tasks == {}


async def test_a_failed_uninstall_is_an_operation_failure() -> None:
    world = _World(installed={"yuque-cli"})
    world.engine.autorun = "failed"

    with pytest.raises(AcliOperationFailedError) as caught:
        await world.run("remove", cli_code="yuque-cli")

    assert caught.value.code == "uninstall_failed"


# ── cancel ───────────────────────────────────────────────────────────────


async def test_cancel_stops_the_running_install() -> None:
    world = _World()
    world.engine.autorun = None
    add = await world.call("add", cli_code="yuque-cli", run_async=True)
    await world.worker.step()

    cancel = await world.call("cancel", cli_code="yuque-cli", action="install", run_async=True)
    assert (cancel.operation.kind, cancel.operation.step) == ("cancel", "cancelling")
    await world.worker.step()
    [engine_op] = world.engine.ops
    assert world.engine.ops[engine_op].state == "cancelling"
    world.engine.finish(engine_op, "cancelled")
    await world.worker.drain()

    assert (await world.call("get_operation", operation_id=cancel.operation.id)).status == "succeeded"
    target = await world.call("get_operation", operation_id=add.operation.id)
    assert (target.status, target.error_code) == ("cancelled", "cancelled")


async def test_a_sync_add_that_is_cancelled_says_so() -> None:
    world = _World()
    world.engine.autorun = None
    add = asyncio.ensure_future(world.call("add", cli_code="yuque-cli"))
    while not world.store.rows:
        await asyncio.sleep(0.01)
    await world.worker.step()
    await world.call("cancel", cli_code="yuque-cli", action="install", run_async=True)
    await world.worker.step()
    world.engine.finish(next(iter(world.engine.ops)), "cancelled")

    with pytest.raises(AcliOperationCancelledError):
        await world.worker.run_while(add)


async def test_cancel_waits_while_the_operation_is_still_granting() -> None:
    world = _World()
    world.engine.autorun = None
    await world.call("add", cli_code="yuque-cli", run_async=True)
    cancel = await world.call("cancel", cli_code="yuque-cli", action="install", run_async=True)

    # The cancel runs first: its target has not reached the engine yet.
    cancel_task = next(t for t in world.queue.tasks.values() if t.payload["kind"] == "cancel")
    outcome = await world.worker.handlers[cancel_task.task_type]._step(cancel_task.payload)
    assert outcome.__class__.__name__ == "Reschedule"
    assert not [c for c in world.engine.calls if c[0] == "cancel"]
    await world.worker.step()
    assert (await world.call("get_operation", operation_id=cancel.operation.id)).running


async def test_cancel_needs_a_matching_running_operation() -> None:
    world = _World()
    with pytest.raises(AcliOperationNotActiveError):
        await world.call("cancel", cli_code="yuque-cli", action="install")

    world.engine.autorun = None
    await world.call("add", cli_code="yuque-cli", run_async=True)
    with pytest.raises(AcliOperationNotActiveError):
        await world.call("cancel", cli_code="yuque-cli", action="uninstall")
    with pytest.raises(AcliOperationNotActiveError):
        await world.call("cancel", cli_code="adev-cli", action="install")


async def test_a_second_cancel_answers_for_the_first() -> None:
    world = _World()
    world.engine.autorun = None
    await world.call("add", cli_code="yuque-cli", run_async=True)

    first = await world.call("cancel", cli_code="yuque-cli", action="install", run_async=True)
    second = await world.call("cancel", cli_code="yuque-cli", action="install", run_async=True)

    assert second.operation.id == first.operation.id


async def test_a_cancel_that_arrives_after_the_end_is_not_active() -> None:
    world = _World()
    world.engine.autorun = None
    await world.call("add", cli_code="yuque-cli", run_async=True)
    await world.worker.step()
    cancel = await world.call("cancel", cli_code="yuque-cli", action="install", run_async=True)
    world.engine.finish(next(iter(world.engine.ops)), "succeeded")
    await world.worker.drain()

    op = await world.call("get_operation", operation_id=cancel.operation.id)
    assert (op.status, op.error_code) == ("failed", "not_active")


# ── reading operations ───────────────────────────────────────────────────


async def test_the_live_operation_is_the_running_add_and_shows_its_cancel() -> None:
    world = _World()
    world.engine.autorun = None
    assert await world.call("live_operation") is None
    add = await world.call("add", cli_code="yuque-cli", run_async=True)
    await world.worker.step()

    live = await world.call("live_operation")
    assert (live.id, live.step) == (add.operation.id, OperationStep.INSTALLING)

    await world.call("cancel", cli_code="yuque-cli", action="install", run_async=True)
    live = await world.call("live_operation")
    assert (live.id, live.step) == (add.operation.id, OperationStep.CANCELLING)
    assert await world.call("live_operation", stage="online") is None


async def test_an_ended_operation_is_not_live() -> None:
    world = _World()
    await world.run("add", cli_code="yuque-cli")

    assert await world.call("live_operation") is None


async def test_an_operation_of_another_bot_is_not_found() -> None:
    world = _World()
    outcome = await world.call("add", cli_code="yuque-cli", run_async=True)
    world.bots.bot = bot(id=8)

    with pytest.raises(AcliOperationNotFoundError):
        await world.call("get_operation", operation_id=outcome.operation.id)
    with pytest.raises(AcliOperationNotFoundError):
        await world.call("get_operation", operation_id="0" * 32)


async def test_a_task_past_its_deadline_reads_as_timed_out() -> None:
    world = _World()
    world.engine.autorun = None
    outcome = await world.call("add", cli_code="yuque-cli", run_async=True)
    [task_id] = world.queue.tasks
    world.queue.time_out(task_id)

    op = await world.call("get_operation", operation_id=outcome.operation.id)

    assert (op.status, op.error_code) == ("failed", "timeout")
    assert world.queue.tasks[task_id].status == TaskStatus.TIMED_OUT


async def test_a_missing_task_reads_as_failed() -> None:
    world = _World()
    outcome = await world.call("add", cli_code="yuque-cli", run_async=True)
    world.queue.tasks.clear()

    op = await world.call("get_operation", operation_id=outcome.operation.id)

    assert (op.status, op.error_code) == ("failed", "lost")


# ── who may write ────────────────────────────────────────────────────────


@pytest.mark.parametrize("method", ["add", "remove", "cancel"])
async def test_only_the_owner_holding_the_lock_may_write_to_an_active_bot(method) -> None:
    extra = {"cli_code": "yuque-cli"} | ({"action": "install"} if method == "cancel" else {})

    with pytest.raises(CallerIdentityPermissionError):
        await _World().call(method, actor_id="member-1", **extra)
    with pytest.raises(AcliBotNotRunningError):
        await _World(record=bot(status="STOPPED")).call(method, **extra)
    with pytest.raises(AcliEngineUnsupportedError):
        await _World(record=bot(active_engine="hermes")).call(method, **extra)


async def test_a_stale_lock_epoch_is_refused() -> None:
    world = _World()

    with pytest.raises(CallerLockEpochError):
        await world.call("add", cli_code="yuque-cli", lock_epoch=99)
