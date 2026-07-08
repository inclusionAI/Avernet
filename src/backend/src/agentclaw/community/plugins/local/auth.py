"""LocalAuth — local-mode AuthPlugin implementation.

Provides cookie/header/query-based identity parsing for singlebox
development mode. All users are allowed (no whitelist enforcement).
"""
from http.cookies import SimpleCookie
from urllib.parse import unquote

from fastapi import HTTPException

from agentclaw.community.core.auth.models import AuthenticatedIdentity
from agentclaw.community.core.errors import Unauthorized
from agentclaw.community.plugin_api.auth import AuthPlugin, AuthRequestContext
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.local._mock_seam import MockSeam


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.FAKE,
    rationale="in-memory cookie parsing, no network I/O",
)
class LocalAuth(MockSeam, AuthPlugin):
    """Local AuthPlugin: cookie-based identity, no whitelist."""

    async def get_login_user(
        self,
        cookie: str,
        referer: str | None = None,
    ) -> AuthenticatedIdentity:
        """Parse user identity from cookie string.

        Looks for ``staff_id`` or ``entity_id`` in the cookie.
        """
        if not cookie:
            raise HTTPException(
                status_code=401,
                detail="Local mode: user identity required.",
            )

        parsed: SimpleCookie = SimpleCookie()
        parsed.load(cookie)

        staff_id = None
        for key in ("staff_id", "entity_id"):
            if key in parsed:
                staff_id = parsed[key].value
                break

        if not staff_id:
            raise HTTPException(
                status_code=401,
                detail="Local mode: staff_id or entity_id cookie required.",
            )

        raw_nick = parsed["nick_name"].value if "nick_name" in parsed else staff_id
        nick_name = unquote(raw_nick)

        return AuthenticatedIdentity(
            id=staff_id,
            operatorName=staff_id,
            outUserNo=staff_id,
            nickName=nick_name,
        )

    async def resolve_user_from_request(
        self,
        ctx: AuthRequestContext,
    ) -> AuthenticatedIdentity:
        """Identity resolution order (mirrors the legacy
        ``core/auth/dependencies.get_local_user_from_request``):

        1. Cookie: ``staff_id`` / ``entity_id``
        2. Header: ``x-user-id``
        3. Query parameter: ``user_id``  (used by frontend ``DevUserInput``)

        Raises ``Unauthorized`` if no identity is found — matches the
        legacy 401 response.
        """
        # FastAPI/Starlette normalize header names to lowercase, so a
        # single lookup is enough.
        staff_id = (
            ctx.cookies.get("staff_id")
            or ctx.cookies.get("entity_id")
            or ctx.headers.get("x-user-id")
            or ctx.query_params.get("user_id")
        )
        if not staff_id:
            raise Unauthorized("Local mode: user identity required.")

        raw_nick = ctx.cookies.get("nick_name", staff_id)
        nick_name = unquote(raw_nick)
        return AuthenticatedIdentity(
            id=staff_id,
            operatorName=staff_id,
            outUserNo=staff_id,
            nickName=nick_name,
        )

    def is_operator_allowed(self, staff_id: str) -> bool:
        """Local mode: all users are allowed."""
        return True

    async def authorize_entity_access(
        self,
        ctx: AuthRequestContext,
        requested_entity_id: str | None,
        requested_entity_type: str | None,
    ) -> tuple[str | None, str | None]:
        """Local mode: passthrough — no SSO, no self-only check.
        Returns whatever the caller asked for, including ``(None, None)``.
        """
        return requested_entity_id, requested_entity_type
