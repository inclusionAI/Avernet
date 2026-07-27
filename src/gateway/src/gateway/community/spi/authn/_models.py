"""Authn SPI — the neutral Principal the gateway produces after authentication.

The gateway authenticates a request, builds one ``Principal`` per required
identity type, and forwards the set (signed) to downstream components, which
project each onto their own domain DTOs. The gateway never lets a component see
raw credentials — except the bot credential, which the bot identity carries
through by design (see the spec's Further Notes).

Identity types are modeled as a discriminated union on ``type``. Roles beyond
the first-party ``UserPrincipal`` and the calling ``BotPrincipal`` (e.g. the
deferred third-party ``AppPrincipal``) are added as new union members.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from gateway.community.spi.auth import AuthenticatedUser


class PrincipalType(StrEnum):
    """Discriminator for the kind of caller a ``Principal`` represents."""

    USER = "user"  # a first-party authenticated user
    BOT = "bot"  # a calling bot, acting in its own identity


class UserPrincipal(BaseModel):
    """A first-party authenticated user, produced by the gateway.

    Ownership and authorization resolve to ``subject`` **within** ``tenant``.
    Authorization scopes are NOT carried here — the gateway is auth-only; the
    component decides permissions.
    """

    model_config = ConfigDict(frozen=True)

    type: Literal[PrincipalType.USER] = PrincipalType.USER
    tenant: str = Field(
        description="Tenant id the caller belongs to (stable id, not a display name)."
    )
    subject: AuthenticatedUser = Field(description="The authenticated end user.")


class BotPrincipal(BaseModel):
    """A calling bot, acting in its own identity (not impersonating a user).

    ``bot_uuid`` is the bot's stable id; ``owner_id`` is the user who owns it
    (the resource-ownership anchor); ``token`` is the presented/verified bot
    credential (a secret flowing downstream — components must treat it as such);
    ``tenant`` is the owner's tenant, preserving the invariant that every
    Principal carries a tenant.
    """

    model_config = ConfigDict(frozen=True)

    type: Literal[PrincipalType.BOT] = PrincipalType.BOT
    tenant: str = Field(description="Owner's tenant (stable id).")
    bot_uuid: str = Field(description="The bot's stable identifier.")
    owner_id: str = Field(description="The user who owns the bot.")
    token: str = Field(description="The presented/verified bot credential (secret).")


Principal = Annotated[UserPrincipal | BotPrincipal, Field(discriminator="type")]


# ── Strategy inputs (framework-agnostic) ─────────────────────────────────────


@dataclass(frozen=True)
class CredentialBundle:
    """Framework-agnostic snapshot of a request's credentials.

    A delivery adapter fills this from the incoming request (e.g. a FastAPI
    ``Request``); an ``AuthStrategy`` reads it without depending on any web
    framework. ``source`` (if sent by the caller) is just another header here —
    the runner never reads it; plugins may read ``headers["source"]`` to decide
    whether they claim a request.
    """

    headers: Mapping[str, str]
    cookies: Mapping[str, str]
    query: Mapping[str, str]
