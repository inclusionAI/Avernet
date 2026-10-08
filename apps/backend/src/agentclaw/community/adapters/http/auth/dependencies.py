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

import base64
import hmac
import json

from fastapi import Depends, Header, Request

from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.core.errors import (
    DomainError,
    Forbidden,
    LoginRedirectRequired,
    Unauthorized,
)
from agentclaw.community.di import Injected
from agentclaw.community.di.modules.config_module import _block
from agentclaw.community.plugin_api.auth import (
    AuthenticatedIdentity,
    AuthPlugin,
    AuthRequestContext,
)
from agentclaw.community.plugin_api.secret_resolver import SecretResolver


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


def _parse_sno_from_iam_token(iam_token: str) -> str | None:
    """Decode the unverified IAM JWT payload and return its ``sno`` claim.

    Trust comes from the preshared service Bearer checked by
    :func:`get_device_connection_user`, not from this payload decoder.
    """
    try:
        parts = iam_token.split(".")
        if len(parts) != 3:
            return None
        payload_segment = parts[1]
        padding = -len(payload_segment) % 4
        decoded = base64.urlsafe_b64decode(payload_segment + "=" * padding)
        payload = json.loads(decoded)
        sno = payload.get("sno")
        return sno.strip() if isinstance(sno, str) and sno.strip() else None
    except Exception:
        return None


async def get_device_connection_user(
    request: Request,
    auth_plugin: AuthPlugin = Injected(AuthPlugin),
    secret_resolver: SecretResolver = Injected(SecretResolver),
) -> AuthenticatedUser:
    """Authenticate the legacy device connection endpoint.

    ``x-iam-token`` explicitly selects the service-authenticated path:
    ``Authorization: Bearer <dima access secret>`` is verified first, then the
    caller work number is read from the IAM token.  Any failure is terminal and
    never downgraded to browser Cookie authentication.  Requests without
    ``x-iam-token`` retain the existing login flow.
    """
    iam_token = (request.headers.get("x-iam-token") or "").strip()
    if not iam_token:
        return await get_current_user(request=request, auth_plugin=auth_plugin)

    authorization = (request.headers.get("authorization") or "").strip()
    if not authorization.startswith("Bearer "):
        raise Unauthorized("Invalid authorization")
    bearer = authorization[7:].strip()

    secret_name = _block("dima").get("access_secret_name", "")
    if not secret_name:
        raise Unauthorized("Invalid authorization")
    try:
        secret = secret_resolver.get_secret(secret_name)
    except Exception as exc:
        raise Unauthorized("Invalid authorization") from exc
    expected = getattr(secret, "secret_value", None) if secret is not None else None
    if not expected or not hmac.compare_digest(bearer, str(expected)):
        raise Unauthorized("Invalid authorization")

    sno = _parse_sno_from_iam_token(iam_token)
    if not sno:
        raise Unauthorized("Invalid authorization")
    return AuthenticatedUser(id=sno, staffId=sno, operatorName=sno)


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
