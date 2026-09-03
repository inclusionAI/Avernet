"""B1 Task 1: ``DeployProfile`` selector + ``modules_for`` matrix selector.

Asserts the additive mechanism reproduces today's per-profile module sets
and that the mandatory switch errors out on unset / unknown values.
"""

from __future__ import annotations

from typing import Annotated

import pytest
from injector import Injector

from agentclaw.community.api.policy_service import PolicyServiceProtocol
from agentclaw.community.api.caller_iam_token_service import (
    CallerIamTokenServiceProtocol,
)
from agentclaw.community.core.bot_management.services.teclaw_publish_task_handler import (
    TeclawPublishTaskLifecycle,
)
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.core.devices.services.baas_device_accessor import (
    BaasDeviceAccessor,
)
from agentclaw.community.core.devices.services.baas_device_service import (
    BaasDeviceService,
)
from agentclaw.community.core.devices.services.device_accessor import DeviceAccessor
from agentclaw.community.core.devices.services.device_service import DeviceService
from agentclaw.community.core.devices.services.device_service_router import (
    DeviceServiceRouter,
)
from agentclaw.community.core.access.services.policy_service import PolicyService
from agentclaw.community.di.container import build_injector
from agentclaw.community.di.modules.http_client_module import HttpClientModule
from agentclaw.community.di.modules.infrastructure.test.http_client import (
    TestHttpClientModule,
)
from agentclaw.community.di.modules.infrastructure.singlebox.devices import (
    SingleboxBaasDeviceService,
)
from agentclaw.community.di.profile import DeployProfile, validate_deploy_environment
from agentclaw.community.di.profile_modules import modules_for
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.kernel.lifecycle import discover_lifecycle_participants
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterTransport,
)
from agentclaw.community.plugin_api.http_client import (
    QUALIFIER_BAAS,
    QUALIFIER_BCN,
    QUALIFIER_GENERAL,
    QUALIFIER_MASA_AGENT_EVAL,
    HttpClient,
)
from agentclaw.community.plugin_api.auth import AuthRequestContext
from agentclaw.community.core.caller_identity.contracts import CallerIdentityStage
from agentclaw.community.plugins.http_client import HttpxClient
from agentclaw.community.plugins.local.device_adapter_transport import (
    InMemoryDeviceAdapterTransport,
)
from agentclaw.community.plugins.local.http_client import LocalHttpClient
from agentclaw.community.plugins.local.policy_service import LocalPolicyService


# NOTE(B11 3.2): the corp-column tests (``modules_for(CORP)`` /
# ``modules_for(CORP_TEST)``, which register corp modules) live in
# ``tests/corp/di/test_profile_and_modules_for.py`` — they import ``agentclaw.corp``
# and can only run corp-present. This file is corp-free (community profiles only).


def _names(modules) -> set[str]:
    return {type(m).__name__ for m in modules}


_HTTP_CLIENT_KEYS = (
    Annotated[HttpClient, QUALIFIER_BAAS],
    Annotated[HttpClient, QUALIFIER_BCN],
    Annotated[HttpClient, QUALIFIER_GENERAL],
    Annotated[HttpClient, QUALIFIER_MASA_AGENT_EVAL],
)


def _resolve_http_clients(injector: Injector) -> list[HttpClient]:
    return [injector.get(key) for key in _HTTP_CLIENT_KEYS]


def test_detect_raises_when_unset(monkeypatch):
    monkeypatch.delenv("DEPLOY_PROFILE", raising=False)
    with pytest.raises(RuntimeError, match="DEPLOY_PROFILE must be set"):
        DeployProfile.detect()


def test_detect_raises_on_unknown(monkeypatch):
    monkeypatch.setenv("DEPLOY_PROFILE", "staging")
    with pytest.raises(RuntimeError, match="Unknown DEPLOY_PROFILE"):
        DeployProfile.detect()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("corp", DeployProfile.CORP),
        ("singlebox", DeployProfile.SINGLEBOX),
        ("test", DeployProfile.TEST),
        ("corp_test", DeployProfile.CORP_TEST),
        ("community", DeployProfile.COMMUNITY),
        ("  TEST  ", DeployProfile.TEST),  # stripped + lowercased
    ],
)
def test_detect_parses_value(monkeypatch, raw, expected):
    monkeypatch.setenv("DEPLOY_PROFILE", raw)
    assert DeployProfile.detect() is expected


