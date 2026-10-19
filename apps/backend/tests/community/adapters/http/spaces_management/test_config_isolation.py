"""Regression: maintenance fixtures must not freeze the lazy config proxy."""

import pytest

from agentclaw.community.core.config.sofa import sofa_config
from tests.community.config.test_effective_config_snapshot import (
    test_effective_config_matches_golden as assert_config_matches_golden,
)


@pytest.mark.parametrize("profile", ["community", "singlebox", "test"])
def test_maintenance_fixture_restores_dynamic_config(harness, monkeypatch, profile):
    # Exercise the real fixture, then perform its patch teardown before switching
    # providers. setattr teardown used to leave a fixed user_config on the proxy.
    assert "user_config" in vars(sofa_config)
    monkeypatch.undo()
    assert "user_config" not in vars(sofa_config)
    assert_config_matches_golden(profile)
