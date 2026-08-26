"""Org group — ``GET /openapi/v1/org/user``: who the verified caller is.

The one operation whose *answer* is the end user, so it is also the one
user-scoped read that takes no ``user_id`` parameter: a client calls it to
*learn* that value, and requiring the parameter here would make the id a
precondition of discovering it. Everything it returns is read off the verified
principal the gateway signed — nothing comes from the request — so the answer
cannot be steered by the caller.

Who it is for: a browser client (Teamclaw) whose session credential is an
http-only cookie. The cookie authenticates every request — the gateway resolves
it into the signed principal — but the page's own code cannot read it, so the
client has no way to know the id it must thread through the rest of the
surface. It calls this once, caches the identity, and names it everywhere else;
a stale cache fails closed, because ``require_user_id`` refuses a ``user_id``
that no longer matches the credential with a 403.

Department is the one attribute not on the signed principal: the gateway
resolves identity only, not org membership, so the signed user carries no
dept fields. It is instead looked up through the StaffDeptPlugin Protocol,
which the corp profile wires to the HR org master-data service and the
offline/community columns leave as a no-dept noop. Resolved off the request
injector inside the handler (not a declared dependency), mirroring the grant
reader — so an app with no injector yields None and the fields stay null rather
than the service becoming a hard requirement of the route. The Protocol method
is sync; it is bridged with asyncio.to_thread. DeptLookupError is **not**
caught: "directory down" (5xx) is distinct from "no dept" (200 + null), and the
envelope_errors wrapper maps it.

Why it is ``REFUSED`` for an application acting alone (``admission.py``): an
app-only caller names no end user, so there is nothing to return — and its own
identity question ("which bots may I reach?") is already answered by
``GET /openapi/v1/bots/authorized``. The refusal is declared on the route with
``refuse_app_only_caller``, as every ``REFUSED`` operation declares it.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from agentclaw.community.adapters.http.openapi_v1.org.schemas import OrgUserIdentity
from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.errors import MissingPrincipalError
from agentclaw.community.adapters.http.openapi_v1.principal import (
    refuse_app_only_caller,
    USER_ID_QUERY,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.staff_dept import DeptSearchItem, StaffDeptPlugin
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute

logger = get_logger()

router = APIRouter(prefix="/openapi/v1/org/user", tags=["org"], route_class=PublicAPIRoute)
dept_router = APIRouter(prefix="/openapi/v1/org/dept", tags=["org"], route_class=PublicAPIRoute)

PrincipalDep = Annotated[Principal, Depends(require_principal)]

_REFUSES_APP_ONLY = [Depends(refuse_app_only_caller)]

#: Keyword query for department fuzzy search. Bounded the same way spaces list
#: ``keyword`` is (``max_length=128``); required (``min_length=1``) because an
#: empty search would scan the whole directory rather than return it.
KeywordQuery = Annotated[
    str, Query(min_length=1, max_length=128, description="Department-name search text.")
]


def _staff_dept_reader(request: Request) -> StaffDeptPlugin | None:
    """The staff-dept service for this caller, or ``None`` when not wired.

    Mirrors ``principal.py``'s ``_grant_reader`` shape: ``getattr`` so a bare
    ``FastAPI()`` test app with no injector yields ``None`` rather than an
    ``AttributeError``, and any resolution failure is logged and treated as
    "not wired". Unlike the grant reader, ``None`` is **not** fail-closed:
    dept is optional profile data, not an authorization decision, so a missing
    reader means "no dept to return" (null fields) rather than "refuse".
    """
    injector = getattr(request.app.state, "injector", None)
    if injector is None:
        return None
    try:
        return injector.get(StaffDeptPlugin)
    except Exception:  # noqa: BLE001 — any resolution failure is "not wired"
        logger.warning(
            "no %s bound; dept fields stay null for this whoami",
            StaffDeptPlugin.__name__,
        )
        return None


@router.get(
    "",
    response_model=Envelope[OrgUserIdentity],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def get_user_identity(
    request: Request,
    principal: PrincipalDep,
    user_id: Annotated[
        str | None,
        Query(
            alias=USER_ID_QUERY,
            # min_length only (mirrors require_user_id): a blank value must not
            # read as whoami — it names nobody and is a 422. No upper bound: the
            # identity boundary has none (GatewayUser.id is unconstrained).
            min_length=1,
            description=(
                "Optional directory filter — whose identity+department to "
                "return. ABSENT means whoami: return the verified caller's own "
                "identity, as before. PRESENT switches to a directory lookup of "
                "that user; any authenticated human caller may name any user — "
                "this is the OPPOSITE contract to the `user_id` on every other "
                "operation (which is who the call acts for and must equal the "
                "caller, 403 otherwise). There is no self-only 403 here. An "
                "app-only caller is still refused."
            ),
        ),
    ] = None,
) -> Envelope[OrgUserIdentity]:
    """Return the end user the caller's credential names, or — when
    `user_id` is present — the identity+dept of the user it names.

    Without the parameter this is the whoami: the identity the gateway resolved
    and signed (the id to use as `user_id` elsewhere), with dept looked up
    through the staff-dept service by the caller's work number. With the
    parameter it is a directory lookup: that user's identity **and** dept come
    from the staff directory (the gateway signs only the caller, so another
    user's identity is read off HR, not the principal). The two branches read
    different sources by design and are not asserted equal even for `self`.

    A reader that is not wired leaves the looked-up fields null (200); a real
    reader returns them, or an all-null info when the person has no record/no
    dept. A reader that fails (directory down) raises and surfaces as 5xx — so
    "no record" and "directory down" stay distinguishable, the same split the
    whoami dept read makes.
    """
    if user_id is None:
        # whoami — UNCHANGED. Identity off the verified principal; dept via
        # get_dept_by_work_no(work_no=user.id). ``getattr`` so a bare FastAPI()
        # test app with no injector + a hand-constructed principal lands in the
        # fail-closed branch rather than on an AttributeError.
        user = getattr(principal, "user", None)
        if user is None:
            # Unreachable behind ``refuse_app_only_caller`` for a verified caller;
            # kept as the fail-closed answer for a hand-constructed principal.
            raise MissingPrincipalError("principal names no end user")

        dept_no = None
        dept_name = None
        dept_path = None
        reader = _staff_dept_reader(request)
        if reader is not None:
            # ``get_dept_by_work_no`` is a sync Plugin method (mirrors antprocess's
            # sync surface); bridge to the async handler via ``asyncio.to_thread``.
            # ``DeptLookupError`` is deliberately not caught — infra failure
            # surfaces as a 5xx distinct from the all-``None`` "no dept" return.
            info = await asyncio.to_thread(
                reader.get_dept_by_work_no, work_no=user.id
            )
            dept_no = info.dept_no
            dept_name = info.dept_name
            dept_path = info.dept_path

        return envelope(
            OrgUserIdentity(
                user_id=user.id,
                username=user.username,
                display_name=user.display_name,
                full_name=user.full_name,
                tenant=principal.tenant,
                # Optional profile data off the staff directory, null when the
                # reader is unwired or the person has no dept (200); a directory
                # failure raised above rather than landing null here.
                dept_no=dept_no,
                dept_name=dept_name,
                dept_path=dept_path,
            ),
            request,
        )

    # directory lookup — relaxed: a human caller may name any user. Identity
    # AND dept come from the staff directory (the gateway signs only the
    # caller). No self-only 403: this is the opposite-contract user_id, carved
    # out of the explicit-user-id rule (see _DIRECTORY_USER_ID in
    # test_explicit_user_id.py).
    info = None
    reader = _staff_dept_reader(request)
    if reader is not None:
        # ``DeptLookupError`` deliberately not caught — infra failure surfaces as
        # 5xx, distinct from the all-None "no record" 200, like the whoami read.
        info = await asyncio.to_thread(reader.get_user_by_work_no, work_no=user_id)
    return envelope(
        OrgUserIdentity(
            user_id=user_id,
            username=info.username if info else None,
            display_name=info.display_name if info else None,
            full_name=info.full_name if info else None,
            tenant=principal.tenant,
            dept_no=info.dept_no if info else None,
            dept_name=info.dept_name if info else None,
            dept_path=info.dept_path if info else None,
        ),
        request,
    )


@dept_router.get(
    "",
    response_model=Envelope[list[DeptSearchItem]],
)
@envelope_errors
async def search_depts(
    request: Request,
    principal: PrincipalDep,
    keyword: KeywordQuery,
) -> Envelope[list[DeptSearchItem]]:
    """Fuzzy-search the department directory by name.

    A tenant-wide catalogue read — the match set does not depend on which user
    is asking — so it takes no user id and admits any authenticated caller.
    Returns the matching departments; an empty list means nothing matched (200,
    not a failure). A directory that is unreachable or errors raises and is
    answered 5xx, distinct from "no match" the way "no dept" is distinct from
    "directory down".
    """
    del principal  # authenticated by ``_PUBLIC_AUTH``; identity is not used here.
    reader = _staff_dept_reader(request)
    if reader is None:
        # No staff service wired (singlebox / community) — empty result, 200.
        return envelope([], request)
    items = await asyncio.to_thread(reader.search_depts, keyword=keyword)
    return envelope(items, request)

