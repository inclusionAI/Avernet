"""Unit tests for the per-route security metadata helper."""

from __future__ import annotations

from gateway.community.adapters.web.contracts import (
    requires_identities,
    requires_user_principal,
)
from gateway.community.spi.authn import PrincipalType


def test_requires_user_principal_is_user_only() -> None:
    assert requires_user_principal() == {"x-avernet-security": ["user"]}


def test_requires_identities_single_type() -> None:
    assert requires_identities(PrincipalType.USER) == {"x-avernet-security": ["user"]}


def test_requires_identities_multiple_types() -> None:
    got = requires_identities(PrincipalType.BOT, PrincipalType.USER)
    assert got == {"x-avernet-security": ["bot", "user"]}


def test_requires_identities_returns_fresh_dict() -> None:
    a = requires_user_principal()
    a["x-avernet-security"].append("tampered")
    assert requires_user_principal() == {"x-avernet-security": ["user"]}
