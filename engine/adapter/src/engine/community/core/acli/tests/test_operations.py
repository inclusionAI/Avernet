"""``AcliOperations`` against the fake ``acli``: the slot, cancel, files, restart."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time

import pytest
from engine.community.core.acli import operations as ops_module
from engine.community.core.acli.operations import (
    INSTALL,
    UNINSTALL,
    AcliOperations,
    OperationBusyError,
    OperationNotActiveError,
    OperationStore,
)
from engine.community.core.acli.runner import AcliError
from engine.community.core.acli.tests.fake_acli import alive


@pytest.fixture
def slot(fake_acli, tmp_path):
    runner, calls = fake_acli
    store_dir = tmp_path / "operations"
    return AcliOperations(runner, OperationStore(store_dir)), calls, store_dir


async def _ended(slot_ops: AcliOperations, op, timeout: float = 10.0):
    await AcliOperations.wait(op, timeout)
    assert not op.active, op
    return op


async def _until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition never held"
        await asyncio.sleep(0.02)


def _args(calls) -> list[list[str]]:
    return [c["args"][:2] for c in calls()]


async def test_install_runs_to_success_and_is_kept_by_id(slot, monkeypatch):
    ops, calls, store_dir = slot

    op = await _ended(ops, ops.submit(INSTALL, "deepinsight-cli"))

    assert op.state == "succeeded"
    assert op.result == {
        "name": "deepinsight-cli", "command": "di", "description": "DeepInsight",
        "version": "1.0.9", "already_installed": False,
    }
    assert _args(calls) == [["tool", "list"], ["install", "deepinsight-cli"]]
    record = json.loads((store_dir / f"{op.id}.json").read_text())
    assert (record["state"], record["pgid"]) == ("succeeded", None)
    assert ops.get(op.id) is op


async def test_installing_an_installed_tool_runs_no_acli_install(slot, monkeypatch):
    ops, calls, _ = slot
    monkeypatch.setenv("FAKE_ACLI_INSTALLED", "dataphin:dp")

    op = await _ended(ops, ops.submit(INSTALL, "dataphin"))

    assert (op.state, op.result["already_installed"], op.result["command"]) == (
        "succeeded", True, "dp",
    )
    assert _args(calls) == [["tool", "list"]]


async def test_one_operation_at_a_time_no_queue(slot, monkeypatch):
    ops, _, _ = slot
    monkeypatch.setenv("FAKE_ACLI_DELAY", "1")

    first = ops.submit(INSTALL, "dataphin")
    with pytest.raises(OperationBusyError) as busy:
        ops.submit(UNINSTALL, "yuntu-cli")
    assert busy.value.operation is first

    await _ended(ops, first)
    second = await _ended(ops, ops.submit(UNINSTALL, "dataphin"))
    assert second.id != first.id and second.state == "succeeded"


async def test_a_failure_stays_failed_under_its_id(slot, monkeypatch):
    ops, calls, store_dir = slot
    monkeypatch.setenv("FAKE_ACLI_MODE", "fail")

    op = await _ended(ops, ops.submit(INSTALL, "nope"))

    assert op.state == "failed"
    assert op.error["code"] == "install_failed"
    ran = len(calls())
    fresh = AcliOperations(ops._runner, OperationStore(store_dir))
    assert fresh.get(op.id).error == op.error
    assert len(calls()) == ran


async def test_uninstall_reports_whether_anything_was_removed(slot, monkeypatch):
    ops, calls, _ = slot
    monkeypatch.setenv("FAKE_ACLI_INSTALLED", "deepinsight-cli:di")

    gone = await _ended(ops, ops.submit(UNINSTALL, "di"))
    absent = await _ended(ops, ops.submit(UNINSTALL, "di"))

    assert gone.result == {"name": "deepinsight-cli", "uninstalled": True}
    assert absent.result == {"name": "di", "uninstalled": False}
    assert _args(calls) == [["tool", "list"], ["tool", "uninstall"], ["tool", "list"]]


async def test_cancel_before_the_install_lands_kills_acli_and_leaves_nothing(slot, monkeypatch):
    ops, calls, _ = slot
    monkeypatch.setenv("FAKE_ACLI_DELAY", "30")
    monkeypatch.setenv("FAKE_ACLI_DELAY_FIRST", "1")

    op = ops.submit(INSTALL, "dataphin")
    await _until(lambda: len(calls()) == 2)
    install_pid = calls()[1]["pid"]
    assert ops.cancel(INSTALL, "dataphin").state == "cancelling"
    await _ended(ops, op)

    assert op.state == "cancelled" and op.result is None
    assert not alive(install_pid)
    # Looked again, found nothing installed, so nothing to undo.
    assert _args(calls) == [["tool", "list"], ["install", "dataphin"], ["tool", "list"]]


async def test_cancel_after_the_install_landed_uninstalls_it(slot, monkeypatch):
    ops, calls, _ = slot
    monkeypatch.setenv("FAKE_ACLI_DELAY", "30")

    op = ops.submit(INSTALL, "dataphin")
    await _until(lambda: len(calls()) == 2)
    await asyncio.sleep(0.3)
    ops.cancel(INSTALL, "dataphin")
    await _ended(ops, op)

    assert op.state == "cancelled"
    assert _args(calls)[2:] == [["tool", "list"], ["tool", "uninstall"]]


async def test_cancel_after_the_uninstall_removed_it_reinstalls(slot, monkeypatch):
    ops, calls, _ = slot
    monkeypatch.setenv("FAKE_ACLI_INSTALLED", "dataphin:dp")
    monkeypatch.setenv("FAKE_ACLI_DELAY", "30")

    op = ops.submit(UNINSTALL, "dataphin")
    await _until(lambda: len(calls()) == 2)
    await asyncio.sleep(0.3)
    ops.cancel(UNINSTALL, "dataphin")
    await _ended(ops, op)

    assert op.state == "cancelled"
    assert _args(calls)[2:] == [["tool", "list"], ["install", "dataphin"]]


async def test_a_failed_revert_is_reported(slot, monkeypatch):
    ops, calls, _ = slot
    monkeypatch.setenv("FAKE_ACLI_DELAY", "30")
    monkeypatch.setenv("FAKE_ACLI_MODE", "uninstall_fail")

    op = ops.submit(INSTALL, "dataphin")
    await _until(lambda: len(calls()) == 2)
    await asyncio.sleep(0.3)
    ops.cancel(INSTALL, "dataphin")
    await _ended(ops, op)

    assert op.state == "failed"
    assert op.error["code"] == "revert_failed"
    assert "not installed before" in op.error["message"]


async def test_cancel_needs_a_matching_running_operation(slot, monkeypatch):
    ops, _, _ = slot
    with pytest.raises(OperationNotActiveError):
        ops.cancel(INSTALL, "dataphin")

    monkeypatch.setenv("FAKE_ACLI_DELAY", "1")
    op = ops.submit(INSTALL, "dataphin")
    with pytest.raises(OperationNotActiveError):
        ops.cancel(UNINSTALL, "dataphin")
    with pytest.raises(OperationNotActiveError):
        ops.cancel(INSTALL, "yuntu-cli")
    await _ended(ops, op)
    with pytest.raises(OperationNotActiveError):
        ops.cancel(INSTALL, "dataphin")


async def test_cancelling_twice_returns_the_same_operation(slot, monkeypatch):
    ops, calls, _ = slot
    monkeypatch.setenv("FAKE_ACLI_DELAY", "30")
    monkeypatch.setenv("FAKE_ACLI_DELAY_FIRST", "1")

    op = ops.submit(INSTALL, "dataphin")
    await _until(lambda: len(calls()) == 2)
    assert ops.cancel(INSTALL, "dataphin") is ops.cancel(INSTALL, "dataphin")
    await _ended(ops, op)


async def test_an_operation_over_its_limit_fails_with_timeout(slot, monkeypatch):
    ops, calls, _ = slot
    monkeypatch.setattr(ops_module, "OPERATION_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setenv("FAKE_ACLI_DELAY", "30")

    op = await _ended(ops, ops.submit(INSTALL, "dataphin"))

    assert (op.state, op.error["code"]) == ("failed", "timeout")
    assert not alive(calls()[1]["pid"])


async def test_wait_returns_the_running_operation_after_its_timeout(slot, monkeypatch):
    ops, _, _ = slot
    monkeypatch.setenv("FAKE_ACLI_DELAY", "1")

    op = ops.submit(INSTALL, "dataphin")
    assert (await AcliOperations.wait(op, 0.1)).state == "running"
    await _ended(ops, op)


async def test_a_restart_marks_running_operations_interrupted_and_kills_acli(
    fake_acli, tmp_path, monkeypatch
):
    runner, _ = fake_acli
    store_dir = tmp_path / "operations"
    monkeypatch.setenv("FAKE_ACLI_MODE", "sleep")
    leftover = subprocess.Popen(
        [runner.executable, "install", "dataphin"], start_new_session=True,
        stdout=subprocess.DEVNULL,
    )
    store_dir.mkdir()
    (store_dir / ("a" * 32 + ".json")).write_text(json.dumps({
        "id": "a" * 32, "action": "install", "name": "dataphin", "state": "running",
        "started_at": "2026-10-21T00:00:00Z", "pgid": leftover.pid, "was_installed": False,
    }))

    op = AcliOperations(runner, OperationStore(store_dir)).get("a" * 32)

    assert (op.state, op.error["code"], op.pgid) == ("interrupted", "interrupted", None)
    assert leftover.wait(timeout=5) != 0
    saved = json.loads((store_dir / ("a" * 32 + ".json")).read_text())
    assert saved["state"] == "interrupted"


async def test_a_restart_leaves_a_reused_pid_alone(fake_acli, tmp_path):
    """After a container restart the pid may belong to something else."""
    runner, _ = fake_acli
    store_dir = tmp_path / "operations"
    stranger = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        store_dir.mkdir()
        (store_dir / ("b" * 32 + ".json")).write_text(json.dumps({
            "id": "b" * 32, "action": "uninstall", "name": "x", "state": "cancelling",
            "started_at": "2026-10-21T00:00:00Z", "pgid": stranger.pid,
        }))

        AcliOperations(runner, OperationStore(store_dir)).get("b" * 32)

        assert stranger.poll() is None
    finally:
        stranger.kill()
        stranger.wait()


def test_unknown_or_malformed_ids_read_as_none(fake_acli, tmp_path):
    runner, _ = fake_acli
    ops = AcliOperations(runner, OperationStore(tmp_path / "operations"))

    assert ops.get("0" * 32) is None
    assert ops.get("../../etc/passwd") is None
    assert ops.get("A" * 32) is None


def test_prune_keeps_the_newest_and_drops_old_ones(tmp_path):
    store = OperationStore(tmp_path)
    now = time.time()
    for index in range(205):
        path = tmp_path / f"{index:032x}.json"
        path.write_text("{}")
        os.utime(path, (now - 205 + index, now - 205 + index))
    stale = tmp_path / ("f" * 32 + ".json")
    stale.write_text("{}")
    os.utime(stale, (now - 2 * 86400, now - 2 * 86400))

    store.prune()

    kept = sorted(p.name for p in tmp_path.iterdir())
    assert len(kept) == 200
    assert f"{204:032x}.json" in kept and f"{4:032x}.json" not in kept
    assert stale.name not in kept


async def test_bad_input_starts_nothing(slot):
    ops, calls, store_dir = slot

    with pytest.raises(AcliError) as caught:
        ops.submit(INSTALL, "-x")

    assert caught.value.code == "invalid_input"
    assert calls() == [] and not store_dir.exists()


async def test_a_record_that_cannot_be_written_refuses_the_operation(fake_acli, tmp_path):
    runner, calls = fake_acli
    blocker = tmp_path / "file"
    blocker.write_text("")
    ops = AcliOperations(runner, OperationStore(blocker / "operations"))

    with pytest.raises(AcliError) as caught:
        ops.submit(INSTALL, "dataphin")

    assert caught.value.code == "install_failed"
    assert calls() == []



def test_unreadable_or_malformed_records_read_as_none(tmp_path):
    store = OperationStore(tmp_path)
    (tmp_path / ("1" * 32 + ".json")).write_text("{not json")
    (tmp_path / ("2" * 32 + ".json")).write_text(json.dumps({"id": "2" * 32}))

    assert store.load("1" * 32) is None
    assert store.load("2" * 32) is None


def test_an_unlistable_store_reads_as_empty(tmp_path, monkeypatch):
    store = OperationStore(tmp_path)
    (tmp_path / ("3" * 32 + ".json")).write_text("{}")

    def refuse(*_args, **_kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(type(tmp_path), "iterdir", refuse)
    assert store.files() == []
    store.prune()


def test_a_failed_prune_keeps_the_file(tmp_path, monkeypatch):
    store = OperationStore(tmp_path)
    stale = tmp_path / ("4" * 32 + ".json")
    stale.write_text("{}")
    os.utime(stale, (0, 0))

    def refuse(self, *_args, **_kwargs):
        raise PermissionError("read-only")

    monkeypatch.setattr(type(stale), "unlink", refuse)
    store.prune()

    assert stale.exists()


def test_an_unknown_action_is_a_programming_error(slot):
    ops, _, _ = slot

    with pytest.raises(ValueError):
        ops.submit("upgrade", "dataphin")


async def test_an_unexpected_error_still_ends_the_operation(slot, monkeypatch):
    ops, _, _ = slot

    async def explode(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(ops._runner, "find_installed", explode)
    op = await _ended(ops, ops.submit(INSTALL, "dataphin"))

    assert (op.state, op.error["code"]) == ("failed", "bad_output")
    assert "boom" in op.error["message"]


async def test_cancel_before_the_operation_looked_changes_nothing(slot, monkeypatch):
    ops, _, _ = slot
    started = asyncio.Event()

    async def hang(*_args, **_kwargs):
        started.set()
        await asyncio.sleep(30)

    monkeypatch.setattr(ops._runner, "find_installed", hang)
    op = ops.submit(INSTALL, "dataphin")
    await started.wait()
    ops.cancel(INSTALL, "dataphin")
    await _ended(ops, op)

    assert (op.state, op.was_installed) == ("cancelled", None)


async def test_an_install_finishing_as_the_cancel_lands_is_reverted(slot, monkeypatch):
    """The cancel arrives after acli succeeded but before the operation ended."""
    ops, calls, _ = slot
    finished = asyncio.Event()
    release = asyncio.Event()
    real_install = ops._runner.install

    async def install_then_pause(name, **kwargs):
        tool = await real_install(name, **kwargs)
        finished.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            pass
        return tool

    monkeypatch.setattr(ops._runner, "install", install_then_pause)
    op = ops.submit(INSTALL, "dataphin")
    await finished.wait()
    ops.cancel(INSTALL, "dataphin")
    await _ended(ops, op)

    assert op.state == "cancelled" and op.result is None
    assert _args(calls)[-1] == ["tool", "uninstall"]


async def test_finished_operations_kept_in_memory_are_bounded(slot, monkeypatch):
    ops, _, _ = slot
    monkeypatch.setattr(ops_module, "RETAIN_COUNT", 1)
    monkeypatch.setenv("FAKE_ACLI_INSTALLED", "dataphin")

    first = await _ended(ops, ops.submit(INSTALL, "dataphin"))
    second = await _ended(ops, ops.submit(INSTALL, "dataphin"))

    assert list(ops._recent) == [second.id]
    assert ops.get(first.id).id == first.id


async def test_a_record_that_cannot_be_updated_does_not_stop_the_operation(
    slot, monkeypatch
):
    ops, _, _ = slot
    op = ops.submit(INSTALL, "dataphin")

    def refuse(_op):
        raise OSError("volume gone")

    monkeypatch.setattr(ops._store, "save", refuse)
    await _ended(ops, op)

    assert op.state == "succeeded"


def test_a_restart_with_no_leftover_process_still_recovers(fake_acli, tmp_path):
    runner, _ = fake_acli
    store_dir = tmp_path / "operations"
    store_dir.mkdir()
    (store_dir / ("5" * 32 + ".json")).write_text(json.dumps({
        "id": "5" * 32, "action": "install", "name": "x", "state": "running",
        "started_at": "2026-10-21T00:00:00Z", "pgid": 999_999_999,
    }))

    op = AcliOperations(runner, OperationStore(store_dir)).get("5" * 32)

    assert op.state == "interrupted"
