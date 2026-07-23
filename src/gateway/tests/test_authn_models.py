"""Unit tests for the authn Principal domain models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from gateway.community.spi.auth import AuthenticatedUser
from gateway.community.spi.authn import Principal, PrincipalType, UserPrincipal


def _subject() -> AuthenticatedUser:
    return AuthenticatedUser(id="u1", username="op")


def test_user_principal_defaults() -> None:
    p = UserPrincipal(tenant="t-1", subject=_subject())
    assert p.type is PrincipalType.USER
    assert p.type == "user"
    assert p.tenant == "t-1"
    assert p.scopes == frozenset()
    assert p.subject.id == "u1"


def test_user_principal_requires_tenant_and_subject() -> None:
    with pytest.raises(ValidationError):
        UserPrincipal(subject=_subject())  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        UserPrincipal(tenant="t-1")  # type: ignore[call-arg]


def test_user_principal_serialization_tags_type() -> None:
    p = UserPrincipal(tenant="t-1", scopes=frozenset({"bots:read"}), subject=_subject())
    dumped = p.model_dump()
    assert dumped["type"] == "user"
    assert dumped["tenant"] == "t-1"
    assert "bots:read" in dumped["scopes"]
    assert dumped["subject"]["id"] == "u1"


def test_user_principal_is_immutable() -> None:
    p = UserPrincipal(tenant="t-1", subject=_subject())
    with pytest.raises(ValidationError):
        p.tenant = "t-2"  # type: ignore[misc]


def test_principal_alias_is_user_principal() -> None:
    assert Principal is UserPrincipal
