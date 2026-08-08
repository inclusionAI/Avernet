"""Unit tests for bootstrap._authn strategy chains + config fail-fast behaviour."""

from __future__ import annotations

import pytest

from gateway.community.bootstrap._authn import (
    _load_route_security,
    _strategy_chains,
)
from gateway.community.bootstrap._forwarding import _load_domain_map
from gateway.community.config import ConfigLoader, UserConfig
from gateway.community.core.authn import IdentityChain
from gateway.community.plugins.authn.access_key_token import AccessKeyTokenStrategy
from gateway.community.plugins.authn.app_token import AppTokenStrategy
from gateway.community.plugins.authn.bot_token import BotTokenStrategy
from gateway.community.plugins.authn.google_token import GoogleUserStrategy
from gateway.community.spi.authn import PrincipalType


def _pool():
    return {
        "google": GoogleUserStrategy(token_header="x-google-token"),
        "bot_token": BotTokenStrategy(registry=None),
        "app_token": AppTokenStrategy(registry=None),
        "access_key_token": AccessKeyTokenStrategy(registry=None),
    }


def _user_config() -> UserConfig:
    return ConfigLoader.load().user_config


def test_missing_config_routes_to_defaults(tmp_path, monkeypatch):
    cfg = tmp_path / "application.yaml"
    cfg.write_text(
        "user_config:\n  identity_strategies:\n    user: [google]\n    bot: [bot_token]\n    app: [app_token]\n    access_key: [access_key_token]\n"
    )
    monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(tmp_path))
    chains = _strategy_chains(_pool(), user_config=_user_config())
    assert PrincipalType.USER in chains
    assert PrincipalType.BOT in chains
    assert PrincipalType.APP in chains
    assert PrincipalType.ACCESS_KEY in chains
    assert isinstance(chains[PrincipalType.USER], IdentityChain)


def test_unknown_strategy_name_raises(tmp_path, monkeypatch):
    cfg = tmp_path / "application.yaml"
    cfg.write_text(
        "user_config:\n  identity_strategies:\n    user: [google, bogus_name]\n"
    )
    monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(tmp_path))
    with pytest.raises(KeyError, match="unknown strategy 'bogus_name'"):
        _strategy_chains(_pool(), user_config=_user_config())


def test_unknown_identity_value_raises(tmp_path, monkeypatch):
    cfg = tmp_path / "application.yaml"
    cfg.write_text("user_config:\n  identity_strategies:\n    alien: [google]\n")
    monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(tmp_path))
    with pytest.raises(KeyError, match="unknown identity 'alien'"):
        _strategy_chains(_pool(), user_config=_user_config())


def test_empty_file_returns_defaults(tmp_path, monkeypatch):
    cfg = tmp_path / "application.yaml"
    cfg.write_text("")
    monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(tmp_path))
    chains = _strategy_chains(_pool(), user_config=_user_config())
    assert PrincipalType.USER in chains


def test_chains_are_identity_chains(tmp_path, monkeypatch):
    cfg = tmp_path / "application.yaml"
    cfg.write_text(
        "user_config:\n  identity_strategies:\n    user: [google]\n    bot: [bot_token]\n    app: [app_token]\n    access_key: [access_key_token]\n"
    )
    monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(tmp_path))
    chains = _strategy_chains(_pool(), user_config=_user_config())
    for ptype, chain in chains.items():
        assert isinstance(chain, IdentityChain)
        assert chain.principal_type is ptype
        assert chain.name == ptype.value


# ── single application.yaml config behaviour ───────────────────────────────


def test_missing_identity_strategies_uses_defaults(tmp_path, monkeypatch):
    (tmp_path / "application.yaml").write_text("app_name: test\n")
    monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(tmp_path))
    chains = _strategy_chains(_pool(), user_config=_user_config())
    assert PrincipalType.USER in chains


def test_missing_route_security_uses_fail_closed_default(tmp_path, monkeypatch):
    (tmp_path / "application.yaml").write_text("app_name: test\n")
    monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(tmp_path))
    route_security = _load_route_security(_user_config())
    req = route_security.resolve("GET", "/anything")
    assert req is not None


def test_missing_upstreams_section_fails(tmp_path, monkeypatch):
    (tmp_path / "application.yaml").write_text("app_name: test\n")
    monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(tmp_path))
    with pytest.raises(ValueError, match="application.yaml user_config.upstreams"):
        _load_domain_map()


# ── None user_config branches (default config loading) ─────────────────────


def test_strategy_chains_with_none_user_config_loads_defaults(
    tmp_path, monkeypatch
) -> None:
    cfg = tmp_path / "application.yaml"
    cfg.write_text(
        "user_config:\n  identity_strategies:\n    user: [google]\n    bot: [bot_token]\n    app: [app_token]\n    access_key: [access_key_token]\n"
    )
    monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(tmp_path))
    chains = _strategy_chains(_pool(), user_config=None)
    assert PrincipalType.USER in chains
    assert isinstance(chains[PrincipalType.USER], IdentityChain)


def test_load_route_security_with_none_user_config_uses_fail_closed_default(
    tmp_path, monkeypatch
) -> None:
    (tmp_path / "application.yaml").write_text("app_name: test\n")
    monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(tmp_path))
    route_security = _load_route_security(user_config=None)
    req = route_security.resolve("GET", "/anything")
    assert req is not None
