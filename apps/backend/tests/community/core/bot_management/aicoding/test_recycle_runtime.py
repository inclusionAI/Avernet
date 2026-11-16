"""Recycle adapts backup policy without installing restart mutation state."""

import time

from agentclaw.community.core.bot_management.engines.aicoding.recycle_runtime import (
    ArcaRecycleTransport,
    observe_baas_release,
    submit_baas_release,
    submit_release,
)
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from agentclaw.community.core.bot_management.engines.aicoding import (
    restart_backup as backup,
)
from agentclaw.community.core.bot_management.engines.aicoding.restart_state import (
    BackupOperation,
    BackupPersistenceError,
    current_backup,
)
from agentclaw.community.core.bot_management.engines.aicoding.recycle_runtime import (
    AicodingRecycleRuntime,
)
from agentclaw.community.core.bot_management.engines.aicoding.recycle_state import (
    RecycleTargetChanged,
)


def test_persistence_failure_is_never_best_effort():
    op = BackupOperation(
        "a" * 32, time.time(), persist=MagicMock(side_effect=RuntimeError("db down"))
    )
    reset_handle = current_backup.set(op)
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
        current_backup.reset(reset_handle)


def test_confirmed_targets_survive_redelivery_without_restart_context():
    reset_handle = current_backup.set(
        BackupOperation("a" * 32, time.time(), confirmed={"s1"})
    )
    try:
        assert backup._confirmed_targets() == {"s1"}
    finally:
        current_backup.reset(reset_handle)


def test_backup_redelivery_does_not_reset_budget():
    op = BackupOperation("a" * 32, time.time() - 1501)
    reset_handle = current_backup.set(op)
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
        current_backup.reset(reset_handle)


def test_baas_new_physical_device_with_same_binding_blocks_release():
    devices, bots, deps = MagicMock(), MagicMock(), MagicMock()
    binding = SimpleNamespace(device_id="d1", device_provider="baas", device_props={})
    devices.get_device.return_value = binding
    runtime = AicodingRecycleRuntime(lambda: devices, bots, deps, MagicMock())
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
    runtime = AicodingRecycleRuntime(lambda: devices, bots, deps, MagicMock())
    bot = {"bot_id": "b1", "owner_id": "u1", "binding_id": 1}
    target = runtime.target(bot)
    record = {
        "operation_id": "a" * 32,
        "backup_started_at": time.time(),
        "target": target,
    }
    from agentclaw.community.core.bot_management.engines.aicoding.strategy import (
        AicodingProvisioningStrategy,
    )

    strategy, ctx = AicodingProvisioningStrategy("claude_code"), MagicMock()
    strategy._fastdisk_backup_blocking = MagicMock()
    strategy.prepare_restart = MagicMock()
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
        "agentclaw.community.core.bot_management.engines.aicoding.recycle_runtime.resolve_restart_strategy",
        lambda _: (ctx, strategy),
    )
    runtime.prepare(bot, record, save)
    assert current_backup.get() is None and current_restart.get() is None


@pytest.fixture
def baas():
    service = MagicMock()
    service.get_recycle_release_status.return_value = "ACTIVE"
    service.list_devices_by_bot_uuid.return_value = []
    return service


def test_baas_receipt_is_not_release_confirmation(baas):
    baas.destroy_bot.return_value = {
        "publish_id": 42,
        "secret": "not persisted",
    }
    result = submit_baas_release(
        baas, target={"bot_uuid": "b1"}, request_id="op1", owner_id="u1"
    )
    assert result == {"publish_id": 42, "request_id": "op1"}
    baas.get_publish_progress.return_value = {"status": "APPROVING"}
    assert (
        observe_baas_release(baas, target={"bot_uuid": "b1"}, release=result)
        == "UNKNOWN"
    )


def test_baas_empty_inventory_alone_is_unknown(baas):
    assert (
        observe_baas_release(baas, target={"bot_uuid": "b1"}, release=None) == "UNKNOWN"
    )


def test_baas_workflow_success_does_not_hide_live_device(baas):
    baas.get_publish_progress.return_value = {"status": "SUCCESS"}
    baas.list_devices_by_bot_uuid.return_value = [{"status": "ACTIVE"}]
    assert (
        observe_baas_release(
            baas, target={"bot_uuid": "b1"}, release={"publish_id": 42}
        )
        == "UNKNOWN"
    )


def test_baas_explicit_terminal_inventory_and_release_can_recover_lost_post(baas):
    baas.get_recycle_release_status.return_value = "RELEASED"
    assert (
        observe_baas_release(baas, target={"bot_uuid": "b1"}, release=None)
        == "RELEASED"
    )


def test_baas_failed_query_is_not_absence(baas):
    baas.get_recycle_release_status.side_effect = TimeoutError("query timed out")
    with pytest.raises(TimeoutError):
        observe_baas_release(baas, target={"bot_uuid": "b1"}, release=None)


def test_unknown_provider_never_resolves_a_different_runtime():
    dependencies, arca = MagicMock(), MagicMock()
    with pytest.raises(RuntimeError, match="provider unavailable"):
        submit_release(
            dependencies,
            arca,
            target={"provider": "missing"},
            request_id="op1",
            owner_id="u1",
        )
    dependencies.baas_runtime.assert_not_called()
    arca.assert_not_called()


def test_unconfigured_arca_transport_fails_closed():
    transport = ArcaRecycleTransport()
    with pytest.raises(RuntimeError):
        transport.submit({"sandbox_id": "s1"}, "op1")
    assert transport.observe({"sandbox_id": "s1"}) == "UNKNOWN"


@pytest.mark.parametrize("typed", [False, True])
def test_baas_untyped_404_is_not_release_evidence(typed):
    import httpx
    from agentclaw.community.core.service_bot.services.baas_service import BaasService

    service = object.__new__(BaasService)
    request = httpx.Request("GET", "https://example.invalid/api/v1/bots/b1")
    response = httpx.Response(
        404,
        request=request,
        json={
            "detail": {"error_code": "BOT_NOT_FOUND" if typed else "NOT_FOUND"},
        },
    )
    service._get_bots_api = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "not found", request=request, response=response
        )
    )
    if typed:
        assert service.get_recycle_release_status(bot_uuid="b1") == "RELEASED"
    else:
        with pytest.raises(httpx.HTTPStatusError):
            service.get_recycle_release_status(bot_uuid="b1")


def test_missing_logical_bot_does_not_hide_orphan_physical_device(baas):
    from agentclaw.community.core.service_bot.services.baas_service import (
        BaasBotNotFoundError,
    )

    baas.destroy_bot.side_effect = BaasBotNotFoundError("absent")
    receipt = submit_baas_release(
        baas, target={"bot_uuid": "b1"}, request_id="op1", owner_id="u1"
    )
    baas.get_recycle_release_status.return_value = "RELEASED"
    baas.list_devices_by_bot_uuid.return_value = [{"status": "ACTIVE"}]
    assert (
        observe_baas_release(baas, target={"bot_uuid": "b1"}, release=receipt)
        == "UNKNOWN"
    )
