"""The feature switch over the config-manifest routes (#1469).

Kept out of the endpoint-case files on purpose. Those seed process state (the
switch is an environment variable) and the runner interleaves them, so a case
asserting the *off* behaviour would be one ordering away from turning the
surface off for its neighbours. Here the variable is set and restored by
``monkeypatch`` inside a single test.
"""
from __future__ import annotations

import pytest

from agentclaw.community.adapters.http.openapi_v1.bots.config_manifest import (
    require_config_manifest_surface,
    router,
)
from agentclaw.community.adapters.http.openapi_v1.errors import (
    ConfigManifestSurfaceDisabledError,
)
from agentclaw.community.core.bot_config_manifest.feature_flag import (
    CONFIG_MANIFEST_ENABLED_ENV,
    config_manifest_surface_enabled,
)


def test_the_surface_is_off_when_nothing_says_otherwise(monkeypatch):
    """Fail closed: a deployment that has never heard of this feature does not
    serve it."""
    monkeypatch.delenv(CONFIG_MANIFEST_ENABLED_ENV, raising=False)
    assert config_manifest_surface_enabled() is False
    with pytest.raises(ConfigManifestSurfaceDisabledError):
        require_config_manifest_surface()


@pytest.mark.parametrize("value", ["true", "TRUE", " True "])
def test_the_switch_opens_the_surface(monkeypatch, value):
    monkeypatch.setenv(CONFIG_MANIFEST_ENABLED_ENV, value)
    assert config_manifest_surface_enabled() is True
    require_config_manifest_surface()  # does not raise


@pytest.mark.parametrize("value", ["false", "1", "yes", "", "enabled"])
def test_anything_other_than_true_leaves_it_closed(monkeypatch, value):
    """Only the exact word opens it. "1" and "yes" read as intent to enable but
    are not the contract, and guessing at them is how a surface gets served by
    accident."""
    monkeypatch.setenv(CONFIG_MANIFEST_ENABLED_ENV, value)
    assert config_manifest_surface_enabled() is False


def test_the_switch_is_declared_once_for_the_whole_group(monkeypatch):
    """On the router, not per handler — the point is that the *surface* is not
    served, and a per-handler declaration is one a fifth route could be added
    without.
    """
    dependencies = [d.dependency for d in router.dependencies]
    assert require_config_manifest_surface in dependencies


def test_the_switch_is_read_per_call_not_cached(monkeypatch):
    """A cached flag needs a reset hook for tests and cannot see a pushed config
    change without a restart."""
    monkeypatch.setenv(CONFIG_MANIFEST_ENABLED_ENV, "true")
    assert config_manifest_surface_enabled() is True
    monkeypatch.setenv(CONFIG_MANIFEST_ENABLED_ENV, "false")
    assert config_manifest_surface_enabled() is False
