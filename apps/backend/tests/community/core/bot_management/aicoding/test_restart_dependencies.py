"""Unified restart assembly is lazy and preserves provider-specific safety policy."""
from types import SimpleNamespace
from unittest.mock import Mock, create_autospec

import pytest

from agentclaw.community.core.bot_management.engines.aicoding.restart_arca import (
    ArcaRestartTransport, RestartBackupTargetChanged,
)
from agentclaw.community.core.bot_management.engines.aicoding.restart_dependencies import RestartBackupDependencies
from agentclaw.community.core.bot_management.engines.aicoding.strategy import AicodingProvisioningStrategy
from agentclaw.community.core.bot_management.engines.default import DefaultProvisioningStrategy
from agentclaw.community.core.bot_management.engines.provisioning import BotProvisioningContext
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.di.modules.bot_management_module import BotManagementModule
from tests.community.core.bot_management.aicoding.test_restart_backup import OPERATION, response


def context(engine):
    return BotProvisioningContext(bot_id="b", owner_id="o", bot_type="personal", active_engine=engine)


def binding_args(provider):
    devices, repository = Mock(), Mock()
    devices.get_device.return_value = SimpleNamespace(
        device_id="logical", device_provider=provider, status="ACTIVE",
        device_props={"sandbox_id": "old-sandbox@8"},
    )
    repository.get_by_id_and_owner.return_value = {"binding_id": 7}
    return dict(binding_id=7, device_service_provider=lambda: devices,
                bot_repository=repository, operation_id=OPERATION)


@pytest.mark.parametrize("engine", ["aicoding", "claude_code"])
@pytest.mark.parametrize("provider", ["arca", "baas"])
def test_di_envelope_resolves_only_selected_provider(engine, provider):
    runtime = create_autospec(BaasService, instance=True)
    from tests.community.core.bot_management.aicoding.test_restart_fastdisk import detection, detection_command
    def result(cmd):
        return detection('skip_no_fastdisk') if cmd == detection_command() else response('not_mounted')
    execute = Mock(side_effect=lambda **kw: result(kw['shell_cmd']))
    runtime.list_devices_by_bot_uuid.return_value = [
        {"provider_device_id": "physical", "status": "ACTIVE"},
    ]
    runtime.post_bots_api.side_effect = lambda **kw: result(kw['payload']['cmd'])
    transport = ArcaRestartTransport(execute)
    injector = Mock()
    injector.get.side_effect = {BaasService: runtime, ArcaRestartTransport: transport}.__getitem__
    dependencies = BotManagementModule().restart_backup_dependencies(injector)
    injector.get.assert_not_called()  # Construction is cycle-safe and does no I/O.
    verify = AicodingProvisioningStrategy(engine).prepare_restart(
        context(engine), restart_dependencies=dependencies, **binding_args(provider),
    )
    verify()
    injector.get.assert_called_once_with(ArcaRestartTransport if provider == "arca" else BaasService)
    if provider == "arca":
        assert execute.call_count == 3
        assert all(call.kwargs["device"].device_props == {"sandbox_id": "old-sandbox@8"}
                   for call in execute.call_args_list)
        assert not runtime.mock_calls
    else:
        assert runtime.list_devices_by_bot_uuid.call_count == 2
        assert runtime.post_bots_api.call_count == 3
        execute.assert_not_called()


@pytest.mark.parametrize("engine", ["openclaw", "moltis", "hermes", "unknown"])
def test_non_coding_does_not_resolve_any_backup_dependency(engine):
    injector = Mock(side_effect=AssertionError("must not resolve"))
    dependencies = BotManagementModule().restart_backup_dependencies(injector)
    assert DefaultProvisioningStrategy().prepare_restart(
        context(engine), restart_dependencies=dependencies, **binding_args("arca"),
    ) is None
    injector.get.assert_not_called()


@pytest.mark.parametrize("engine", ["aicoding", "claude_code"])
def test_unbound_coding_does_not_resolve_backup_dependencies(engine):
    injector = Mock()
    dependencies = BotManagementModule().restart_backup_dependencies(injector)
    verify = AicodingProvisioningStrategy(engine).prepare_restart(
        context(engine), restart_dependencies=dependencies,
    )
    verify()
    injector.get.assert_not_called()


@pytest.mark.parametrize("provider", ["baas", "arca"])
def test_unconfigured_dependencies_preserve_degrade_vs_block(provider, caplog):
    strategy = AicodingProvisioningStrategy("aicoding")
    if provider == "arca":
        with pytest.raises(RestartBackupTargetChanged):
            strategy.prepare_restart(context("aicoding"), **binding_args(provider))
    else:
        verify = strategy.prepare_restart(context("aicoding"), **binding_args(provider))
        verify()
        assert "action=continue_restart" in caplog.text
        assert "data_loss_risk=true" in caplog.text


@pytest.mark.parametrize("provider", ["arca", "baas"])
def test_binding_database_failure_precedes_resource_resolution(provider):
    args = binding_args(provider)
    args["device_service_provider"]().get_device.side_effect = RuntimeError("database unavailable")
    resolve = Mock(side_effect=AssertionError("must not resolve"))
    with pytest.raises(RuntimeError, match="database unavailable"):
        AicodingProvisioningStrategy("aicoding").prepare_restart(
            context("aicoding"),
            restart_dependencies=RestartBackupDependencies(baas_runtime=resolve, arca_transport=resolve),
            **args,
        )
    resolve.assert_not_called()


def test_injector_can_construct_singleton_envelope_without_backend_bindings():
    from injector import Injector

    injector = Injector([BotManagementModule()], auto_bind=False)
    dependencies = injector.get(RestartBackupDependencies)
    assert dependencies is injector.get(RestartBackupDependencies)
    runtime, transport = Mock(), ArcaRestartTransport()
    injector.binder.bind(BaasService, to=runtime)
    injector.binder.bind(ArcaRestartTransport, to=transport)
    # The factories use the final graph, not dependencies eagerly captured
    # during BotService construction; corp bindings can supply the transport.
    assert dependencies.baas_runtime() is runtime
    assert dependencies.arca_transport() is transport
