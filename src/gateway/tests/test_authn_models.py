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
    p = UserPrincipal(tenant="t-1", subject=_subject())
    dumped = p.model_dump()
    assert dumped["type"] == "user"
    assert dumped["tenant"] == "t-1"
    assert dumped["subject"]["id"] == "u1"


def test_user_principal_is_immutable() -> None:
    p = UserPrincipal(tenant="t-1", subject=_subject())
    with pytest.raises(ValidationError):
        p.tenant = "t-2"  # type: ignore[misc]


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
            tenant="t-bot",
        ),
    )
    assert bot.type == "bot"
    assert bot.bot.bot_uuid == "b-1"
    assert bot.bot.token == "tok"


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
