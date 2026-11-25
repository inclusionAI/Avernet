"""Default-off ownership composition shares the manager dispatcher and lifecycle."""
from types import ModuleType, SimpleNamespace
import sys
from unittest.mock import Mock

import pytest
from injector import Injector, Module, singleton

from agentclaw.community.api.bot_manager_sync import BotManagerSyncProtocol
from agentclaw.community.api.bot_ownership_sync import BotOwnershipSyncProtocol
from agentclaw.community.core.bot_collaborator.manager_sync.dispatcher import BotManagerSyncDispatcher
from agentclaw.community.core.bot_collaborator.manager_sync.lifecycle import (
    BotManagerSyncLifecycle, DisabledBotManagerSyncLifecycle,
)
from agentclaw.community.core.bot_management.ownership_sync.notifications import DisabledBotOwnershipSync
from agentclaw.community.core.bot_management.ownership_sync.service import BotOwnershipSyncService
from agentclaw.community.core.repository.implementations.bot.ownership_sync import SqlOwnershipSyncReadRepository
from agentclaw.community.core.repository.protocols.bot.manager_sync import ManagerSyncReadRepository
from agentclaw.community.core.repository.protocols.bot.ownership_sync import OwnershipSyncReadRepository
from agentclaw.community.di import DeployProfile, build_injector
from agentclaw.community.di.bot_manager_sync_config import ManagerSyncConfig
from agentclaw.community.di.config import SecretNamesConfig
from agentclaw.community.di.modules.bot_manager_sync_module import BotManagerSyncModule, ManagerSyncStack
from agentclaw.community.di.modules.bot_ownership_sync_module import BotOwnershipSyncModule
from agentclaw.community.di.modules.infrastructure.test.bot_manager_sync import TestBotManagerSyncModule as RecordingModule
from agentclaw.community.di.profile_modules import modules_for
from agentclaw.community.kernel.bot_manager_sync import SubmissionResult, SyncReason, SyncWorkKind
from agentclaw.community.plugin_api.bcs_ownership import BcsOwnershipPlugin
from agentclaw.community.plugin_api.secret_resolver import SecretResolver
from agentclaw.community.plugins.community.bcs_managers import HttpBcsManagersPlugin
from agentclaw.community.plugins.community.bcs_ownership import HttpBcsOwnershipPlugin
from agentclaw.community.plugins.local.bcs_managers import RecordingBcsManagersPlugin
from agentclaw.community.plugins.local.bcs_ownership import RecordingBcsOwnershipPlugin

_KEY = "ownership-fixture-signing-key-0123456789abcdef"


class Settings(Module):
    def __init__(self, *, enabled=True, ownership=True, secret=_KEY, readers=True):
        self.config = ManagerSyncConfig(
            enabled=enabled, ownership_enabled=ownership,
            env="dev", bcs_base_url="http://127.0.0.1:9999",
        )
        self.secret = secret
        self.readers = readers

    def configure(self, binder):
        binder.bind(ManagerSyncConfig, to=self.config, scope=singleton)
        binder.bind(SecretNamesConfig, to=SecretNamesConfig(), scope=singleton)
        resolver = Mock()
        resolver.get_secret.return_value = (
            SimpleNamespace(secret_value=self.secret) if self.secret else None
        )
        binder.bind(SecretResolver, to=resolver, scope=singleton)
        if self.readers:
            binder.bind(ManagerSyncReadRepository, to=Mock(), scope=singleton)
            binder.bind(OwnershipSyncReadRepository, to=Mock(), scope=singleton)


def base_injector(**kwargs):
    return Injector([
        BotManagerSyncModule(), BotOwnershipSyncModule(), Settings(**kwargs),
    ], auto_bind=False)


def submit(notifier):
    return notifier.request_ownership_sync(
        bot_pk=7, env="dev", avernet_tenant="a", reason=SyncReason.OWNER_HANDOFF,
    )


def test_enabled_protocols_share_exact_dispatcher_and_stack():
    injector = base_injector()
    owner = injector.get(BotOwnershipSyncProtocol)
    assert owner is injector.get(BotManagerSyncProtocol)
    assert isinstance(owner, BotManagerSyncDispatcher)
    stack = injector.get(ManagerSyncStack)
    assert isinstance(stack.bcs, HttpBcsManagersPlugin)
    assert isinstance(stack.ownership, HttpBcsOwnershipPlugin)
    assert stack.bcs._mint_principal is stack.ownership._mint_principal
    assert stack.bcs._base_url == stack.ownership._base_url
    assert stack.bcs._client is None and stack.ownership._client is None
    assert owner._ownership_service._bcs is stack.ownership
    consumer = injector.get(BotOwnershipSyncService)
    assert consumer is injector.get(BotOwnershipSyncService)
    assert consumer._bcs is stack.ownership
    lifecycle = injector.get(BotManagerSyncLifecycle)
    assert lifecycle._dispatcher is owner
    assert lifecycle._ownership is stack.ownership


