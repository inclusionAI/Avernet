"""AuthPlugin — user authentication interface.

Implementations (selected by deploy profile):
- corp: ``plugins.prod.auth.BuserviceAuth`` (Buservice SSO)
- community: ``plugins.community.auth.OidcAuthPlugin`` (BCS unified auth — name retained)
- local/test: ``plugins.local.auth.LocalAuth``
"""
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field
from agentclaw.community.plugin_api.base import Plugin


@dataclass(frozen=True)
class AuthRequestContext:
    """Minimal request snapshot passed to ``AuthPlugin.resolve_user_from_request``.

    The Protocol stays framework-agnostic: it doesn't import FastAPI's
    ``Request`` type. The HTTP adapter (``core/auth/dependencies.py``)
    populates this struct from whatever request representation it has.
    """

    cookies: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    query_params: dict[str, str] = field(default_factory=dict)
    base_url: str = ""


class AuthenticatedIdentity(BaseModel):
    """Neutral caller identity returned by an ``AuthPlugin``.

    Provider-agnostic: each impl (corp SSO, community BCS, local) maps its
    own user representation onto these fields. The ``staffId``/``tenantId``
    aliases preserve the historical attribute surface so consumers read the
    same names regardless of provider.
    """
    model_config = ConfigDict(populate_by_name=True)

    id: str  # provider-specific user id
    mobileNumber: str | None = None
    nickName: str | None = None  # display name
    operatorName: str  # account / login name
    outUserNo: str = Field(alias="staffId")  # staff/work number — the canonical handle
    realName: str | None = None  # full name
    tntInstId: str | None = Field(default=None, alias="tenantId")  # tenant id

    @property
    def tenantId(self) -> str | None:
        return self.tntInstId

    @property
    def staffId(self) -> str:
        # The canonical handle core services key on (provider-agnostic).
        return self.outUserNo


@runtime_checkable
class AuthPlugin(Plugin, Protocol):
    """Authenticate a request and return the caller identity."""

    async def get_login_user(
        self,
        cookie: str,
        referer: str | None = None,
    ) -> AuthenticatedIdentity:
        """Authenticate via cookie and return the user identity.

        Args:
            cookie: Serialized cookie string from the HTTP request.
            referer: Referer header value (used by some auth backends).

        Returns:
            Authenticated user.

        Raises:
            HTTPException: If authentication fails.
        """
        ...

    async def resolve_user_from_request(
        self,
        ctx: AuthRequestContext,
    ) -> AuthenticatedIdentity:
        """Authenticate from a request snapshot and return the user.

        Local and prod impls each know how to extract identity from the
        request (cookies vs SSO call); callers stay mode-agnostic.

        Args:
            ctx: ``AuthRequestContext`` populated by the adapter.

        Returns:
            Authenticated user.

        Raises:
            Unauthorized / LoginRedirectRequired: per the existing
            ``core/auth/dependencies`` contract.
        """
        ...

    def is_operator_allowed(self, staff_id: str) -> bool:
        """Check if the work-number is in the operator whitelist.

        Args:
            staff_id: The caller's staff_id / work number.

        Returns:
            True if allowed, False otherwise.
        """
        ...

    async def authorize_entity_access(
        self,
        ctx: AuthRequestContext,
        requested_entity_id: str | None,
        requested_entity_type: str | None,
    ) -> tuple[str | None, str | None]:
        """Per-runtime entity-access policy.

        Returns the (entity_id, entity_type) the caller should actually
        query. Raises :class:`Unauthorized` / :class:`Forbidden` from
        ``core.errors`` when the caller is not allowed.

        Strictness varies by impl:

        - **local/test**: passthrough — returns the requested pair unchanged.
          No auth required in local boots.
        - **corp / community**: require an authenticated caller; default a
          missing ``entity_id`` to the caller's own staff id; forbid querying
          any other user's entity.

        Args:
            ctx: Request snapshot (cookies / headers / base_url).
            requested_entity_id: Caller-supplied entity id from query.
            requested_entity_type: Caller-supplied entity type from query.

        Returns:
            ``(effective_entity_id, effective_entity_type)``.

        Raises:
            Unauthorized: When the caller is unauthenticated and policy
                requires it.
            Forbidden: When the caller requests an entity they may not
                access.
        """
        ...
