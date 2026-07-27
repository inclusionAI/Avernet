"""Unit tests for the BotPrincipal model."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from gateway.community.spi.authn import BotPrincipal, PrincipalType


def _bot(**over: object) -> BotPrincipal:
    base = dict(tenant="t-1", bot_uuid="bot-7", owner_id="owner-1", token="tok")
    base.update(over)
    return BotPrincipal(**base)  # type: ignore[arg-type]


def test_bot_principal_defaults() -> None:
    p = _bot()
    assert p.type is PrincipalType.BOT
    assert p.type == "bot"
    assert p.tenant == "t-1"
    assert p.bot_uuid == "bot-7"
    assert p.owner_id == "owner-1"
    assert p.token == "tok"


def test_bot_principal_requires_core_fields() -> None:
    with pytest.raises(ValidationError):
        BotPrincipal(tenant="t-1")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        _bot(bot_uuid=None)  # type: ignore[arg-type]


def test_bot_principal_serialization_tags_type() -> None:
    dumped = _bot().model_dump()
    assert dumped["type"] == "bot"
    assert dumped["tenant"] == "t-1"
    assert dumped["bot_uuid"] == "bot-7"


def test_bot_principal_is_immutable() -> None:
    p = _bot()
    with pytest.raises(ValidationError):
        p.tenant = "t-2"  # type: ignore[misc]
