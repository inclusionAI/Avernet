"""``GET /api/v1/org/user`` — directory-identity read keyed on ``?user_id=``.

A verified caller names a ``user_id`` and gets that user's directory entry;
the answer comes from the staff directory (the gateway signs only the caller,
so another user's identity is read off HR, not the principal), and the tenant
comes from the verified caller.

Mirrors ``GET /openapi/v1/org/user``'s directory-lookup branch at a separate
prefix; the differences are: the access dep is this module's own
:func:`require_org_user_caller` (over the cached, signature-verified
:func:`resolve_caller`), and an app-only caller is admitted — any verified
principal may look a user up. ``user_id`` is a REQUIRED directory filter with
no absent-param fall-back to the caller's own identity.

Failure contract (same split the sibling makes): no reader wired ⇒ null
identity+dept (``200``); directory unreachable (``DeptLookupError``) ⇒ ``5xx``;
no or invalid principal ⇒ the surface's uniform ``401``.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from agentclaw.community.adapters.http.org.dependencies import (
    require_org_user_caller,
)
from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.org.schemas import OrgUserIdentity
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.core.gateway_principal import VerifiedCaller
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.staff_dept import StaffDeptPlugin

logger = get_logger()

# A plain APIRouter (not PublicAPIRoute): this surface owns its access seam via
# ``require_org_user_caller`` rather than the openapi_v1 admission machinery, so
# it must not inherit ``_PUBLIC_AUTH`` (which would apply ``require_principal``
# and the table-driven end-user/app-only adjudication the public surface wants).
router = APIRouter(prefix="/api/v1/org/user", tags=["org"])


def _staff_dept_reader(request: Request) -> StaffDeptPlugin | None:
    """The staff-dept service for this caller, or ``None`` when not wired.

    Mirrors ``openapi_v1/org/router.py``'s reader: resolved off the request
    injector so an app with one yields the service and an app without (a bare
    ``FastAPI()`` test app, or a community/local profile that does not bind it)
    yields ``None`` — leaving the looked-up fields null (``200``) rather than
    the service becoming a hard requirement of the route.
    """
    injector = getattr(request.app.state, "injector", None)
    if injector is None:
        return None
    try:
        return injector.get(StaffDeptPlugin)
    except Exception:  # noqa: BLE001 — any resolution failure is "not wired"
        logger.warning("no %s bound; org/user fields stay null", StaffDeptPlugin.__name__)
        return None


@router.get("", response_model=Envelope[OrgUserIdentity])
@envelope_errors
async def get_org_user(
    request: Request,
    caller: Annotated[VerifiedCaller, Depends(require_org_user_caller)],
    user_id: Annotated[
        str,
        Query(
            alias="user_id",
            # min_length only (mirrors the sibling): a blank value names nobody
            # and is a 422. No upper bound — the identity boundary has none
            # (GatewayUser.id is unconstrained).
            min_length=1,
            description=(
                "Required directory filter — the work number of the user whose "
                "identity+department to return. The answer comes from the staff "
                "directory, not the verified caller. Any authenticated caller may "
                "name any user; there is no self-only restriction here."
            ),
        ),
    ],
) -> Envelope[OrgUserIdentity]:
    """Return the identity+department of the user named by ``user_id``.

    A directory lookup, not a whoami: ``user_id`` is required and
    authoritative, and that user's identity **and** department come from the
    staff directory (the gateway signs only the caller, so another user's
    identity is read off HR, not the principal). There is no absent-param
    fall-back to the caller's own identity.

    A reader that is not wired leaves the looked-up fields null (``200``); a
    real reader returns them, or an all-null info when the person has no
    record / no dept. A reader that fails (directory down) raises and surfaces
    as 5xx — so "no record" and "directory down" stay distinguishable.
    """
    info = None
    reader = _staff_dept_reader(request)
    if reader is not None:
        # ``DeptLookupError`` deliberately not caught — infra failure surfaces
        # as 5xx, distinct from the all-None "no record" 200, like the sibling.
        info = await asyncio.to_thread(reader.get_user_by_work_no, work_no=user_id)
    return envelope(
        OrgUserIdentity(
            user_id=user_id,
            username=info.username if info else None,
            display_name=info.display_name if info else None,
            full_name=info.full_name if info else None,
            tenant=caller.tenant,
            dept_no=info.dept_no if info else None,
            dept_name=info.dept_name if info else None,
            dept_path=info.dept_path if info else None,
        ),
        request,
    )
