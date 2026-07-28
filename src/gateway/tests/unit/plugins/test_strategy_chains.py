"""Unit tests for bootstrap._authn._strategy_chains (identity_strategies.yaml parsing)."""

from __future__ import annotations

import pytest

from gateway.community.bootstrap._authn import _strategy_chains
from gateway.community.core.authn import IdentityChain
from gateway.community.spi.authn import PrincipalType


def test_missing_config_returns_defaults(tmp_path, monkeypatch):
    # No GATEWAY_CONFIG_PATH and no ./configs on cwd → defaults (4 identities).
    monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(tmp_path))
    chains = _strategy_chains()
    assert PrincipalType.USER in chains
    assert PrincipalType.BOT in chains
    assert PrincipalType.APP in chains
    assert PrincipalType.ACCESS_KEY in chains
    assert isinstance(chains[PrincipalType.USER], IdentityChain)


def test_unknown_strategy_name_raises(tmp_path, monkeypatch):
    cfg = tmp_path / "identity_strategies.yaml"
    cfg.write_text("identity_strategies:\n  user: [google, bogus_name]\n")
    monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(tmp_path))
    with pytest.raises(KeyError, match="unknown strategy 'bogus_name'"):
        _strategy_chains()


def test_unknown_identity_value_raises(tmp_path, monkeypatch):
    cfg = tmp_path / "identity_strategies.yaml"
    cfg.write_text("identity_strategies:\n  alien: [google]\n")
    monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(tmp_path))
    with pytest.raises(KeyError, match="unknown identity 'alien'"):
        _strategy_chains()


def test_empty_file_returns_defaults(tmp_path, monkeypatch):
    cfg = tmp_path / "identity_strategies.yaml"
    cfg.write_text("")
    monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(tmp_path))
    chains = _strategy_chains()
    assert PrincipalType.USER in chains


def test_chains_are_identity_chains(tmp_path, monkeypatch):
    monkeypatch.setenv("GATEWAY_CONFIG_PATH", str(tmp_path))
    chains = _strategy_chains()
    for ptype, chain in chains.items():
        assert isinstance(chain, IdentityChain)
        assert chain.principal_type is ptype
        assert chain.name == ptype.value
