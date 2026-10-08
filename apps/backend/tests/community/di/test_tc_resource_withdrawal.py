from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentclaw.community.di.tc_resource_withdrawal_config import (
    ResourceWithdrawalConfig,
)
from agentclaw.community.di.modules.tc_resource_withdrawal_module import (
    ResourceWithdrawalModule,
)
from agentclaw.community.di.modules import config_module
from agentclaw.community.plugin_api.tc_resource_withdrawal import (
    ResourceWithdrawalPublisherPlugin,
)
from agentclaw.community.plugins.community.tc_resource_withdrawal import (
    HttpResourceWithdrawalPublisher,
)
from agentclaw.community.plugins.local.tc_resource_withdrawal import (
    LocalResourceWithdrawalPublisher,
)


@pytest.mark.parametrize(
    "settings",
    [
        {"enabled": "false"},
        {"enabled": True},
        {"poll_seconds": 0},
        {"max_attempts": True},
        {"lease_seconds": 10},
        {"retry_max_seconds": 1},
        {"base_url": "http://not-local.test"},
        {"base_url": "https://user:pass@host.test"},
        {"base_url": "https://host.test/path"},
        {"base_url": "https://host.test/?token=abc"},
    ],
)
def test_bad_configuration_fails_closed(settings):
    with pytest.raises(ValueError):
        ResourceWithdrawalConfig(**settings)


def test_default_disabled_no_secret_lookup():
    cfg = ResourceWithdrawalConfig()
    resolver = Mock()
    impl = ResourceWithdrawalModule(local=False).publisher(cfg, resolver, Mock())
    assert isinstance(impl, HttpResourceWithdrawalPublisher)
    resolver.get_secret.assert_not_called()
    assert isinstance(
        ResourceWithdrawalModule(local=True).publisher(cfg, resolver, Mock()),
        LocalResourceWithdrawalPublisher,
    )


@pytest.mark.parametrize("value", [None, "", " ", "bad\nvalue"])
def test_enabled_missing_secret_fails(value):
    cfg = ResourceWithdrawalConfig(
        enabled=True,
        base_url="https://ecb.example.test",
        secret_name="ECB_WITHDRAWAL",
        tenant="tenant-1",
    )
    resolver = Mock()
    resolver.get_secret.return_value = SimpleNamespace(secret_value=value)
    with pytest.raises(ValueError, match="withdrawal_secret"):
        ResourceWithdrawalModule(local=False).publisher(cfg, resolver, Mock())


def test_unknown_config_key_fails_on_build(monkeypatch):
    monkeypatch.setattr(
        config_module,
        "read_user_config",
        lambda: {"tc_resource_withdrawal": {"enabeld": True}},
    )
    from agentclaw.community.di import build_injector, DeployProfile

    with pytest.raises(ValueError, match="unknown"):
        build_injector(profile=DeployProfile.TEST)


def test_community_uses_real_plugin_and_local_contract_uses_fake():
    from agentclaw.community.di import build_injector, DeployProfile

    for profile in (DeployProfile.COMMUNITY, DeployProfile.SINGLEBOX):
        assert isinstance(
            build_injector(profile=profile).get(ResourceWithdrawalPublisherPlugin),
            HttpResourceWithdrawalPublisher,
        )
    assert isinstance(
        build_injector(profile=DeployProfile.TEST).get(
            ResourceWithdrawalPublisherPlugin
        ),
        LocalResourceWithdrawalPublisher,
    )


@pytest.mark.parametrize(
    "settings",
    [
        {"tenant": "x" * 129},
        {"tenant": " t"},
        {"base_url": "https://host.test:bad"},
        {"secret_name": None},
    ],
)
def test_configuration_rejects_invalid_identity_or_origin(settings):
    with pytest.raises(ValueError):
        ResourceWithdrawalConfig(**settings)


def test_secret_resolution_failure_is_redacted_and_valid_secret_is_used():
    cfg = ResourceWithdrawalConfig(
        enabled=True,
        base_url="https://ecb.example.test",
        secret_name="ECB_WITHDRAWAL",
        tenant="tenant-1",
    )
    resolver = Mock()
    resolver.get_secret.side_effect = RuntimeError("private")
    with pytest.raises(ValueError, match="^withdrawal_secret_resolution_failed$"):
        ResourceWithdrawalModule().publisher(cfg, resolver, Mock())
    resolver.get_secret.side_effect = None
    resolver.get_secret.return_value = SimpleNamespace(secret_value="test-only")
    adapter = ResourceWithdrawalModule().publisher(cfg, resolver, Mock())
    assert adapter._authorization == "test-only"
    resolver.get_secret.assert_called_with(secret_name="ECB_WITHDRAWAL")


def test_config_mapping_required(monkeypatch):
    monkeypatch.setattr(
        config_module, "read_user_config", lambda: {"tc_resource_withdrawal": []}
    )
    with pytest.raises(ValueError, match="mapping"):
        ResourceWithdrawalModule().config()