@pytest.mark.parametrize(
    "key",
    ["SERVER_ENV", "REAL_SERVER_ENV", "ALIPAY_APP_ENV"],
)
def test_legacy_singlebox_env_is_rejected(key):
    with pytest.raises(RuntimeError, match="DEPLOY_PROFILE=singlebox SERVER_ENV=dev"):
        validate_deploy_environment({key: "singlebox"})


@pytest.mark.parametrize(
    "source",
    [
        {"SERVER_ENV": "dev", "REAL_SERVER_ENV": "singlebox"},
        {"SERVER_ENV": "dev", "ALIPAY_APP_ENV": "singlebox"},
    ],
)
def test_legacy_singlebox_env_names_each_violating_key_and_cleanup(source):
    with pytest.raises(RuntimeError) as error:
        validate_deploy_environment(source)

    message = str(error.value)
    assert "REAL_SERVER_ENV" in message or "ALIPAY_APP_ENV" in message
    assert "clear the retired environment variable" in message
    assert "DEPLOY_PROFILE=singlebox SERVER_ENV=dev" in message


def test_legacy_singlebox_env_lists_all_violating_keys():
    with pytest.raises(RuntimeError) as error:
        validate_deploy_environment(
            {"SERVER_ENV": "singlebox", "ALIPAY_APP_ENV": "singlebox"}
        )

    message = str(error.value)
    assert "SERVER_ENV, ALIPAY_APP_ENV" in message


def test_dev_env_is_accepted():
    validate_deploy_environment({"SERVER_ENV": "dev"})


def test_none_env_value_is_accepted():
    validate_deploy_environment({"SERVER_ENV": None})


def test_modules_for_community_is_isolated():
    # Community installs only its own column — never the prod infra modules.
    assert _names(modules_for(DeployProfile.COMMUNITY)) == {
        "CommunityCacheModule",
        "CommunityCallerIdentityModule",
        "CommunitySecretModule",
        "CommunityDatabaseModule",
        "CommunityObjectStorageModule",
        "CommunityIdentityModule",
        "CommunityHealthModule",
        "CommunityHarnessModule",
        "CommunityTracerModule",
        "CommunityOutboundRulesModule",
        "CommunityDRMModule",
        "CommunitySandboxRuntimeModule",
        "CommunityDeviceSyncModule",
        "CommunityDevicesModule",
        "CommunityMcpCenterModule",
        "CommunitySkillCenterClientModule",
        "CommunityAICodingModule",
        "CommunityAppServicesModule",
        "CommunityApprovalWorkflowModule",
        "CommunityBotPublishApprovalModule",
        "CommunityEvalEnvModule",
        "CommunityNotifyModule",
        "CommunityStaffDeptModule",
    }


def test_test_and_singlebox_have_explicit_access_and_http_bindings():
    test_names = _names(modules_for(DeployProfile.TEST))
    singlebox_names = _names(modules_for(DeployProfile.SINGLEBOX))
    legacy_access_module = "Testing" + "AccessModule"

    assert "TestHttpClientModule" in test_names
    assert "TestDevicesModule" in test_names
    assert "CommunityDeviceSyncModule" in test_names
    assert "SingleboxDeviceSyncModule" not in test_names
    assert "SingleboxDevicesModule" not in test_names
    assert "SingleboxAccessModule" not in test_names
    assert legacy_access_module not in test_names

    assert "SingleboxAccessModule" in singlebox_names
    assert "TestHttpClientModule" not in singlebox_names
    assert "SingleboxDevicesModule" in singlebox_names
    assert "CommunityDeviceSyncModule" in singlebox_names
    assert "SingleboxDeviceSyncModule" not in singlebox_names
    assert "TestDevicesModule" not in singlebox_names
    assert legacy_access_module not in singlebox_names

    assert test_names - {
        "CommunityDeviceSyncModule",
        "TestHttpClientModule",
        "TestDevicesModule",
    } == (
        singlebox_names
        - {
            "SingleboxAccessModule",
            "SingleboxCallerIdentityModule",
            "SingleboxDevicesModule",
            "CommunityDeviceSyncModule",
        }
    )


def test_test_profile_resolves_real_policy_and_local_http_clients():
    injector = build_injector(profile=DeployProfile.TEST)

    assert isinstance(injector.get(PolicyServiceProtocol), PolicyService)
    assert all(
        isinstance(client, LocalHttpClient)
        for client in _resolve_http_clients(injector)
    )


