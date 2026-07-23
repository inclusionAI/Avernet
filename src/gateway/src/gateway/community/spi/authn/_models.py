"""Authn SPI — the neutral Principal the gateway produces after authentication.

Seeds the ``Principal`` model from the API Gateway auth design
(``src/gateway/docs/2026-07-21-auth-design.md`` §4). The gateway authenticates a
request, builds a ``Principal``, and forwards it (signed) to downstream
components, which project it onto their own domain DTOs. The gateway never
lets a component see raw credentials.

This round defines the first-party :class:`UserPrincipal`. ``AppPrincipal``
(third-party app) and the discriminated ``Principal`` union land when
third-party / app-principal access is added; ``PrincipalType`` is left open for
that.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from gateway.community.spi.auth import AuthenticatedUser


class PrincipalType(StrEnum):
    """Discriminator for the kind of caller a ``Principal`` represents."""

    USER = "user"  # a first-party authenticated user
    # THIRD_PARTY_APP = "third_party_app"  # added with app-principal access


class UserPrincipal(BaseModel):
    """A first-party authenticated user, produced by the gateway.

    The gateway builds this after authenticating the request, then forwards it
    downstream. Ownership and authorization resolve to ``subject`` **within**
    ``tenant`` — the tenant is always present so a caller is never ambiguous
    about which tenant's data it may touch.
    """

    model_config = ConfigDict(frozen=True)

    type: Literal[PrincipalType.USER] = PrincipalType.USER
    tenant: str = Field(
        description="Tenant id the caller belongs to (stable id, not a display name)."
    )
    scopes: frozenset[str] = Field(
        default_factory=frozenset,
        description="Permission scopes granted to the caller.",
    )
    subject: AuthenticatedUser = Field(description="The authenticated end user.")


# `Principal` becomes a discriminated union (UserPrincipal | AppPrincipal | ...)
# when app-principal access lands; for now the only member is UserPrincipal.
Principal = UserPrincipal