@pytest.mark.parametrize("enabled,ownership,secret", [
    (False, False, _KEY), (True, False, _KEY), (True, True, ""),
])
def test_disabled_and_unavailable_notifiers_retain_diagnostics(enabled, ownership, secret):
    injector = base_injector(enabled=enabled, ownership=ownership, secret=secret)
    notifier = injector.get(BotOwnershipSyncProtocol)
    assert isinstance(notifier, DisabledBotOwnershipSync)
    assert notifier is injector.get(BotOwnershipSyncProtocol)
    assert submit(notifier) is SubmissionResult.DISABLED
    assert submit(notifier) is SubmissionResult.DISABLED
    assert notifier._diagnostics.counted_by_kind[
        (SyncWorkKind.OWNERSHIP, "submit_admitted", SubmissionResult.DISABLED)
    ] == 2
    assert injector.get(ManagerSyncStack).ownership is None
    if enabled and not ownership:
        manager = injector.get(BotManagerSyncProtocol)
        assert isinstance(manager, BotManagerSyncDispatcher)
        assert manager._ownership_service is None
    else:
        assert isinstance(injector.get(BotManagerSyncLifecycle), DisabledBotManagerSyncLifecycle)
    with pytest.raises(RuntimeError, match="unavailable"):
        injector.get(BotOwnershipSyncService)


def test_disabled_does_not_resolve_any_resources():
    class Off(Module):
        def configure(self, binder):
            binder.bind(ManagerSyncConfig, to=ManagerSyncConfig(), scope=singleton)

    injector = Injector([BotManagerSyncModule(), BotOwnershipSyncModule(), Off()], auto_bind=False)
    assert submit(injector.get(BotOwnershipSyncProtocol)) is SubmissionResult.DISABLED
    assert not injector.get(ManagerSyncStack).available
    assert isinstance(injector.get(BotManagerSyncLifecycle), DisabledBotManagerSyncLifecycle)


@pytest.mark.parametrize("ownership,secret", [(False, _KEY), (True, "")])
def test_no_owner_http_construction_when_off_or_signer_missing(monkeypatch, ownership, secret):
    def forbidden(*args, **kwargs):
        pytest.fail("Owner HTTP resource constructed")

    monkeypatch.setattr(HttpBcsOwnershipPlugin, "__init__", forbidden)
    injector = base_injector(ownership=ownership, secret=secret)
    injector.get(BotManagerSyncProtocol)
    assert submit(injector.get(BotOwnershipSyncProtocol)) is SubmissionResult.DISABLED


def test_recording_stack_does_not_resolve_owner_when_flag_off():
    class NoOwner(Module):
        def configure(self, binder):
            def forbidden():
                pytest.fail("Owner plugin resolved while flag off")
            binder.bind(BcsOwnershipPlugin, to=forbidden)

    injector = Injector([
        BotManagerSyncModule(), BotOwnershipSyncModule(), RecordingModule(),
        Settings(ownership=False), NoOwner(),
    ], auto_bind=False)
    assert injector.get(ManagerSyncStack).ownership is None


def test_stack_old_factory_call_stays_managers_only():
    managers = RecordingBcsManagersPlugin()
    stack = ManagerSyncStack.available_with(managers)
    assert stack.bcs is managers and stack.ownership is None
    assert ManagerSyncStack.unavailable("missing").ownership is None


@pytest.mark.parametrize("profile,plugin_type", [
    (DeployProfile.TEST, RecordingBcsOwnershipPlugin),
    (DeployProfile.SINGLEBOX, HttpBcsOwnershipPlugin),
    (DeployProfile.COMMUNITY, HttpBcsOwnershipPlugin),
])
def test_profile_uses_existing_column_and_registers_owner_module(profile, plugin_type):
    injector = build_injector(profile=profile, extra_modules=[Settings(readers=False)])
    stack = injector.get(ManagerSyncStack)
    assert isinstance(stack.ownership, plugin_type)
    assert injector.get(BotOwnershipSyncProtocol) is injector.get(BotManagerSyncProtocol)
    assert isinstance(injector.get(OwnershipSyncReadRepository), SqlOwnershipSyncReadRepository)
    assert injector.get(BotOwnershipSyncService)._bcs is stack.ownership
    if profile is DeployProfile.TEST:
        assert stack.ownership is injector.get(BcsOwnershipPlugin)


def test_corp_test_reuses_recording_module(monkeypatch):
    from agentclaw.community.di import modules_bootstrap

    fake = ModuleType("agentclaw.community.di.modules.testing_devices_module")
    fake.TestingDevicesModule = type("TestingDevicesModule", (Module,), {})
    monkeypatch.setitem(sys.modules, fake.__name__, fake)
    monkeypatch.setattr(modules_bootstrap, "_test_corp_reuse_provider", lambda: [])
    selected = [module for module in modules_for(DeployProfile.CORP_TEST)
                if isinstance(module, RecordingModule)]
    assert len(selected) == 1
    injector = Injector([
        BotManagerSyncModule(), BotOwnershipSyncModule(), *selected, Settings(),
    ], auto_bind=False)
    stack = injector.get(ManagerSyncStack)
    assert stack.ownership is injector.get(BcsOwnershipPlugin)
    assert isinstance(stack.ownership, RecordingBcsOwnershipPlugin)
    assert injector.get(BotOwnershipSyncService)._bcs is stack.ownership
    assert injector.get(BotOwnershipSyncProtocol) is injector.get(BotManagerSyncProtocol)


