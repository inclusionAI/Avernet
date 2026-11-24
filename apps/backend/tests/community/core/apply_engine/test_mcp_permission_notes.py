import asyncio

from agentclaw.community.core.apply_engine.run.mcp_permission_notes import permission_notes


def test_only_explicit_server_denial_is_reported_once():
    calls = []

    def check(user, code, timeout):
        calls.append((user, code, timeout))
        return {"denied": False, "unknown": None, "allowed": True}[code]

    notes = asyncio.run(permission_notes(check, "user", ["denied", "unknown", "allowed", "denied"]))
    assert len(notes) == 1
    assert "denied" in notes[0]
    assert [code for _, code, _ in calls].count("denied") == 1
    assert all(user == "user" and 0 < timeout <= 2 for user, _, timeout in calls)


def test_batch_limits_concurrency_and_ignores_late_results():
    import threading
    import time

    lock = threading.Lock()
    active = 0
    maximum = 0
    started = []

    def check(user, code, timeout):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            started.append(code)
        try:
            time.sleep(2.2 if code == "slow" else 0.08)
            return False
        finally:
            with lock:
                active -= 1

    start = time.monotonic()
    notes = asyncio.run(permission_notes(check, "user", ["slow", "one", "two", "three", "four"]))
    elapsed = time.monotonic() - start
    assert maximum <= 4
    assert set(started) == {"slow", "one", "two", "three", "four"}
    assert len(notes) == 4
    assert not any("slow" in note for note in notes)
    assert 1.8 <= elapsed < 2.2


def test_only_successful_declarations_with_delivery_and_passport_receipts_are_checked():
    from datetime import datetime
    from types import SimpleNamespace
    from agentclaw.community.core.apply_engine.capabilities import Category
    from agentclaw.community.core.apply_engine.outcomes import (
        ApplyReport, ApplyStatus, CategoryResult, EntryOutcome, EntryResult,
    )
    from agentclaw.community.core.apply_engine.run.mcp_permission_notes import finish_permission_notes
    from agentclaw.community.core.skill_center.runtime_projection_contract import (
        RuntimeProjectionResult, RuntimeProjectionStatus,
    )

    state = SimpleNamespace(
        manifest_mcp_candidates={"mcp/delivered": frozenset({"delivered"}),
                                 "mcp/failed": frozenset({"failed"}),
                                 "mcp/pending": frozenset({"pending"})},
        delivered_mcp_codes=set(),
    )
    ctx = SimpleNamespace(permission_subject="person", capability_state=state)
    report = ApplyReport("a", "bot", "explicit", ApplyStatus.PARTIAL, datetime.now(),
        categories=(CategoryResult(Category.CAPABILITIES, entries=(
            EntryResult(Category.CAPABILITIES, "mcp/delivered", EntryOutcome.CREATED),
            EntryResult(Category.CAPABILITIES, "mcp/failed", EntryOutcome.FAILED),
            EntryResult(Category.CAPABILITIES, "mcp/pending", EntryOutcome.CREATED),
        )),),
        runtime_projection=RuntimeProjectionResult(
            status=RuntimeProjectionStatus.CONVERGED,
            delivered_mcp_codes=frozenset({"delivered", "failed"}), passport_updated=True,
            passport_mcp_codes=frozenset({"delivered", "failed"}),
        ), notes=("existing",))
    calls = []
    def check(user, code, timeout):
        calls.append((user, code))
        return False
    result = asyncio.run(finish_permission_notes(ctx, report, check))
    assert calls == [("person", "delivered")]
    assert result.notes[0] == "existing" and "delivered" in result.notes[1]


def test_concurrent_applies_share_process_limit():
    import threading
    import time

    gate = threading.Event()
    lock = threading.Lock()
    active = 0
    maximum = 0

    def check(user, code, timeout):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        try:
            assert gate.wait(timeout=1.5)
            return False
        finally:
            with lock:
                active -= 1

    async def run():
        tasks = [asyncio.create_task(permission_notes(check, f"user-{i}",
                 [f"server-{j}" for j in range(4)])) for i in range(5)]
        try:
            deadline = time.monotonic() + 1
            while maximum < 16 and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert maximum == 16
        finally:
            gate.set()
        return await asyncio.gather(*tasks)

    reports = asyncio.run(run())
    assert maximum == 16
    assert all(len(notes) == 4 for notes in reports)


def test_expired_budget_does_not_dispatch_remaining_or_change_returned_notes(monkeypatch):
    import threading
    from agentclaw.community.core.apply_engine.run import mcp_permission_notes

    monkeypatch.setattr(mcp_permission_notes, "_BUDGET_SECONDS", 0.2)
    slow_release = threading.Event()
    slow_started = threading.Event()
    calls = []
    lock = threading.Lock()

    def check(user, code, timeout):
        with lock:
            calls.append(code)
        if code == "timely":
            return False
        if code == "fails":
            raise TimeoutError("unavailable")
        slow_started.set()
        slow_release.wait(1)
        return False

    async def run():
        # The quick responses free two slots, allowing all four blockers to
        # occupy them before the unstarted final item reaches the deadline.
        notes = await permission_notes(check, "user", [
            "timely", "fails", "slow-1", "slow-2", "slow-3", "slow-4", "not-dispatched",
        ])
        before_release = tuple(notes)
        assert slow_started.is_set()
        assert len(notes) == 1 and "timely" in notes[0]
        assert "not-dispatched" not in calls
        slow_release.set()
        await asyncio.sleep(0.05)
        assert notes == before_release
        return notes

    try:
        result = asyncio.run(run())
    finally:
        slow_release.set()
    assert len(result) == 1
