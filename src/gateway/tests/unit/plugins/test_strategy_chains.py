"""Unit tests for bootstrap._authn._strategy_chains + config fail-fast behaviour."""

from __future__ import annotations

import pytest

from gateway.community.bootstrap._authn import (
    _load_route_security,
    _strategy_chains,
)
from gateway.community.bootstrap._forwarding import _load_domain_map
from gateway.community.core.authn import IdentityChain
from gateway.community.plugins.authn.app_token import (
    StubAppTokenValidator,
    StubTenantResolver,
)
from gateway.community.plugins.database.sqlite import SqliteDatabasePlugin
from gateway.community.spi.authn import PrincipalType


def _bootstrap_db():
    from gateway.community.bootstrap._configs import DatabasePluginConfig

    db = SqliteDatabasePlugin()
    db.init_database(DatabasePluginConfig(plugin_type="SQLITE_ORM", db_url=""))
    from gateway.community.bootstrap._authn import _seed_authn

    _seed_authn(db)
    return db


def test_missing_config_routes_to_defaults(tmp_path, monkeypatch):
    cfg = tmp_path / "application.yaml"
    cfg.write_text(
        "identity_strategies:\n  user: [google]\n  bot: [bot_token]\n  app: [app_token]\n  access_key: [access_key_token]\n"
    )
    monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(tmp_path))
    chains = _strategy_chains(
        _bootstrap_db(), StubAppTokenValidator(), StubTenantResolver()
    )
    assert PrincipalType.USER in chains
    assert PrincipalType.BOT in chains
    assert PrincipalType.APP in chains
    assert PrincipalType.ACCESS_KEY in chains
    assert isinstance(chains[PrincipalType.USER], IdentityChain)


def test_unknown_strategy_name_raises(tmp_path, monkeypatch):
    cfg = tmp_path / "application.yaml"
    cfg.write_text("identity_strategies:\n  user: [google, bogus_name]\n")
    monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(tmp_path))
    with pytest.raises(KeyError, match="unknown strategy 'bogus_name'"):
        _strategy_chains(_bootstrap_db(), StubAppTokenValidator(), StubTenantResolver())


def test_unknown_identity_value_raises(tmp_path, monkeypatch):
    cfg = tmp_path / "application.yaml"
    cfg.write_text("identity_strategies:\n  alien: [google]\n")
    monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(tmp_path))
    with pytest.raises(KeyError, match="unknown identity 'alien'"):
        _strategy_chains(_bootstrap_db(), StubAppTokenValidator(), StubTenantResolver())


def test_empty_file_returns_defaults(tmp_path, monkeypatch):
    cfg = tmp_path / "application.yaml"
    cfg.write_text("")
    monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(tmp_path))
    chains = _strategy_chains(
        _bootstrap_db(), StubAppTokenValidator(), StubTenantResolver()
    )
    assert PrincipalType.USER in chains


def test_chains_are_identity_chains(tmp_path, monkeypatch):
    cfg = tmp_path / "application.yaml"
    cfg.write_text(
        "identity_strategies:\n  user: [google]\n  bot: [bot_token]\n  app: [app_token]\n  access_key: [access_key_token]\n"
    )
    monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(tmp_path))
    chains = _strategy_chains(
        _bootstrap_db(), StubAppTokenValidator(), StubTenantResolver()
    )
    for ptype, chain in chains.items():
        assert isinstance(chain, IdentityChain)
        assert chain.principal_type is ptype
        assert chain.name == ptype.value


# ── single application.yaml config behaviour ───────────────────────────────


def test_missing_identity_strategies_uses_defaults(tmp_path, monkeypatch):
    (tmp_path / "application.yaml").write_text("app_name: test\n")
    monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(tmp_path))
    chains = _strategy_chains(
        _bootstrap_db(), StubAppTokenValidator(), StubTenantResolver()
    )
    assert PrincipalType.USER in chains


def test_missing_route_security_uses_fail_closed_default(tmp_path, monkeypatch):
    (tmp_path / "application.yaml").write_text("app_name: test\n")
    monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(tmp_path))
    route_security = _load_route_security()
    req = route_security.resolve("GET", "/anything")
    assert req is not None


def test_missing_upstreams_section_fails(tmp_path, monkeypatch):
    (tmp_path / "application.yaml").write_text("app_name: test\n")
    monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(tmp_path))
    with pytest.raises(ValueError, match="application.yaml upstreams"):
        _load_domain_map()
