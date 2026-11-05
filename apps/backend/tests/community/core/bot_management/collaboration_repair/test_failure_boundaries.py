"""Repair failures preserve completed work and respect the request deadline."""

import asyncio
import threading
import time

import httpx
import pytest

from agentclaw.community.core.bot_management.collaboration_repair.service import (
    CollaborationRepairService,
)
from agentclaw.community.kernel.bot_manager_sync import SnapshotUnavailable
from agentclaw.community.kernel.collaboration_repair import RepairPreconditionError
from agentclaw.community.plugins.community.bcs_managers import HttpBcsManagersPlugin
from tests.community.core.bot_management.collaboration_repair.factories import (
    make_repair_case,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["initial", "ownership", "managers"])
async def test_blocked_reader_returns_before_thread_finishes(phase):
    # Earlier phases keep a generous, frozen budget. Only the selected
    # read gets the short real asyncio deadline, independent of the time
    # spent resolving preceding facts on the executor.
    now = 0.0
    case = make_repair_case(
        timeout_seconds=1.0 if phase == "initial" else 30.0,
        clock=lambda: now,
    )
    if phase != "initial":
        preceding_send = (
            case.metadata.exists if phase == "ownership"
            else case.metadata.update_profile
        )
        preceding_result = preceding_send.return_value

        async def arm_read_deadline(*args, **kwargs):
            nonlocal now
            now = 29.0
            return preceding_result

        preceding_send.side_effect = arm_read_deadline
    reader = {
        "initial": case.reader,
        "ownership": case.ownership_reader,
        "managers": case.managers_reader,
    }[phase]
    release = threading.Event()
    started = threading.Event()
    finished = threading.Event()
    entered = asyncio.Event()
    loop = asyncio.get_running_loop()
    facts = reader.read_facts.return_value

    def blocked_read(key):
        started.set()
        loop.call_soon_threadsafe(entered.set)
        try:
            assert release.wait(15), "test did not release its facts reader"
            return facts
        finally:
            finished.set()

    reader.read_facts.side_effect = blocked_read
    task = asyncio.create_task(case.service.repair(case.command))
    entry_task = asyncio.create_task(entered.wait())
    try:
        # Confirm this exact reader started rather than accepting an earlier
        # timeout. These watchdogs detect a hang; production's real timeout
        # must finish the request while the selected reader is still blocked.
        await asyncio.wait(
            (task, entry_task), timeout=15, return_when=asyncio.FIRST_COMPLETED,
        )
        assert entered.is_set(), "repair stopped before its selected reader started"
        if phase == "initial":
            with pytest.raises(RepairPreconditionError, match="repair_deadline_exceeded"):
                await asyncio.wait_for(task, timeout=15)
            case.metadata.exists.assert_not_awaited()
        else:
            report = await asyncio.wait_for(task, timeout=15)
            items = {item.component: item for item in report.items}
            assert items[phase].status == "failed"
            assert items[phase].error_code == "delivery_timeout"
            assert report.status == ("failed" if phase == "ownership" else "partial_failed")
        assert not finished.is_set()
    finally:
        release.set()
        task.cancel()
        entry_task.cancel()
        await asyncio.gather(task, entry_task, return_exceptions=True)
        if started.is_set():
            assert await asyncio.to_thread(finished.wait, 15), "facts reader did not exit"
    # A late DB result must never restart the cancelled flow.
    await asyncio.sleep(0)
    if phase != "managers":
        case.ownership.replace_ownership.assert_not_awaited()
        case.metadata.update_profile.assert_not_awaited()
    case.managers.replace_managers.assert_not_awaited()


@pytest.mark.asyncio
async def test_managers_read_failure_preserves_prior_successes():
    case = make_repair_case()
    case.managers_reader.read_facts.side_effect = SnapshotUnavailable("read_error")
    report = await case.service.repair(case.command)
    assert [(item.component, item.status, item.error_code) for item in report.items] == [
        ("ownership", "succeeded", None),
        ("profile", "succeeded", None),
        ("managers", "failed", "collaboration_facts_read_error"),
    ]
    assert report.status == "partial_failed"
    case.ownership.replace_ownership.assert_awaited_once()
    case.metadata.update_profile.assert_awaited_once()
    case.managers.replace_managers.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_managers_missing_response_is_reported():
    case = make_repair_case()
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(404, json={"code": 40400, "data": None})

    async with httpx.AsyncClient(
        base_url="http://bcs.test", transport=httpx.MockTransport(handle)
    ) as client:
        managers = HttpBcsManagersPlugin(
            base_url="http://bcs.test", mint_principal=lambda tenant: "test-principal",
            client=client,
        )
        service = CollaborationRepairService(
            case.reader, case.ownership_reader, case.managers_reader,
            case.metadata, case.ownership, managers,
            timeout_seconds=30, clock=time.monotonic,
        )
        report = await service.repair(case.command)
    assert len(requests) == 1
    assert report.items[2].status == "failed"
    assert report.items[2].error_code == "collaboration_bot_not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["initial", "ownership", "managers"])
async def test_reader_timeout_error_propagates_without_deadline_expiry(phase):
    case = make_repair_case()
    reader = {
        "initial": case.reader,
        "ownership": case.ownership_reader,
        "managers": case.managers_reader,
    }[phase]
    error = TimeoutError("unclassified read error")
    reader.read_facts.side_effect = error
    with pytest.raises(TimeoutError, match="unclassified read error"):
        await case.service.repair(case.command)
    case.managers.replace_managers.assert_not_awaited()
