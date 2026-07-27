"""Unit tests for the authn Principal domain models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import FrozenInstanceError

import pytest
from pydantic import TypeAdapter, ValidationError

from gateway.community.spi.auth import AuthenticatedUser
from gateway.community.spi.authn import (
    BotPrincipal,
    CredentialBundle,
    Principal,
    PrincipalType,
    UserPrincipal,
)


def _subject() -> AuthenticatedUser:
    return AuthenticatedUser(id="u1", username="op")


def test_user_principal_defaults() -> None:
    p = UserPrincipal(tenant="t-1", subject=_subject())
    assert p.type is PrincipalType.USER
    assert p.type == "user"
    assert p.tenant == "t-1"
    assert p.subject.id == "u1"


def test_user_principal_requires_tenant_and_subject() -> None:
    with pytest.raises(ValidationError):
        UserPrincipal(subject=_subject())  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        UserPrincipal(tenant="t-1")  # type: ignore[call-arg]


def test_user_principal_serialization_tags_type() -> None:
    dumped = UserPrincipal(tenant="t-1", subject=_subject()).model_dump()
    assert dumped["type"] == "user"
    assert dumped["tenant"] == "t-1"
    assert dumped["subject"]["id"] == "u1"


def test_user_principal_is_immutable() -> None:
    p = UserPrincipal(tenant="t-1", subject=_subject())
    with pytest.raises(ValidationError):
        p.tenant = "t-2"  # type: ignore[misc]


def test_user_principal_has_no_scopes() -> None:
    # Authorization scopes were removed from the gateway's identity model.
    assert not hasattr(UserPrincipal(tenant="t-1", subject=_subject()), "scopes")


def test_principal_is_a_discriminated_union_of_user_and_bot() -> None:
    adapter = TypeAdapter(Principal)
    user = adapter.validate_python(
        {"type": "user", "tenant": "t", "subject": {"id": "u", "username": "a"}}
    )
    bot = adapter.validate_python(
        {"type": "bot", "tenant": "t", "bot_uuid": "b", "owner_id": "o", "token": "k"}
    )
    assert isinstance(user, UserPrincipal)
    assert isinstance(bot, BotPrincipal)


def test_credential_bundle_is_frozen() -> None:
    creds = CredentialBundle(headers={}, cookies={"SSO_TOKEN": "x"}, query={})
    assert creds.cookies["SSO_TOKEN"] == "x"
    with pytest.raises(FrozenInstanceError):
        creds.headers = {}  # type: ignore[misc]


def test_credential_bundle_keeps_mapping_types() -> None:
    creds = CredentialBundle(headers={"a": "b"}, cookies={}, query={})
    assert isinstance(creds.headers, Mapping)
