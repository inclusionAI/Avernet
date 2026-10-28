"""Strict parse of ``user_config.bot_manager_sync`` + the Task 6 wiring.

Parse pins (the config module is independent — no provider lives here):

- the missing block is the disabled default; unknown keys / wrong types
  are structural config errors regardless of ``enabled``;
- enabling requires every field — indistinct emptiness is refused;
- endpoint-style values must be http(s) URLs, never anything guessable
  from source defaults.

Wiring pins (test profile binds Recording + Memory via the new
infrastructure module):

- the default binding is the zero-resource disabled posture, and the
  lifecycle participant is the disabled no-op lifecycle;
- explicitly enabling (config override module + fixture secrets)
  resolves a real dispatcher on the recording/memory stack;
- enabling without credentials makes the syncer unavailable with a
  diagnostic while the rest of the DI graph keeps resolving.
"""
from __future__ import annotations

import threading
import time

import pytest
from injector import Module, singleton

from agentclaw.community.api.bot_manager_sync import BotManagerSyncProtocol
from agentclaw.community.core.bot_collaborator.manager_sync.dispatcher import (
    BotManagerSyncDispatcher,
    DisabledBotManagerSync,
)
from agentclaw.community.core.bot_collaborator.manager_sync.lifecycle import (
    BotManagerSyncLifecycle,
    DisabledBotManagerSyncLifecycle,
)
from agentclaw.community.di import DeployProfile, build_injector
from agentclaw.community.di.bot_manager_sync_config import (
    ManagerSyncConfig,
    parse_manager_sync_config,
)
from agentclaw.community.di.modules.bot_manager_sync_module import (
    BotManagerSyncModule,
    ManagerSyncStack,
    )
from agentclaw.community.kernel.bot_manager_sync import (
    SpaceChange,
    SubmissionResult,
    SyncReason,
)
from agentclaw.community.plugin_api.secret_resolver import SecretResolver


# --------------------------------------------------------------------------- parse


def test_missing_block_is_the_disabled_default():
    config = parse_manager_sync_config(None)
    assert config == ManagerSyncConfig()
    assert parse_manager_sync_config({}) == ManagerSyncConfig()


def test_valid_full_block_parses():
    config = parse_manager_sync_config(
        {
            "enabled": True,
            "target_id": "bcs-prod-a",
            "env": "prod",
            "gateway_base_url": "https://gateway.example.test",
            "app_secret_names_by_tenant": {"teamclaw": "SECRET_NAME_BCS_APP"},
            "coordination_generation": "ops-init-2026-10-27",
        }
    )
    assert config.enabled is True
    assert config.target_id == "bcs-prod-a"
    assert config.env == "prod"
    assert config.app_secret_names_by_tenant == {"teamclaw": "SECRET_NAME_BCS_APP"}
    assert config.coordination_generation == "ops-init-2026-10-27"


def test_disabled_block_with_all_fields_parses():
    config = parse_manager_sync_config(
        {
            "enabled": False,
            "target_id": "bcs-prod-a",
            "env": "prod",
            "gateway_base_url": "https://gateway.example.test",
            "app_secret_names_by_tenant": {"teamclaw": "S"},
            "coordination_generation": "g",
        }
    )
    assert config.enabled is False


def test_unknown_key_is_a_config_error():
    with pytest.raises(ValueError, match="unknown"):
        parse_manager_sync_config({"enabled": False, "retry_mode": "aggressive"})


def test_non_mapping_block_is_a_config_error():
    with pytest.raises(ValueError, match="bot_manager_sync"):
        parse_manager_sync_config("yes")


@pytest.mark.parametrize("bad_enabled", ["true", 1, None])
def test_non_bool_enabled_is_a_config_error(bad_enabled):
    with pytest.raises(ValueError, match="enabled"):
        parse_manager_sync_config({"enabled": bad_enabled})


@pytest.mark.parametrize("field", ["target_id", "env", "gateway_base_url",
                                   "coordination_generation"])
def test_enabled_requires_every_field(field):
    block = {
        "enabled": True,
        "target_id": "t",
        "env": "dev",
        "gateway_base_url": "http://127.0.0.1:9999",
        "app_secret_names_by_tenant": {"teamclaw": "S"},
        "coordination_generation": "g",
    }
    block.pop(field)
    with pytest.raises(ValueError, match=field):
        parse_manager_sync_config(block)


def test_non_string_fields_are_config_errors_even_when_disabled():
    with pytest.raises(ValueError, match="target_id"):
        parse_manager_sync_config({"enabled": False, "target_id": 7})