def test_singlebox_profile_resolves_local_policy_and_real_http_clients():
    injector = build_injector(profile=DeployProfile.SINGLEBOX)

    assert isinstance(injector.get(PolicyServiceProtocol), LocalPolicyService)
    assert all(
        isinstance(client, HttpxClient) for client in _resolve_http_clients(injector)
    )


def test_singlebox_profile_keeps_local_device_adapter_transport():
    injector = build_injector(profile=DeployProfile.SINGLEBOX)

    assert isinstance(
        injector.get(DeviceAdapterTransport),
        InMemoryDeviceAdapterTransport,
    )


@pytest.mark.asyncio
async def test_singlebox_profile_returns_mock_iam_token_without_cookie():
    injector = build_injector(profile=DeployProfile.SINGLEBOX)

    result = await injector.get(CallerIamTokenServiceProtocol).get_iam_token(
        iam_token="",
        auth_request=AuthRequestContext({}, {}, {}, "http://test/"),
        bot_id=None,
        stage=CallerIdentityStage.DRAFT,
        publish_id=None,
        entity_id=None,
        is_test_exchange=False,
    )

    assert result.error is None
    assert result.iam_token == "mock_iam_token"


@pytest.mark.asyncio
async def test_community_profile_returns_placeholder_iam_token_without_cookie():
    injector = build_injector(profile=DeployProfile.COMMUNITY)

    result = await injector.get(CallerIamTokenServiceProtocol).get_iam_token(
        iam_token="",
        auth_request=AuthRequestContext(
            {"bcs_session": "oauth-session-must-not-be-returned"},
            {},
            {},
            "http://test/",
        ),
        bot_id="bot-1",
        stage=CallerIdentityStage.ONLINE,
        publish_id=None,
        entity_id="user-1",
        is_test_exchange=False,
    )

    assert result.error is None
    assert result.iam_token == "caller_mode_unsupported"
    assert result.iam_token != "oauth-session-must-not-be-returned"


def test_singlebox_profile_resolves_baas_only_device_runtime():
    injector = build_injector(profile=DeployProfile.SINGLEBOX)

    service = injector.get(DeviceService)
    baas_device_accessor = injector.get(BaasDeviceAccessor)
    assert isinstance(service, DeviceServiceRouter)
    assert set(service._providers) == {"baas"}
    assert isinstance(service._providers["baas"], BaasDeviceService)
    assert isinstance(service._providers["baas"], SingleboxBaasDeviceService)
    assert injector.get(DeviceAccessor) is baas_device_accessor
    participant_names = {
        type(participant).__name__
        for participant in discover_lifecycle_participants(injector)
    }
    assert "SingleboxBaasTemplateConfigLifecycle" in participant_names
    assert "LocalDeviceLifecycle" not in participant_names


def test_singlebox_rollout_policy_preserves_normalized_engine_bucket():
    injector = build_injector(profile=DeployProfile.SINGLEBOX)
    service = injector.get(DeviceService)

    decision = service._arca_baas_rollout_policy.decide(
        user_id="owner",
        bot_type="personal",
        engine_type="claude-code",
        template_type="personalCoding",
    )

    assert decision.target_provider == "baas"
    assert decision.engine_bucket == "aicoding"


@pytest.mark.parametrize("profile", [DeployProfile.TEST, DeployProfile.SINGLEBOX])
def test_teclaw_publish_lifecycle_uses_real_dependencies_in_every_local_profile(profile):
    injector = build_injector(profile=profile)

    lifecycle = injector.get(TeclawPublishTaskLifecycle)

    assert lifecycle._baas_service is injector.get(BaasService)
    assert lifecycle._device_binding_repo is injector.get(DeviceBindingRepository)
    assert "TeclawPublishTaskLifecycle" in {
        type(participant).__name__
        for participant in discover_lifecycle_participants(injector)
    }


def test_test_http_override_wins_when_installed_after_base():
    # Generic Injector contract: a later TestHttpClientModule replaces every
    # qualified key supplied by the base HttpClientModule.
    injector = Injector([HttpClientModule(), TestHttpClientModule()])

    assert all(
        isinstance(client, LocalHttpClient)
        for client in _resolve_http_clients(injector)
    )
