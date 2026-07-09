"""B1 Task 1: ``DeployProfile`` selector + ``modules_for`` matrix selector.

Asserts the additive mechanism reproduces today's per-profile module sets
and that the mandatory switch errors out on unset / unknown values.
"""
from __future__ import annotations

import pytest

from agentclaw.community.di.profile import DeployProfile
from agentclaw.community.di.profile_modules import modules_for


# NOTE(B11 3.2): the corp-column tests (``modules_for(CORP)`` /
# ``modules_for(CORP_TEST)``, which register corp modules) live in
# ``tests/corp/di/test_profile_and_modules_for.py`` — they import ``agentclaw.corp``
# and can only run corp-present. This file is corp-free (community profiles only).


def _names(modules) -> set[str]:
    return {type(m).__name__ for m in modules}


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


def test_modules_for_community_is_isolated():
    # Community installs only its own column — never the prod infra modules.
    assert _names(modules_for(DeployProfile.COMMUNITY)) == {
        "CommunityCacheModule",
        "CommunitySecretModule",
        "CommunityDatabaseModule",
        "CommunityObjectStorageModule",
        "CommunityIdentityModule",
        "CommunityHealthModule",
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
        "CommunityNotifyModule",
    }


def test_modules_for_test_and_singlebox_match():
    # B11 (3.2): the test/singlebox column is corp-free — no corp reuse registry,
    # no corp-flavored Test* modules. Every borrowed concern is a community/neutral
    # equivalent. (No register_corp_modules call: these profiles trigger no corp.)
    expected = {
        # Corp-free common doubles.
        "TestingAccessModule",
        "TestApprovalWorkflowModule",
        "TestBotPublishApprovalModule",
        "TestingAicodingModule",
        "TestingDatabaseModule",
        "TestingSkillCenterModule",
        "TestingMcpModule",
        "TestCacheModule",
        "TestSecretModule",
        "TestIdentityModule",
        "TestHealthModule",
        "TestTracerModule",
        "TestDRMModule",
        "TestSandboxRuntimeModule",
        "TestHttpClientModule",
        "TestSkillCenterClientModule",
        # Corp-free equivalents for the corp-touching concerns.
        "TestDevicesModule",          # corp-free local device doubles
        "TestTokenVaultModule",       # empty-key vault
        "CommunityAICodingModule",    # empty workflow catalog
        "CommunityNotifyModule",  # no-op notify sender
        "CommunityOutboundRulesModule",
        "CommunityDeviceSyncModule",
        # Corp-free test app-services (real BotChatService, local_sql router,
        # community no-op code-platform) — shared with the corp_test column.
        "TestAppServicesModule",
    }
    assert _names(modules_for(DeployProfile.TEST)) == expected
    assert _names(modules_for(DeployProfile.SINGLEBOX)) == expected
