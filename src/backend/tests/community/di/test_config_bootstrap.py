"""Composition-root ConfigProvider selection — non-corp profiles (B2 T3, B11 3.2)."""
import pytest

from agentclaw.community.core.config import provider as P
from agentclaw.community.core.config.yaml_provider import YamlConfigProvider
from agentclaw.community.di.config_bootstrap import (
    register_config_provider,
)
from agentclaw.community.di.profile import DeployProfile


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Isolate this file without replacing the suite bootstrap on teardown."""
    previous_provider = P._provider
    previous_cached = P._cached
    P.reset_config_provider()
    try:
        yield
    finally:
        P._provider = previous_provider
        P._cached = previous_cached


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        (DeployProfile.COMMUNITY, "application-community.yaml"),
        (DeployProfile.TEST, "application-test.yaml"),
        (DeployProfile.CORP_TEST, "application-test.yaml"),
        (DeployProfile.SINGLEBOX, "application-singlebox.yaml"),
    ],
)
def test_non_corp_profile_registers_explicit_yaml_provider(profile, expected):
    register_config_provider(profile)

    assert isinstance(P._provider, YamlConfigProvider)
    assert P._provider.profile is profile
    assert P._provider.profile_name == profile.value
    assert P._provider.overlay_name == expected


def test_config_load_requires_composition_root_registration():
    P.reset_config_provider()

    with pytest.raises(RuntimeError, match="ConfigProvider has not been registered"):
        P.load_config()