def test_gateway_url_must_be_http_s():
    with pytest.raises(ValueError, match="gateway_base_url"):
        parse_manager_sync_config(
            {
                "enabled": True,
                "target_id": "t",
                "env": "dev",
                "gateway_base_url": "ftp://gateway.example.test",
                "app_secret_names_by_tenant": {"teamclaw": "S"},
                "coordination_generation": "g",
            }
        )
    # Also validated when the feature itself is off.
    with pytest.raises(ValueError, match="gateway_base_url"):
        parse_manager_sync_config(
            {"enabled": False, "gateway_base_url": "not-a-url"}
        )


def test_app_secret_map_must_map_nonempty_strings():
    with pytest.raises(ValueError, match="app_secret_names_by_tenant"):
        parse_manager_sync_config(
            {
                "enabled": True,
                "target_id": "t",
                "env": "dev",
                "gateway_base_url": "http://127.0.0.1:9999",
                "app_secret_names_by_tenant": ["teamclaw"],
                "coordination_generation": "g",
            }
        )
    with pytest.raises(ValueError, match="app_secret_names_by_tenant"):
        parse_manager_sync_config(
            {
                "enabled": True,
                "target_id": "t",
                "env": "dev",
                "gateway_base_url": "http://127.0.0.1:9999",
                "app_secret_names_by_tenant": {"teamclaw": ""},
                "coordination_generation": "g",
            }
        )


def test_enabled_requires_a_nonempty_secret_map():
    with pytest.raises(ValueError, match="app_secret_names_by_tenant"):
        parse_manager_sync_config(
            {
                "enabled": True,
                "target_id": "t",
                "env": "dev",
                "gateway_base_url": "http://127.0.0.1:9999",
                "app_secret_names_by_tenant": {},
                "coordination_generation": "g",
            }
        )


# --------------------------------------------------------------------------- wiring


class _FixtureSecretResolver:
    """SecretResolver double: ``.secret_value`` present for every name."""

    def get_secret(self, secret_name: str):
        import types

        return types.SimpleNamespace(secret_value=f"fixture-{secret_name}")


_ENABLED_CONFIG = ManagerSyncConfig(
    enabled=True,
    target_id="bcs-target-fixture",
    env="dev",
    gateway_base_url="http://127.0.0.1:9999",
    app_secret_names_by_tenant={"teamclaw": "SECRET_NAME_BCS_APP"},
    coordination_generation="ops-generation-fixture",
)


class _EnabledConfigModule(Module):
    """Test-only override: install the enabled config (+ fixture secrets)."""

    def __init__(self, *, with_secrets: bool = True) -> None:
        self._with_secrets = with_secrets

    def configure(self, binder):
        binder.bind(ManagerSyncConfig, to=_ENABLED_CONFIG, scope=singleton)
        if self._with_secrets:
            binder.bind(
                SecretResolver, to=_FixtureSecretResolver(), scope=singleton
            )


