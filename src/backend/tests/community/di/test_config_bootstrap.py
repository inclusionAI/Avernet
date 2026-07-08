"""Composition-root ConfigProvider selection — non-corp profiles (B2 T3, B11 3.2).

Asserts ``register_config_provider`` leaves the YAML default in place for every
**non-corp** profile (``test`` / ``singlebox`` / ``community`` / ``corp_test``).
Corp-free: it registers no provider (the registry stays ``None`` → YAML fallback),
so it does not reference ``agentclaw.corp`` and runs with corp absent. The corp
branch (``register_config_provider(CORP)`` installs the sofapy provider) lives in
``tests/corp/di/test_config_bootstrap.py``.
"""
import pytest

from agentclaw.community.core.config import provider as P
from agentclaw.community.di.config_bootstrap import register_config_provider
from agentclaw.community.di.profile import DeployProfile


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Each test starts and ends on the default (no-provider) registry."""
    P.reset_config_provider()
    yield
    P.reset_config_provider()


@pytest.mark.parametrize(
    "profile",
    [
        DeployProfile.TEST,
        DeployProfile.SINGLEBOX,
        DeployProfile.COMMUNITY,
        DeployProfile.CORP_TEST,
    ],
)
def test_non_corp_leaves_yaml_default(profile):
    register_config_provider(profile)
    # No provider was registered — the registry stays on its default, so
    # ``load_config`` falls back to the YAML provider (proving no sofapy install
    # and no corp import for these profiles).
    assert P._provider is None
    cfg = P.load_config()
    assert isinstance(cfg, P.AppConfig)
