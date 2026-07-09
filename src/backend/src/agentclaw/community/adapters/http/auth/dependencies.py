"""FastAPI authentication dependencies.

Provides ``get_current_user()`` for injection into router endpoints.
Resolution of "local vs prod identity" lives in the injected
``AuthPlugin`` impl — this dep just adapts the FastAPI ``Request`` into
an ``AuthRequestContext`` and forwards to the plugin (Rule 14: mode
never reaches this layer).

The plugin returns its own user model (``AuthenticatedIdentity`` in prod);
this module converts to the adapter-owned :class:`AuthenticatedUser`
right at the boundary so routers depend only on the HTTP layer's
identity DTO.
"""
from fastapi import Depends, Header, Request

from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.core.errors import (
    DomainError,
    Forbidden,
    LoginRedirectRequired,
    Unauthorized,
)
from agentclaw.community.di import Injected
from agentclaw.community.plugin_api.auth import AuthPlugin, AuthRequestContext, AuthenticatedIdentity


def _build_auth_context(request: Request) -> AuthRequestContext:
    """Snapshot the FastAPI request into a framework-agnostic context."""
    return AuthRequestContext(
        cookies=dict(request.cookies),
        headers={k: v for k, v in request.headers.items()},
        query_params=dict(request.query_params),
        base_url=str(request.base_url),
    )


def _to_authenticated_user(buser: AuthenticatedIdentity) -> AuthenticatedUser:
    """Boundary converter: plugin user model → adapter-owned identity.

    Kept narrow on purpose — the adapter only surfaces the fields
    routers actually read. If a new attribute is needed, add it to
    :class:`AuthenticatedUser` and populate here; do not widen the
    surface by re-exposing the raw plugin model.
    """
    return AuthenticatedUser(
        id=buser.id,
        staffId=buser.staffId,
        operatorName=buser.operatorName,
        nickName=buser.nickName,
        tenantId=buser.tenantId,
    )


async def get_current_user(
    request: Request,
    auth_plugin: AuthPlugin = Injected(AuthPlugin),
) -> AuthenticatedUser:
    """FastAPI Depends — resolve the authenticated user via ``AuthPlugin``.

    Local impl reads from cookies/headers/query; prod impl runs the
    Buservice SSO call. Either way, this dep is mode-blind. The
    plugin's user model is converted to :class:`AuthenticatedUser`
    here so routers never type against the plugin's class.

    Errors:
        - ``Unauthorized``: the plugin reported missing identity (raised
          directly, not collapsed).
        - ``LoginRedirectRequired``: any other failure (transport, parse,
          missing prod cookie). Frontend treats the 302 as "restart login".
    """
    ctx = _build_auth_context(request)
    try:
        buser = await auth_plugin.resolve_user_from_request(ctx)
    except DomainError:
        # Plugin already raised a precise domain error (e.g. Unauthorized
        # in local mode when no identity is present) — preserve it.
        raise
    except Exception as e:
        # Plugin transport / parse failure — collapse to "go log in".
        raise LoginRedirectRequired("missing login cookie") from e
    return _to_authenticated_user(buser)


def get_current_staff_id(x_staff_id: str | None = Header(default=None)) -> str:
    """Legacy dependency — get staff ID from X-Staff-Id header."""
    if not x_staff_id:
        raise Unauthorized("missing login context")
    return x_staff_id


async def require_operator(
    user: AuthenticatedUser = Depends(get_current_user),
    auth_plugin: AuthPlugin = Injected(AuthPlugin),
) -> AuthenticatedUser:
    """FastAPI Depends — ensure the current user is an allowed operator.

    The auth plugin still owns the operator-allowlist policy; we pass
    the adapter identity as a tiny stand-in so the plugin signature
    stays unchanged. Plugin reads only ``staffId`` to decide.
    """
    if not auth_plugin.is_operator_allowed(user.staffId):
        raise Forbidden("权限不足：您没有操作员权限")
    return user