def _wait_thread_gone(name: str, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(t.name == name for t in threading.enumerate()):
            return True
        time.sleep(0.01)
    return False


def test_default_wiring_is_the_disabled_posture():
    injector = build_injector(profile=DeployProfile.TEST)
    protocol = injector.get(BotManagerSyncProtocol)
    lifecycle = injector.get(BotManagerSyncLifecycle)
    assert isinstance(protocol, DisabledBotManagerSync)
    assert isinstance(lifecycle, DisabledBotManagerSyncLifecycle)
    assert (
        protocol.request_sync(
            bot_pk=7, env="dev", avernet_tenant="a", reason=SyncReason.REPAIR
        )
        is SubmissionResult.DISABLED
    )
    assert (
        protocol.request_space_sync(SpaceChange("a", "dev", ("s",), ()))
        is SubmissionResult.DISABLED
    )
    assert not any(
        t.name.startswith("manager-sync") for t in threading.enumerate()
    )


def test_enabled_wiring_resolves_dispatcher_on_test_doubles():
    injector = build_injector(
        profile=DeployProfile.TEST,
        extra_modules=[_EnabledConfigModule()],
    )
    protocol = injector.get(BotManagerSyncProtocol)
    lifecycle = injector.get(BotManagerSyncLifecycle)
    # The test column's stack: recording delivery + memory coordination.
    stack = injector.get(ManagerSyncStack)
    assert stack.available is True

    from agentclaw.community.plugins.local.bcs_managers import (
        RecordingBcsManagersPlugin,
    )
    from agentclaw.community.plugins.local.manager_sync_coordination import (
        MemoryManagerSyncCoordinator,
    )

    assert type(stack.bcs) is RecordingBcsManagersPlugin
    assert type(stack.coordinator) is MemoryManagerSyncCoordinator

    assert isinstance(protocol, BotManagerSyncDispatcher)
    assert isinstance(lifecycle, BotManagerSyncLifecycle)
    assert type(lifecycle) is BotManagerSyncLifecycle  # not the disabled twin
    # Business keeps resolving right beside the enabled syncer.
    from agentclaw.community.core.bot_management.owner_handoff.contracts import (
        OwnerHandoffPolicy,
    )

    assert injector.get(OwnerHandoffPolicy) is not None
    # Before startup the dispatcher admits into memory only.
    assert (
        protocol.request_sync(
            bot_pk=7, env="dev", avernet_tenant="teamclaw",
            reason=SyncReason.REPAIR,
        )
        is SubmissionResult.QUEUED
    )
    # Env gate: the wired deployment env is "dev".
    assert (
        protocol.request_sync(
            bot_pk=8, env="pre", avernet_tenant="teamclaw",
            reason=SyncReason.REPAIR,
        )
        is SubmissionResult.REJECTED
    )


class _CommunityStackFixtures(Module):
    """Bindings the base (community) stack provider needs to resolve."""

    def __init__(self, *, redis_url: str) -> None:
        self._redis_url = redis_url

    def configure(self, binder):
        from agentclaw.community.di import config_community

        binder.bind(ManagerSyncConfig, to=_ENABLED_CONFIG, scope=singleton)
        binder.bind(
            SecretResolver,
            to=_NoSecretResolver(),
            scope=singleton,
        )
        binder.bind(
            config_community.CommunityCacheConfig,
            to=config_community.CommunityCacheConfig(redis_url=self._redis_url),
            scope=singleton,
        )


class _NoSecretResolver:
    """Resolver that yields nothing — every named secret is missing."""

    def get_secret(self, secret_name: str):
        return None


def test_enabled_without_credentials_is_unavailable_with_diagnostics(caplog):
    from injector import Injector

    # The community (prod) stack: every secret lookup misses, so the
    # stack must come up unavailable — never with empty-string fake
    # credentials — and the entry point stays the zero-resource disabled
    # posture while the diagnostic names the reason.
    injector = Injector(
        [
            BotManagerSyncModule(),
            _CommunityStackFixtures(
                redis_url="redis://127.0.0.1:6399/0"
            ),
        ]
    )
    stack = injector.get(ManagerSyncStack)
    assert stack.available is False
    assert "missing-credential" in stack.reason
    protocol = injector.get(BotManagerSyncProtocol)
    assert isinstance(protocol, DisabledBotManagerSync)
    assert any(
        "MANAGER-SYNC" in record.message and "missing-credential" in record.message
        for record in caplog.records
    )


def test_enabled_without_shared_redis_is_unavailable_no_process_lock():
    """No CommunityCacheConfig redis URL ⇒ no in-process lock fallback."""
    from injector import Injector

    injector = Injector(
        [
            BotManagerSyncModule(),
            _CommunityStackFixtures(redis_url=""),
        ]
    )
    stack = injector.get(ManagerSyncStack)
    assert stack.available is False
    assert stack.reason == "shared-redis-required"


@pytest.mark.asyncio
async def test_enabled_wiring_runs_one_round_through_the_real_thread():
    """E2E: lifecycle startup → admission → worker round over the
    recording/memory stack → bounded shutdown with the thread joined."""
    injector = build_injector(
        profile=DeployProfile.TEST,
        extra_modules=[_EnabledConfigModule()],
    )
    protocol = injector.get(BotManagerSyncProtocol)
    assert isinstance(protocol, BotManagerSyncDispatcher)
    lifecycle = injector.get(BotManagerSyncLifecycle)
    await lifecycle.startup()
    try:
        assert (
            protocol.request_sync(
                bot_pk=424242, env="dev", avernet_tenant="teamclaw",
                reason=SyncReason.COLLABORATOR_CHANGED,
            )
            is SubmissionResult.QUEUED
        )
        # The round runs on the dedicated loop; the (SQLite) reader has no
        # such bot, so the round ends FAILED before any delivery.
        assert await protocol.drain(5.0) is True
        bcs = injector.get(ManagerSyncStack).bcs
        assert bcs is not None and bcs.calls == []
    finally:
        await lifecycle.shutdown()
    assert _wait_thread_gone("manager-sync-loop") is True
    assert (
        protocol.request_sync(
            bot_pk=1, env="dev", avernet_tenant="teamclaw",
            reason=SyncReason.REPAIR,
        )
        is SubmissionResult.REJECTED
    )