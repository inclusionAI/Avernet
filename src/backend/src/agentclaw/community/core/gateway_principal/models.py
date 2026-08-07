"""Backend-side projection of the gateway's forwarded Principal.

The gateway authenticates a caller, resolves every identity the request carries
into its own ``Principal`` discriminated union, and forwards that set to us
signed (auth design §7.1). These are **our** DTOs for that payload: the backend
never imports gateway types (Rule 7 / design §9), it projects the wire shape
onto the models below.

Wire contract (gateway ``spi/authn/_models.py``, forwarded by
``adapters/web/_forward.py``): each entry of the token's ``principals`` claim is
one Principal serialized with ``model_dump(mode="json")``, tagged by ``type``.
Unknown fields are ignored rather than rejected, so the gateway can add a field
without breaking us; a **renamed or removed** field it declares required fails
parsing, which fails the request closed. That is deliberate — a payload we
cannot read is not a caller we can scope.

Two deliberate omissions from the wire shape:

- ``Bot.token`` and ``AccessKey.access_key_token`` are **not projected.** They
  are live credentials the gateway forwards for components that need to act as
  the caller; this surface only needs to know *who* the caller is, and the
  cheapest way to not leak a secret is to never hold it.
- Nothing here carries scopes or permissions. The gateway resolves identity
  only; authorization stays ours (design §11).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class PrincipalType(StrEnum):
    """The kind of caller a principal represents (the union's ``type`` tag)."""

    USER = "user"
    BOT = "bot"
    APP = "app"
    ACCESS_KEY = "access_key"


class GatewayUser(BaseModel):
    """The authenticated end user carried by a ``user`` principal.

    Mirrors the gateway's ``AuthenticatedUser``: only ``id`` and ``username`` are
    guaranteed; the rest are profile attributes an identity provider may not
    supply, so they are optional here because absence is a real state of the
    contract — not defensive widening.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    username: str
    display_name: str | None = None
    full_name: str | None = None
    tenant_id: str | None = None


class GatewayApp(BaseModel):
    """The registered third-party application carried by an ``app`` principal."""

    model_config = ConfigDict(frozen=True)

    app_id: int
    app_name: str
    owners: str
    tenant: str
    app_type: str = "UNKNOWN"


class GatewayBot(BaseModel):
    """The bot/agent identity carried by a ``bot`` principal.

    ``owner_id`` is the bot's creator and the resource-ownership anchor — the
    field this surface scopes by when a bot calls on its own behalf.
    """

    model_config = ConfigDict(frozen=True)

    bot_uuid: str
    owner_id: str
    app_id: int
    agent_code: str
    tenant: str


class GatewayAccessKey(BaseModel):
    """The access key carried by an ``access_key`` principal.

    Carries no owner: the gateway's access-key registry
    (``avernet_access_key_token``) has no owner column, so an access-key caller
    identifies a *tenant*, not a person. See ``verifier.py`` for what that means
    for owner-scoped endpoints.
    """

    model_config = ConfigDict(frozen=True)

    access_key: str
    expire_at: datetime


class UserPrincipal(BaseModel):
    """A first-party authenticated user.

    Alone among the four, this principal carries **no tenant** — the gateway
    does not send one, because nothing in a user credential proves which tenant
    a person acts for (gateway ``spi/authn/_models.py``). A tenant is asserted
    by the *machine* identities: an app, a bot and an access key are each
    registered to one.

    So an identity set naming only a user asserts no tenant, and
    ``VerifiedCaller.tenant`` resolves it to :data:`DEFAULT_AVERNET_TENANT` —
    the internal tenant, which is the right scope for a first-party caller on
    our own frontend. That fallback is **ours**, decided here from the absence
    of a claim; it is not a value the token supplied. A token *may* also name
    ``teamclaw`` on a machine principal — a registered first-party tenant since
    2026-08-05 — and the two routes land on the same scope. What a ``user``
    entry says is still not one of them: a ``tenant`` smuggled onto one is an
    unknown field, dropped by the DTO rather than honoured.

    ``subject.tenant_id`` still carries whatever the identity provider said
    about the person. It is attribution, not an isolation key — nothing scopes
    by it.
    """

    model_config = ConfigDict(frozen=True)

    type: Literal[PrincipalType.USER] = PrincipalType.USER
    subject: GatewayUser

    @property
    def tenant(self) -> None:
        """User principals assert no isolation tenant.

        Kept as a read-only compatibility attribute for callers/tests that
        check ``principal.tenant is None``. It is intentionally not a Pydantic
        field, so a forged ``tenant`` key on a user principal is still ignored
        rather than honoured as an isolation claim.
        """
        return None


class BotPrincipal(BaseModel):
    """A bot/agent calling as a first-class caller in its own right."""

    model_config = ConfigDict(frozen=True)

    type: Literal[PrincipalType.BOT] = PrincipalType.BOT
    tenant: str
    bot: GatewayBot


class AppPrincipal(BaseModel):
    """A third-party application calling as itself."""

    model_config = ConfigDict(frozen=True)

    type: Literal[PrincipalType.APP] = PrincipalType.APP
    tenant: str
    app: GatewayApp


class AccessKeyPrincipal(BaseModel):
    """A caller authenticated against an access key."""

    model_config = ConfigDict(frozen=True)

    type: Literal[PrincipalType.ACCESS_KEY] = PrincipalType.ACCESS_KEY
    tenant: str
    access_key: GatewayAccessKey


# Discriminated by ``type``, exactly as the gateway serializes it, so a payload
# whose tag is unknown fails parsing instead of silently matching a member.
GatewayPrincipal = Annotated[
    UserPrincipal | BotPrincipal | AppPrincipal | AccessKeyPrincipal,
    Field(discriminator="type"),
]
