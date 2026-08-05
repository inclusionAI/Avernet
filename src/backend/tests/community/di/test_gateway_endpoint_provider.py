"""The gateway pre/prod selection, which lives in the composition root.

``EngineConnectionService`` receives an already-resolved
:class:`GatewayEndpoint` and never reads ``SERVER_ENV`` itself — selecting a
deployment is composition-root work (``AGENTS.md``: raw environment access
belongs in configuration loading, bootstrap, composition roots, or tests). So
the selection is pinned here rather than in that service's tests.
"""
from __future__ import annotations

import pytest

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


_HOSTS = {"base_url": "https://gw.example", "base_url_pre": "https://gw-pre.example"}


def test_pre_selects_the_pre_gateway(stub_user_config, monkeypatch):
    """pre and prod are separate gateways; collapsing them would send a
    credential issued for one to the other, which rejects it."""
    stub_user_config({"gateway": _HOSTS})
    monkeypatch.setenv("SERVER_ENV", "pre")
    module = ConfigModule()
    assert module.gateway_endpoint(module.gateway()).base_url == "https://gw-pre.example"


@pytest.mark.parametrize("env", ["prod", "dev", ""])
def test_every_other_env_selects_the_prod_gateway(stub_user_config, monkeypatch, env):
    stub_user_config({"gateway": _HOSTS})
    monkeypatch.setenv("SERVER_ENV", env)
    module = ConfigModule()
    assert module.gateway_endpoint(module.gateway()).base_url == "https://gw.example"


def test_an_unset_env_selects_the_prod_gateway(stub_user_config):
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


def test_a_pre_deployment_with_only_a_prod_host_resolves_empty(
    stub_user_config, monkeypatch
):
    """No silent fallback to the prod host — a pre deployment pointed at the
    prod gateway is the mix-up the separate keys exist to prevent, so an
    unconfigured pre resolves empty and is reported as unconfigured."""
    stub_user_config({"gateway": {"base_url": "https://gw.example"}})
    monkeypatch.setenv("SERVER_ENV", "pre")
    module = ConfigModule()
    assert module.gateway_endpoint(module.gateway()).base_url == ""
