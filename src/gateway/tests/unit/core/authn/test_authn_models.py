"""Unit tests for the authn Principal domain models."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime
from typing import get_args

import pytest
from pydantic import ValidationError

from gateway.community.spi.auth import AuthenticatedUser
from gateway.community.spi.authn import (
    AccessKey,
    AccessKeyPrincipal,
    AppPrincipal,
    Bot,
    BotPrincipal,
    CredentialBundle,
    Presence,
    Principal,
    PrincipalType,
    ThirdPartyApp,
    UserPrincipal,
)


def _subject() -> AuthenticatedUser:
    return AuthenticatedUser(id="u1", username="op")


def test_user_principal_defaults() -> None:
    p = UserPrincipal(subject=_subject())
    assert p.type is PrincipalType.USER
    assert p.type == "user"
    assert p.subject.id == "u1"


def test_user_principal_has_no_tenant_field() -> None:
    """A person is not registered to a tenant; only machine callers assert one.

    Asserted against the model's fields rather than an instance attribute so the
    check fails if the field is reintroduced with a default — the shape sent over
    the wire is the contract, and a defaulted field is still sent.
    """
    assert "tenant" not in UserPrincipal.model_fields
    assert "tenant" not in UserPrincipal(subject=_subject()).model_dump()


def test_user_principal_does_not_carry_a_tenant_it_is_handed() -> None:
    """A caller still on the old contract cannot smuggle a tenant onto the wire.

    The model ignores unknown fields (pydantic's default), so this does not
    raise — it drops. What matters is that the dropped value never reaches
    ``model_dump``, which is what the signer serializes into the token.
    """
    p = UserPrincipal(subject=_subject(), tenant="t-1")  # type: ignore[call-arg]
    assert not hasattr(p, "tenant")
    assert "tenant" not in p.model_dump()


def test_user_principal_requires_subject() -> None:
    with pytest.raises(ValidationError):
        UserPrincipal()  # type: ignore[call-arg]


def test_user_principal_serialization_tags_type() -> None:
    p = UserPrincipal(subject=_subject())
    dumped = p.model_dump()
    assert dumped["type"] == "user"
    assert dumped["subject"]["id"] == "u1"


def test_user_principal_is_immutable() -> None:
    p = UserPrincipal(subject=_subject())
    with pytest.raises(ValidationError):
        p.subject = _subject()  # type: ignore[misc]


def test_principal_union_includes_all_members() -> None:
    members = get_args(get_args(Principal)[0])
    assert UserPrincipal in members
    assert BotPrincipal in members
    assert AppPrincipal in members
    assert AccessKeyPrincipal in members


def test_credential_bundle_is_frozen() -> None:
    creds = CredentialBundle(headers={}, cookies={"SSO_TOKEN": "x"}, query={})
    assert creds.cookies["SSO_TOKEN"] == "x"
    with pytest.raises(FrozenInstanceError):
        creds.headers = {}  # type: ignore[misc]


def test_presence_enum_values() -> None:
    assert Presence.REQUIRED == "required"
    assert Presence.OPTIONAL == "optional"


def test_app_and_bot_principal_types() -> None:
    app = AppPrincipal(
        tenant="t-app",
        app=ThirdPartyApp(
            app_id=1,
            app_name="Cid App",
            owners="org-1",
            tenant="t-app",
            app_type="assistant",
        ),
    )
    assert app.type == "app"
    assert app.tenant == "t-app"
    assert app.app.app_id == 1
    assert app.app.tenant == "t-app"

    bot = BotPrincipal(
        tenant="t-bot",
        bot=Bot(
            bot_uuid="b-1",
            owner_id="org-1",
            token="tok",
            app_id=1,
            agent_code="agent-1",
            tenant="t-bot",
        ),
    )
    assert bot.type == "bot"
    assert bot.bot.bot_uuid == "b-1"
    assert bot.bot.token == "tok"
    assert bot.bot.agent_code == "agent-1"


def test_bot_requires_token() -> None:
    with pytest.raises(ValidationError):
        Bot(bot_uuid="b-1", owner_id="org-1")  # type: ignore[call-arg]


def test_access_key_principal_type() -> None:
    ak = AccessKeyPrincipal(
        tenant="t-ak",
        access_key=AccessKey(
            access_key="ak-1",
            access_key_token="tok",
            expire_at=datetime(2027, 1, 1, 0, 0, 0),
        ),
    )
    assert ak.type == "access_key"
    assert ak.tenant == "t-ak"
    assert ak.access_key.access_key == "ak-1"
    assert ak.access_key.access_key_token == "tok"
    assert ak.access_key.expire_at == datetime(2027, 1, 1, 0, 0, 0)


def test_access_key_requires_all_fields() -> None:
    with pytest.raises(ValidationError):
        AccessKey(access_key="ak-1")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        AccessKey()  # type: ignore[call-arg]
