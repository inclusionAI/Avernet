"""Unit tests for the authn strategy-chain config parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from gateway.community.core.authn._config import build_strategy_registry, load_chains
from gateway.community.spi.authn import CredentialBundle, PrincipalType

_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "authn.yaml"


class _FakeStrategy:
    def __init__(
        self, name: str, ptype: PrincipalType, principal: object | None
    ) -> None:
        self.name = name
        self.principal_type = ptype
        self._principal = principal

    async def build(self, creds: CredentialBundle) -> object | None:
        return self._principal


def test_load_chains_from_shipped_config() -> None:
    chains = load_chains(_CONFIG)
    assert PrincipalType.USER in chains
    # User identity comes only from a verified Google access token (no cookie).
    assert chains[PrincipalType.USER] == ["google"]
    assert PrincipalType.BOT in chains


def test_build_registry_orders_plugins_by_chain() -> None:
    pool = {
        "google": _FakeStrategy("google", PrincipalType.USER, None),
        "bot_token": _FakeStrategy("bot_token", PrincipalType.BOT, None),
    }
    chains = {
        PrincipalType.USER: ["google"],
        PrincipalType.BOT: ["bot_token"],
    }
    registry = build_strategy_registry(chains, pool)
    assert [s.name for s in registry[PrincipalType.USER]] == ["google"]
    assert registry[PrincipalType.BOT][0].name == "bot_token"


def test_build_registry_rejects_unknown_strategy_name() -> None:
    pool = {"google": _FakeStrategy("google", PrincipalType.USER, None)}
    chains = {PrincipalType.USER: ["google", "ghost"]}
    with pytest.raises(ValueError, match="ghost"):
        build_strategy_registry(chains, pool)


def test_build_registry_rejects_wrong_principal_type() -> None:
    # A plugin in the user chain whose principal_type is BOT is a misconfiguration.
    pool = {"bot_token": _FakeStrategy("bot_token", PrincipalType.BOT, None)}
    chains = {PrincipalType.USER: ["bot_token"]}
    with pytest.raises(ValueError, match="type"):
        build_strategy_registry(chains, pool)


def test_shipped_user_chain_has_google() -> None:
    chains = load_chains(_CONFIG)
    assert chains[PrincipalType.USER] == ["google"]