def test_owner_notifier_is_disabled_if_shared_stack_has_no_owner_capability():
    class ManagersOnlyStack(Module):
        def configure(self, binder):
            binder.bind(ManagerSyncStack, to=ManagerSyncStack.available_with(
                RecordingBcsManagersPlugin(),
            ), scope=singleton)

    injector = Injector([
        BotManagerSyncModule(), BotOwnershipSyncModule(), Settings(), ManagersOnlyStack(),
    ], auto_bind=False)
    assert isinstance(injector.get(BotManagerSyncProtocol), BotManagerSyncDispatcher)
    assert isinstance(injector.get(BotOwnershipSyncProtocol), DisabledBotOwnershipSync)


@pytest.mark.parametrize("block", [
    {"ownership_enabled": "false"},
    {"enabled": False, "ownership_enabled": True},
    {"ownership_enabled": False, "owner_endpoint": "unused"},
])
def test_invalid_ownership_config_fails_eager_injector_build(monkeypatch, block):
    from agentclaw.community.di.modules import bot_manager_sync_module

    monkeypatch.setattr(
        bot_manager_sync_module, "read_user_config", lambda: {"bot_manager_sync": block},
    )
    with pytest.raises(ValueError):
        build_injector(profile=DeployProfile.TEST)


def test_enabled_repair_uses_shared_dispatcher_and_read_repository():
    from agentclaw.community.core.bot_management.ownership_sync.repair import (
        OwnershipSyncRepairService,
    )
    from agentclaw.community.kernel.bot_manager_sync import SyncKey

    injector = base_injector()
    repair = injector.get(OwnershipSyncRepairService)
    assert repair is injector.get(OwnershipSyncRepairService)
    assert repair._reader is injector.get(OwnershipSyncReadRepository)
    assert repair._notifier is injector.get(BotOwnershipSyncProtocol)
    assert repair._notifier is injector.get(BotManagerSyncProtocol)
    assert repair.request_bot(SyncKey("a", "dev", 7)) is SubmissionResult.QUEUED
    assert repair.request_bot(SyncKey("a", "dev", 7)) is SubmissionResult.COALESCED


@pytest.mark.parametrize("ownership,secret", [(False, _KEY), (True, "")])
def test_disabled_or_missing_signer_repair_does_not_resolve_reader(ownership, secret):
    from agentclaw.community.core.bot_management.ownership_sync.repair import (
        DisabledOwnershipSyncRepair, OwnershipSyncRepairService,
    )

    class NoReader(Module):
        def configure(self, binder):
            def forbidden():
                pytest.fail("disabled repair resolved reader")
            binder.bind(OwnershipSyncReadRepository, to=forbidden)

    injector = Injector([
        BotManagerSyncModule(), BotOwnershipSyncModule(),
        Settings(ownership=ownership, secret=secret), NoReader(),
    ], auto_bind=False)
    repair = injector.get(OwnershipSyncRepairService)
    assert isinstance(repair, DisabledOwnershipSyncRepair)
    assert repair is injector.get(OwnershipSyncRepairService)
    with pytest.raises(RuntimeError, match="^ownership sync repair unavailable$"):
        repair.request_page(avernet_tenant="a", env="dev", after_pk=0, limit=10)


def test_feature_off_repair_resolves_no_stack_or_resources():
    from agentclaw.community.core.bot_management.ownership_sync.repair import (
        DisabledOwnershipSyncRepair, OwnershipSyncRepairService,
    )

    class Off(Module):
        def configure(self, binder):
            binder.bind(ManagerSyncConfig, to=ManagerSyncConfig(), scope=singleton)

    injector = Injector([BotOwnershipSyncModule(), Off()], auto_bind=False)
    assert isinstance(injector.get(OwnershipSyncRepairService), DisabledOwnershipSyncRepair)


def test_managers_only_stack_disables_repair_without_readers():
    from agentclaw.community.core.bot_management.ownership_sync.repair import (
        DisabledOwnershipSyncRepair, OwnershipSyncRepairService,
    )

    class ManagersOnly(Module):
        def configure(self, binder):
            binder.bind(ManagerSyncStack, to=ManagerSyncStack.available_with(
                RecordingBcsManagersPlugin(),
            ), scope=singleton)

    injector = Injector([
        BotOwnershipSyncModule(), Settings(readers=False), ManagersOnly(),
    ], auto_bind=False)
    assert isinstance(injector.get(OwnershipSyncRepairService), DisabledOwnershipSyncRepair)
