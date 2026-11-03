"""Default-off configuration and shared production/singlebox delivery wiring."""
from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import jwt
import pytest
from injector import Injector, Module, singleton

from agentclaw.community.api.bot_manager_sync import BotManagerSyncProtocol
from agentclaw.community.core.bot_collaborator.manager_sync.dispatcher import (
    BotManagerSyncDispatcher, DisabledBotManagerSync,
)
from agentclaw.community.core.bot_collaborator.manager_sync.lifecycle import (
    BotManagerSyncLifecycle, DisabledBotManagerSyncLifecycle,
)
from agentclaw.community.core.bot_collaborator.manager_sync.repair import ManagerSyncRepairService
from agentclaw.community.di import DeployProfile, build_injector
from agentclaw.community.di.bot_manager_sync_config import ManagerSyncConfig, parse_manager_sync_config
from agentclaw.community.di.config import SecretNamesConfig
from agentclaw.community.di.modules.bot_manager_sync_module import BotManagerSyncModule, ManagerSyncStack
from agentclaw.community.di.profile_modules import modules_for
from agentclaw.community.kernel.bot_manager_sync import SubmissionResult, SyncReason
from agentclaw.community.plugin_api.secret_resolver import SecretResolver
from agentclaw.community.plugins.community.bcs_managers import HttpBcsManagersPlugin
from agentclaw.community.plugins.local.bcs_managers import RecordingBcsManagersPlugin

_KEY = "manager-sync-fixture-signing-key-0123456789abcdef"
_CONFIG = ManagerSyncConfig(enabled=True, env="dev", bcs_base_url="http://127.0.0.1:9999")


class _Secrets:
    def __init__(self, value=_KEY):
        self.value = value

    def get_secret(self, name):
        return SimpleNamespace(secret_value=self.value) if self.value else None


class _ConfigModule(Module):
    def __init__(self, *, secret=_KEY, config=_CONFIG):
        self.secret, self.config = secret, config

    def configure(self, binder):
        binder.bind(ManagerSyncConfig, to=self.config, scope=singleton)
        binder.bind(SecretResolver, to=_Secrets(self.secret), scope=singleton)
        binder.bind(SecretNamesConfig, to=SecretNamesConfig(), scope=singleton)


def test_default_and_enabled_config():
    assert parse_manager_sync_config(None) == ManagerSyncConfig()
    assert parse_manager_sync_config({}) == ManagerSyncConfig()
    assert parse_manager_sync_config({"enabled": True, "env": "dev", "bcs_base_url": "http://127.0.0.1:9999"}) == _CONFIG


@pytest.mark.parametrize("name", [
    "coordination_mode", "coordination_generation", "target_id",
    "gateway_base_url", "app_secret_names_by_tenant", "retry_mode",
])
def test_removed_or_unknown_config_is_rejected(name):
    with pytest.raises(ValueError, match="unknown"):
        parse_manager_sync_config({"enabled": False, name: "old-value"})


@pytest.mark.parametrize("value", ["true", 1, None])
def test_boolean_is_strict(value):
    with pytest.raises(ValueError, match="enabled"):
        parse_manager_sync_config({"enabled": value})


@pytest.mark.parametrize("name", ["env", "bcs_base_url"])
@pytest.mark.parametrize("value", [None, 7, []])
def test_strings_are_strict_even_when_disabled(name, value):
    with pytest.raises(ValueError, match=name):
        parse_manager_sync_config({name: value})


@pytest.mark.parametrize("name", ["env", "bcs_base_url"])
def test_enabled_requires_target_fields(name):
    values = {"enabled": True, "env": "dev", "bcs_base_url": "https://bcs.example.test"}
    values.pop(name)
    with pytest.raises(ValueError, match=name):
        parse_manager_sync_config(values)


@pytest.mark.parametrize("url", [
    "not-a-url", "ftp://bcs.example.test", "http://", "https://bcs.example.test/prefix",
    "https://bcs.example.test?query=1", "https://bcs.example.test#fragment",
    "https://user:password@bcs.example.test", "https://bcs.example.test:bad",
    "https://bcs.example.test:65536", "https://bcs.example.test:0",
    "http://[invalid", "http://invalid host",
])
def test_url_is_validated_when_disabled(url):
    with pytest.raises(ValueError, match="bcs_base_url"):
        parse_manager_sync_config({"bcs_base_url": url})


def test_non_mapping_is_rejected():
    with pytest.raises(ValueError, match="mapping"):
        parse_manager_sync_config("enabled")


