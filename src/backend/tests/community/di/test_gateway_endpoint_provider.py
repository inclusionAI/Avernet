"""The gateway endpoint the composition root hands to the connection service.

``EngineConnectionService`` receives an already-resolved
:class:`GatewayEndpoint` and never reads ``SERVER_ENV`` itself — a deployment
detail is composition-root work (``AGENTS.md``: raw environment access belongs
in configuration loading, bootstrap, composition roots, or tests).

Since SOFAPy 1.3 the deployment overlay is selected and merged before this code
reads config, so the ``gateway`` block carries ONE host and this provider only
re-shapes it — the process env no longer picks between a prod and a pre key.
"""
from __future__ import annotations

import dataclasses

import pytest

from agentclaw.community.di import config as cfg
from agentclaw.community.di.modules import config_module
from agentclaw.community.di.modules.config_module import ConfigModule


@pytest.fixture
def stub_user_config(monkeypatch):
    def _set(user_config: dict) -> None:
        monkeypatch.setattr(config_module, "_user_config", lambda: dict(user_config))

    return _set


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for name in ("SERVER_ENV", "REAL_SERVER_ENV", "ALIPAY_APP_ENV"):
        monkeypatch.delenv(name, raising=False)


_HOSTS = {"base_url": "https://gw.example"}


@pytest.mark.parametrize("env", ["pre", "prod", "dev", ""])
def test_every_env_publishes_the_overlay_host(stub_user_config, monkeypatch, env):
    """The overlay already picked the gateway; the process env cannot re-pick it."""
    stub_user_config({"gateway": _HOSTS})
    monkeypatch.setenv("SERVER_ENV", env)
    module = ConfigModule()
    assert module.gateway_endpoint(module.gateway()).base_url == "https://gw.example"


def test_an_unset_env_publishes_the_overlay_host(stub_user_config):
    """``get_current_env`` returns ``""`` when no env var is set at all."""
    stub_user_config({"gateway": _HOSTS})
    module = ConfigModule()
    assert module.gateway_endpoint(module.gateway()).base_url == "https://gw.example"


def test_an_absent_block_resolves_to_no_gateway(stub_user_config):
    """The community build's normal state: empty, so the connection endpoint
    reports that this deployment fronts no gateway."""
    stub_user_config({})
    module = ConfigModule()
    assert module.gateway_endpoint(module.gateway()).base_url == ""


def test_a_legacy_pre_key_is_not_read(stub_user_config, monkeypatch):
    """A stale ``base_url_pre`` left in a yaml block is inert — no fallback, no
    selection. A pre deployment gets its host from its own overlay's
    ``base_url`` or it is reported as unconfigured."""
    stub_user_config({"gateway": {"base_url_pre": "https://gw-pre.example"}})
    monkeypatch.setenv("SERVER_ENV", "pre")
    module = ConfigModule()
    assert module.gateway().base_url == ""
    assert module.gateway_endpoint(module.gateway()).base_url == ""


def test_gateway_config_has_no_legacy_env_suffixed_field() -> None:
    """Guard: ``base_url_pre`` is gone for good (SOFAPy 1.3 overlays)."""
    fields = {f.name for f in dataclasses.fields(cfg.GatewayConfig)}
    assert "base_url_pre" not in fields
    assert "base_url" in fields
