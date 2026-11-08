"""Recycle adapts backup policy without installing restart mutation state."""

from unittest.mock import MagicMock
from types import SimpleNamespace
import time

import pytest

from agentclaw.community.core.bot_dormant.recycle_runtime import DeviceRecycleRuntime
from agentclaw.community.core.bot_dormant.recycle_state import RecycleTargetChanged
from agentclaw.community.core.bot_management.engines.aicoding.backup_operation import (
    BackupOperation,
    BackupPersistenceError,
    current_backup,
)
from agentclaw.community.core.bot_management.engines.aicoding import (
    restart_backup as backup,
)


def test_persistence_failure_is_never_best_effort():
    op = BackupOperation(
        "a" * 32, time.time(), persist=MagicMock(side_effect=RuntimeError("db down"))
    )
    token = current_backup.set(op)
    try:
        with pytest.raises(BackupPersistenceError):
            backup._best_effort(
                lambda: op.confirm("s1"),
                phase="prepare",
                bot_id="b1",
                target_id="s1",
                operation_id=op.operation_id,
            )
    finally:
        current_backup.reset(token)


def test_confirmed_targets_survive_redelivery_without_restart_context():
    token = current_backup.set(BackupOperation("a" * 32, time.time(), confirmed={"s1"}))
    try:
        assert backup._confirmed_targets() == {"s1"}
    finally:
        current_backup.reset(token)


def test_backup_redelivery_does_not_reset_budget():
    op = BackupOperation("a" * 32, time.time() - 1501)
    token = current_backup.set(op)
    execute = MagicMock()
    try:
        with pytest.raises(TimeoutError):
            backup.prepare_backup(
                execute=execute,
                operation_id=op.operation_id,
                bot_id="b1",
                target_id="s1",
            )
        execute.assert_not_called()
    finally:
        current_backup.reset(token)


def test_baas_new_physical_device_with_same_binding_blocks_release():
    devices, bots, deps = MagicMock(), MagicMock(), MagicMock()
    binding = SimpleNamespace(device_id="d1", device_provider="baas", device_props={})
    devices.get_device.return_value = binding
    runtime = DeviceRecycleRuntime(lambda: devices, bots, deps)
    target = {
        "binding_id": 1,
        "provider": "baas",
        "device_id": "d1",
        "sandbox_id": None,
        "bot_uuid": "d1",
    }
    deps.baas_runtime.return_value.list_devices_by_bot_uuid.return_value = [
        {"provider_device_id": "new-sandbox", "status": "ACTIVE"},
    ]
    with pytest.raises(RecycleTargetChanged):
        runtime.prepare(
            {"binding_id": 1},
            {"target": target, "physical_targets": ["old-sandbox"]},
            MagicMock(),
        )


def test_recycle_uses_durable_policy_and_backup_context_without_restart_mutation(
    monkeypatch,
):
    from agentclaw.community.core.bot_management.engines.aicoding.restart_state import (
        current_restart,
    )

    devices, bots, deps = MagicMock(), MagicMock(), MagicMock()
    devices.get_device.return_value = SimpleNamespace(
        device_id="d1", device_provider="arca", device_props={"sandbox_id": "s1"}
    )
    runtime = DeviceRecycleRuntime(lambda: devices, bots, deps)
    bot = {"bot_id": "b1", "owner_id": "u1", "binding_id": 1}
    target = runtime.target(bot)
    record = {
        "operation_id": "a" * 32,
        "backup_started_at": time.time(),
        "target": target,
    }
    strategy, ctx = MagicMock(), MagicMock()
    strategy._fastdisk_backup_blocking.return_value = False

    def save(**changes):
        record.update(changes)
        # Simulate another delivery winning the persisted configuration read.
        record["fail_block"] = True
        return dict(record)

    def prepare(*args, **kwargs):
        assert current_restart.get() is None
        assert current_backup.get().operation_id == record["operation_id"]
        assert kwargs["blocking_enabled"]() is True
        assert kwargs["operation_id"] == record["operation_id"]
        return None

    strategy.prepare_restart.side_effect = prepare
    monkeypatch.setattr(
        "agentclaw.community.core.bot_dormant.recycle_runtime.resolve_restart_strategy",
        lambda _: (ctx, strategy),
    )
    runtime.prepare(bot, record, save)
    assert current_backup.get() is None and current_restart.get() is None