def test_disabled_base_module_needs_no_secrets_or_cache():
    class Disabled(Module):
        def configure(self, binder):
            binder.bind(ManagerSyncConfig, to=ManagerSyncConfig(), scope=singleton)

    injector = Injector([BotManagerSyncModule(), Disabled()], auto_bind=False)
    assert not injector.get(ManagerSyncStack).available
    assert isinstance(injector.get(BotManagerSyncProtocol), DisabledBotManagerSync)
    assert isinstance(injector.get(BotManagerSyncLifecycle), DisabledBotManagerSyncLifecycle)


def test_enabled_base_module_needs_no_cache_or_coordinator():
    injector = Injector([BotManagerSyncModule(), _ConfigModule()], auto_bind=False)
    stack = injector.get(ManagerSyncStack)
    assert stack.available and isinstance(stack.bcs, HttpBcsManagersPlugin)
    token = stack.bcs._mint_principal("tenant-a")
    claims = jwt.decode(token, _KEY, algorithms=["HS256"], issuer="backend", audience="bcs")
    assert claims["principals"][0]["app"]["app_name"] == "backend"
    assert claims["principals"][0]["tenant"] == "tenant-a"
    assert jwt.get_unverified_header(token)["kid"] == "bare"


def test_missing_signing_key_disables_sync_without_affecting_business(caplog):
    injector = Injector([BotManagerSyncModule(), _ConfigModule(secret="")], auto_bind=False)
    assert injector.get(ManagerSyncStack).reason == "principal-signing-key-required"
    assert isinstance(injector.get(BotManagerSyncProtocol), DisabledBotManagerSync)
    assert isinstance(injector.get(BotManagerSyncLifecycle), DisabledBotManagerSyncLifecycle)
    assert "no principal signing key resolved" in caplog.text


def test_corp_test_profile_wires_recording_delivery_without_outbound_http(
    monkeypatch,
):
    from agentclaw.community.di import modules_bootstrap

    fake_devices_module = ModuleType(
        "agentclaw.community.di.modules.testing_devices_module"
    )
    setattr(
        fake_devices_module,
        "TestingDevicesModule",
        type("TestingDevicesModule", (Module,), {}),
    )
    monkeypatch.setitem(sys.modules, fake_devices_module.__name__, fake_devices_module)
    monkeypatch.setattr(modules_bootstrap, "_test_corp_reuse_provider", lambda: [])

    selected = [
        module for module in modules_for(DeployProfile.CORP_TEST)
        if type(module).__name__ == "TestBotManagerSyncModule"
    ]

    assert len(selected) == 1
    injector = Injector(
        [BotManagerSyncModule(), selected[0], _ConfigModule()], auto_bind=False
    )
    assert isinstance(
        injector.get(ManagerSyncStack).bcs, RecordingBcsManagersPlugin
    )


@pytest.mark.parametrize("profile,plugin_type", [
    (DeployProfile.TEST, RecordingBcsManagersPlugin),
    (DeployProfile.SINGLEBOX, HttpBcsManagersPlugin),
])
def test_profile_wires_expected_delivery(profile, plugin_type):
    injector = build_injector(profile=profile, extra_modules=[_ConfigModule()])
    stack = injector.get(ManagerSyncStack)
    assert isinstance(stack.bcs, plugin_type)
    protocol = injector.get(BotManagerSyncProtocol)
    assert isinstance(protocol, BotManagerSyncDispatcher)
    assert injector.get(ManagerSyncRepairService)._notifier is protocol
    assert protocol.request_sync(bot_pk=7, env="pre", avernet_tenant="a", reason=SyncReason.REPAIR) is SubmissionResult.REJECTED


@pytest.mark.asyncio
async def test_lifecycle_runs_and_shuts_down_same_dispatcher():
    injector = build_injector(profile=DeployProfile.TEST, extra_modules=[_ConfigModule()])
    protocol = injector.get(BotManagerSyncProtocol)
    lifecycle = injector.get(BotManagerSyncLifecycle)
    await lifecycle.startup()
    try:
        assert protocol.request_sync(bot_pk=424242, env="dev", avernet_tenant="a", reason=SyncReason.OWNER_HANDOFF) is SubmissionResult.QUEUED
        assert await protocol.drain(5)
        assert injector.get(ManagerSyncStack).bcs.calls == []  # missing Bot is not an empty roster
    finally:
        await lifecycle.shutdown()
    assert protocol.request_sync(bot_pk=424242, env="dev", avernet_tenant="a", reason=SyncReason.REPAIR) is SubmissionResult.REJECTED


@pytest.mark.parametrize("url", ["https://bcs.example.test/", "http://[::1]:21000"])
def test_origin_allows_root_slash_and_ipv6(url):
    assert parse_manager_sync_config({"bcs_base_url": url}).bcs_base_url == url
