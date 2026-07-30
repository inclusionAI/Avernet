"""Authn SPI — the neutral Principal the gateway produces after authentication.

The gateway authenticates a request, resolves the identities it carries into a
set of Principals (one per present :class:`PrincipalType`), and forwards them
(signed) to downstream components, which project each onto its own domain DTO.
The gateway never lets a component see raw credentials.

``Principal`` is a discriminated union (``type`` tag); each member carries only
the fields that exist for its kind, so illegal states (a USER carrying an
``app``) cannot be constructed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from gateway.community.spi.auth import AuthenticatedUser


class PrincipalType(StrEnum):
    """Discriminator for the kind of caller a ``Principal`` represents."""

    USER = "user"
    BOT = "bot"
    APP = "app"
    ACCESS_KEY = "access_key"


class ThirdPartyApp(BaseModel):
    """A registered third-party developer application — the calling program itself."""

    model_config = ConfigDict(frozen=True)

    app_id: str  # app id issued at registration
    app_name: str  # human-facing app name
    owners: str  # owning developer/org; resource-ownership fallback subject
    app_type: str = "UNKNOWN"  # from the app-token record


class Bot(BaseModel):
    """A bot/agent acting as a first-class caller in its own right.

    ``bot_uuid`` is the bot's stable id; ``owner_id`` is the user who owns it
    (the resource-ownership anchor); ``token`` is the presented/verified bot
    credential (a secret flowing downstream — components must treat it as such).
    """

    model_config = ConfigDict(frozen=True)

    bot_uuid: str  # stable, provider-issued bot id
    owner_id: str  # owning user (resource-ownership anchor)
    token: str  # the presented/verified bot credential (secret)


class UserPrincipal(BaseModel):
    """A first-party authenticated user, produced by the gateway."""

    model_config = ConfigDict(frozen=True)

    type: Literal[PrincipalType.USER] = PrincipalType.USER
    tenant: str = Field(description="Tenant id the caller belongs to (stable id).")
    subject: AuthenticatedUser = Field(description="The authenticated end user.")


class BotPrincipal(BaseModel):
    """A bot/agent caller, produced by the gateway.

    Ownership and resource resolution anchor to ``bot`` **within** ``tenant``.
    """

    model_config = ConfigDict(frozen=True)

    type: Literal[PrincipalType.BOT] = PrincipalType.BOT
    tenant: str = Field(description="Tenant id the caller belongs to (stable id).")
    bot: Bot = Field(description="The authenticated bot/agent identity.")


class AppPrincipal(BaseModel):
    """A third-party application caller, produced by the gateway."""

    model_config = ConfigDict(frozen=True)

    type: Literal[PrincipalType.APP] = PrincipalType.APP
    tenant: str = Field(description="Tenant id the caller belongs to (stable id).")
    app: ThirdPartyApp = Field(description="The authenticated third-party app.")
    on_behalf_of_opaque: str | None = Field(
        default=None,
        description=(
            "Opaque, unverified handle of the app's own end user. ``None`` is an "
            "intentional default (the app calls on its own behalf). Used only for "
            "ownership/quota/audit — never as an authenticated identity for "
            "cross-resource decisions."
        ),
    )


class AccessKey(BaseModel):
    """An access key a caller authenticated against.

    Carries its ``access_key_id``, the presented ``access_key_token`` (a secret
    flowing downstream — components must treat it as such), and its ``expire_at``.
    """

    model_config = ConfigDict(frozen=True)

    access_key_id: str  # stable id of the resolved access key
    access_key_token: str  # the presented/verified access-key credential (secret)
    expire_at: datetime  # when the access key expires


class AccessKeyPrincipal(BaseModel):
    """A caller authenticated via an access-key token, produced by the gateway.

    Ownership and resource resolution anchor to ``access_key`` **within**
    ``tenant``.
    """

    model_config = ConfigDict(frozen=True)

    type: Literal[PrincipalType.ACCESS_KEY] = PrincipalType.ACCESS_KEY
    tenant: str = Field(description="Tenant id the caller belongs to (stable id).")
    access_key: AccessKey = Field(description="The authenticated access key.")


# Discriminated union of all Principal kinds. pydantic serializes/deserializes
# cleanly by the ``type`` tag; members each carry only their kind's fields.
Principal = Annotated[
    UserPrincipal | BotPrincipal | AppPrincipal | AccessKeyPrincipal,
    Field(discriminator="type"),
]


class Presence(StrEnum):
    """Whether a route requires an identity or merely accepts it."""

    REQUIRED = "required"
    OPTIONAL = "optional"


@dataclass(frozen=True)
class CredentialBundle:
    """Framework-agnostic snapshot of a request's credentials."""

    headers: Mapping[str, str]
    cookies: Mapping[str, str]
    query: Mapping[str, str]
