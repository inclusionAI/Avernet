"""Recycle adapts backup policy without installing restart mutation state."""

import time

from agentclaw.community.core.bot_management.engines.aicoding.recycle_runtime import ArcaRecycleTransport
from agentclaw.community.core.bot_dormant.recycle_service import (
    observe_baas_release,
    submit_baas_release,
    submit_release,
)
from types import SimpleNamespace
from unittest.mock import MagicMock, create_autospec

import pytest
from agentclaw.community.core.bot_management.engines.aicoding.restart_dependencies import (
    BaasRecycleRuntime, BaasRestartRuntime, RestartBackupDependencies,
)
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.core.bot_management.engines.aicoding import (
    restart_backup as backup,
)
from agentclaw.community.core.bot_management.engines.aicoding.restart_state import (
    BackupOperation,
    BackupPersistenceError,
    current_backup,
)
from agentclaw.community.core.bot_dormant.recycle_service import (
    ManagedRecycleRuntime,
)
from agentclaw.community.core.bot_management.engines.restart_contract import (
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
    runtime = ManagedRecycleRuntime(lambda: devices, bots, deps, MagicMock())
    target = {
        "binding_id": 1,
        "provider": "baas",
        "device_id": "d1",
        "sandbox_id": None,
        "bot_uuid": "d1",
    }
    deps.baas_runtime.return_value = create_autospec(BaasRecycleRuntime, instance=True)
    deps.baas_runtime.return_value.list_devices_by_bot_uuid.return_value = [
        {"provider_device_id": "new-sandbox", "status": "ACTIVE"},
    ]
    with pytest.raises(RecycleTargetChanged):
        runtime.prepare(
            {"binding_id": 1},
            {"target": target, "physical_targets": ["old-sandbox"]},
            MagicMock(),
        )


@pytest.mark.parametrize("failed", [False, True])
def test_recycle_uses_durable_policy_and_backup_context_without_restart_mutation(
    monkeypatch, failed,
):
    from agentclaw.community.core.bot_management.engines import registry
    from agentclaw.community.core.bot_management.engines.aicoding.restart_arca import ArcaRestartTransport
    from agentclaw.community.core.bot_management.engines.aicoding.restart_state import current_restart
    from tests.community.core.bot_management.aicoding.test_restart_fastdisk import detection
    from tests.community.core.bot_management.aicoding.test_restart_backup import response

    config = MagicMock()
    config.get_value.return_value = False
    monkeypatch.setattr(registry, "_restart_common_config_provider", lambda: config)
    devices, bots = MagicMock(), MagicMock()
    devices.get_device.return_value = SimpleNamespace(
        device_id="d1", device_provider="arca", status="ACTIVE", device_props={"sandbox_id": "s1"},
    )
    bot = {"bot_id": "b1", "owner_id": "u1", "binding_id": 1, "active_engine": "claude_code", "bot_type": "personal", "ext": {}}
    bots.get_by_id_and_owner.return_value = bot
    record = {"operation_id": "a" * 32, "backup_started_at": time.time()}

    def execute(**kwargs):
        assert current_restart.get() is None
        assert current_backup.get().operation_id == record["operation_id"]
        assert kwargs["device"].device_props == {"sandbox_id": "s1"}
        if kwargs["shell_cmd"] == backup.detection_command():
            return detection()
        assert record["operation_id"] in kwargs["shell_cmd"]
        return response("failed") if failed else response("committed", backup={
            "status": "success", "generation_id": "g1", "operation_id": record["operation_id"],
        })

    command = MagicMock(side_effect=execute)
    deps = RestartBackupDependencies(arca_transport=lambda: ArcaRestartTransport(command))
    runtime = ManagedRecycleRuntime(lambda: devices, bots, deps, MagicMock())
    record["target"] = runtime.target(bot)

    def save(**changes):
        record.update(changes)
        # Another delivery's persisted switch must win over this False config read.
        record["fail_block"] = True
        return dict(record)

    if failed:
        from agentclaw.community.core.bot_management.engines.restart_contract import RecycleBackupBlocked
        with pytest.raises(RecycleBackupBlocked):
            runtime.prepare(bot, record, save)
    else:
        verify = runtime.prepare(bot, record, save)
        assert current_backup.get() is None
        verify()
    config.get_value.assert_called_once()
    assert record["fastdisk_targets"] == ["s1"]
    assert "warning" not in record
    assert current_backup.get() is None and current_restart.get() is None
    assert bot["ext"] == {}
    bots.update_by_owner.assert_not_called()


@pytest.fixture
def baas():
    service = create_autospec(BaasService, instance=True)
    service.is_physical_device_destroyed.return_value = False
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
    assert (
        observe_baas_release(baas, physical_targets=["s1@42"])
        == "UNKNOWN"
    )


def test_baas_empty_inventory_alone_is_unknown(baas):
    assert (
        observe_baas_release(baas, physical_targets=["s1@42"]) == "UNKNOWN"
    )


def test_physical_absence_is_authoritative_without_logical_release_reads(baas):
    baas.is_physical_device_destroyed.return_value = True
    assert observe_baas_release(baas, physical_targets=["s1@42"]) == "RELEASED"
    baas.list_devices_by_bot_uuid.assert_not_called()


def test_baas_failed_query_is_not_absence(baas):
    baas.is_physical_device_destroyed.side_effect = TimeoutError("query timed out")
    with pytest.raises(TimeoutError):
        observe_baas_release(baas, physical_targets=["s1@42"])


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


@pytest.mark.parametrize("status,code", [
    (404, "DEVICE_NOT_FOUND"), (404, "NOT_FOUND"), (404, "BOT_NOT_FOUND"),
    (404, "TEMPLATE_NOT_FOUND"), (404, "DEVICE_UNAVAILABLE"),
    (500, "DEVICE_NOT_FOUND"), (500, "DEVICE_DESTROY_FAILED"),
    (401, "DEVICE_NOT_FOUND"), (403, "DEVICE_NOT_FOUND"),
])
def test_only_typed_physical_not_found_confirms_destruction(status, code):
    import httpx
    service = object.__new__(BaasService)
    service._tenant = "test"
    service._http = MagicMock()
    service._http.get.return_value = httpx.Response(
        status, request=httpx.Request("GET", "https://example.invalid/info"),
        json={"detail": {"error_code": code}},
    )
    if status == 404 and code == "DEVICE_NOT_FOUND":
        assert service.is_physical_device_destroyed(provider_device_id="s1@42") is True
    else:
        with pytest.raises(httpx.HTTPStatusError):
            service.is_physical_device_destroyed(provider_device_id="s1@42")
    service._http.get.assert_called_once_with(
        "/api/v1/paas/devices/s1%4042/info", params={"tenant": "test"}, timeout=30.0,
    )


@pytest.mark.parametrize("status", ["RUNNING", "STOPPED", "RELEASED", "UNKNOWN"])
def test_present_physical_resource_is_not_destroyed(status):
    import httpx
    service = object.__new__(BaasService)
    service._tenant = "test"
    service._http = MagicMock()
    service._http.get.return_value = httpx.Response(
        200, request=httpx.Request("GET", "https://example.invalid/info"),
        json={"code": 0, "data": {"status": status}},
    )
    assert service.is_physical_device_destroyed(provider_device_id="s1@42") is False


def test_missing_logical_bot_does_not_hide_orphan_physical_device(baas):
    from agentclaw.community.core.service_bot.services.baas_service import (
        BaasBotNotFoundError,
    )

    baas.destroy_bot.side_effect = BaasBotNotFoundError("absent")
    submit_baas_release(
        baas, target={"bot_uuid": "b1"}, request_id="op1", owner_id="u1"
    )
    assert observe_baas_release(baas, physical_targets=["s1@42"]) == "UNKNOWN"


@pytest.mark.parametrize("targets", [None, [], [""], [None]])
def test_missing_pinned_physical_targets_never_confirm_release(baas, targets):
    baas.is_physical_device_destroyed.return_value = True
    assert observe_baas_release(baas, physical_targets=targets) == "UNKNOWN"
    baas.is_physical_device_destroyed.assert_not_called()


def test_all_pinned_targets_must_be_physically_absent(baas):
    baas.is_physical_device_destroyed.side_effect = [True, False]
    assert observe_baas_release(baas, physical_targets=["s1@42", "s2@43"]) == "UNKNOWN"
    assert [c.kwargs["provider_device_id"] for c in baas.is_physical_device_destroyed.call_args_list] == ["s1@42", "s2@43"]
    baas.destroy_bot.assert_not_called()


def test_restart_only_runtime_cannot_silently_supply_recycle_capabilities():
    deps = RestartBackupDependencies.for_instance(create_autospec(BaasRestartRuntime, instance=True))
    runtime = ManagedRecycleRuntime(MagicMock(), MagicMock(), deps, MagicMock())
    with pytest.raises(RuntimeError, match="capabilities unavailable"):
        runtime.submit_release({"binding_id": 1, "provider": "baas", "bot_uuid": "b1"}, "op1", "u1")


def test_real_baas_service_supplies_declared_recycle_capabilities():
    assert isinstance(object.__new__(BaasService), BaasRecycleRuntime)


def test_runtime_observes_persisted_physical_targets_through_declared_capability(baas):
    baas.is_physical_device_destroyed.return_value = True
    runtime = ManagedRecycleRuntime(
        MagicMock(), MagicMock(), RestartBackupDependencies.for_instance(baas), MagicMock(),
    )
    assert runtime.observe_release(
        {"binding_id": 1, "provider": "baas", "bot_uuid": "b1"}, None, ["s1@42"],
    ) == "RELEASED"
    baas.is_physical_device_destroyed.assert_called_once_with(provider_device_id="s1@42")


def test_prepare_pins_terminal_inventory_rows_instead_of_forgetting_physical_ids(baas):
    from tests.community.core.bot_management.aicoding.test_restart_fastdisk import detection

    baas.list_devices_by_bot_uuid.return_value = [
        {"provider_device_id": "s1@42", "status": "ACTIVE"},
        {"provider_device_id": "s2@42", "status": "STOPPED"},
        {"provider_device_id": "s3@42", "status": "RELEASED"},
    ]
    baas.post_bots_api.return_value = detection("skip_no_fastdisk")
    devices, bots = MagicMock(), MagicMock()
    devices.get_device.return_value = SimpleNamespace(
        device_id="d1", device_provider="baas", device_props={}, status="ACTIVE",
    )
    bot = {"bot_id": "b1", "owner_id": "u1", "bot_type": "personal", "active_engine": "claude_code", "binding_id": 1}
    bots.get_by_id_and_owner.return_value = bot
    runtime = ManagedRecycleRuntime(lambda: devices, bots, RestartBackupDependencies.for_instance(baas), MagicMock())
    record = {"target": runtime.target(bot), "operation_id": "a" * 32, "backup_started_at": time.time(), "fail_block": False}

    def save(**changes):
        record.update(changes)
        return dict(record)

    runtime.prepare(bot, record, save)()
    assert record["physical_targets"] == ["s1@42", "s2@42", "s3@42"]
    baas.destroy_bot.assert_not_called()


def test_bound_bot_without_physical_inventory_cannot_begin_release(baas):
    devices = MagicMock()
    devices.get_device.return_value = SimpleNamespace(device_id="d1", device_provider="baas", device_props={})
    runtime = ManagedRecycleRuntime(lambda: devices, MagicMock(), RestartBackupDependencies.for_instance(baas), MagicMock())
    bot = {"binding_id": 1}
    with pytest.raises(RecycleTargetChanged, match="Physical targets missing"):
        runtime.prepare(bot, {"target": runtime.target(bot)}, MagicMock())
    baas.destroy_bot.assert_not_called()


def test_unbound_bot_does_not_require_a_physical_probe():
    deps = RestartBackupDependencies(baas_runtime=MagicMock(side_effect=AssertionError("must stay lazy")))
    runtime = ManagedRecycleRuntime(MagicMock(), MagicMock(), deps, MagicMock())
    assert runtime.observe_release({"binding_id": None}, None, None) == "RELEASED"


@pytest.mark.parametrize("engine,mounted", [("openclaw", False), ("hermes", False), ("claude_code", False), ("claude_code", True), ("aicoding", True)])
def test_shared_recycle_only_backs_up_confirmed_fastdisk_coding(engine, mounted, monkeypatch):
    from agentclaw.community.core.bot_management.engines import registry
    from agentclaw.community.core.bot_management.engines.aicoding.restart_arca import ArcaRestartTransport
    from tests.community.core.bot_management.aicoding.test_restart_fastdisk import detection
    from tests.community.core.bot_management.aicoding.test_restart_backup import response

    config = MagicMock()
    config.get_value.return_value = True
    monkeypatch.setattr(registry, "_restart_common_config_provider", lambda: config)
    devices, bots, execute = MagicMock(), MagicMock(), MagicMock()
    devices.get_device.return_value = SimpleNamespace(device_id="d1", device_provider="arca", status="ACTIVE", device_props={"sandbox_id": "s1"})
    op = "a" * 32
    execute.side_effect = [detection("run" if mounted else "skip_no_fastdisk"), response("committed", backup={"status": "success", "generation_id": "g1", "operation_id": op})]
    deps = RestartBackupDependencies(arca_transport=lambda: ArcaRestartTransport(execute))
    runtime = ManagedRecycleRuntime(lambda: devices, bots, deps, MagicMock())
    bot = {"bot_id": "b1", "owner_id": "u1", "bot_type": "personal", "active_engine": engine, "status": "ACTIVE", "binding_id": 1}
    bots.get_by_id_and_owner.return_value = bot
    record = {"target": runtime.target(bot), "operation_id": op, "backup_started_at": time.time()}

    def save(**changes):
        record.update(changes)
        return dict(record)

    runtime.prepare(bot, record, save)()
    coding = engine in {"aicoding", "claude_code"}
    assert execute.call_count == (2 if coding and mounted else 1 if coding else 0)
    if coding and mounted:
        config.get_value.assert_called_once()
        assert record["fastdisk_targets"] == ["s1"] and record["fail_block"] is True
    else:
        config.get_value.assert_not_called()
        assert "fastdisk_targets" not in record and "fail_block" not in record


def test_fastdisk_policy_write_failure_escapes_baas_compatibility_fallback(baas, monkeypatch):
    from agentclaw.community.core.bot_management.engines import registry
    from tests.community.core.bot_management.aicoding.test_restart_fastdisk import detection

    config = MagicMock()
    config.get_value.return_value = True
    monkeypatch.setattr(registry, "_restart_common_config_provider", lambda: config)
    devices, bots = MagicMock(), MagicMock()
    devices.get_device.return_value = SimpleNamespace(device_id="d1", device_provider="baas", status="ACTIVE", device_props={})
    baas.list_devices_by_bot_uuid.return_value = [{"provider_device_id": "s1@42", "status": "ACTIVE"}]
    baas.post_bots_api.return_value = detection()
    runtime = ManagedRecycleRuntime(lambda: devices, bots, RestartBackupDependencies.for_instance(baas), MagicMock())
    bot = {"bot_id": "b1", "owner_id": "u1", "bot_type": "personal", "active_engine": "claude_code", "binding_id": 1}
    record = {"target": runtime.target(bot), "operation_id": "a" * 32, "backup_started_at": time.time()}

    def save(**changes):
        if "fail_block" in changes:
            raise RuntimeError("database unavailable")
        record.update(changes)
        return dict(record)

    with pytest.raises(BackupPersistenceError):
        runtime.prepare(bot, record, save)
    assert baas.post_bots_api.call_count == 1  # Only the read-only mount probe.
    baas.destroy_bot.assert_not_called()
