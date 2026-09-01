"""Source-credentials group — ``/openapi/v1/bots/source-credentials`` (W3, #1471).

Tenant-level named credentials a manifest references by name; the platform
presents the secret while fetching within the stored prefixes. This is an
application-operated surface: the gateway's route_security requires an app
credential on every path of the group — a human credential alone cannot
reach it — so the caller arriving here is an application of the tenant,
with a user identity riding along only for audit attribution.

Ownership is the one mutation boundary: a name belongs to the application
that created it, and rotation (re-PUT) and delete are the owner's calls
alone, enforced in the service against the stored row. Reads — masked
metadata, the name is the reference namespace manifests cite — belong to
every application of the tenant.

Secrets never ride a response: PUT answers masked metadata, GET answers
masked metadata, and the apply-time report (W4) records the name only.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Deleted,
    Envelope,
)
from agentclaw.community.adapters.http.openapi_v1.errors import (
    MissingPrincipalError,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    deleted as deleted_envelope,
    envelope,
    envelope_errors,
)
from agentclaw.community.adapters.http.openapi_v1.source_credentials.schemas import (
    CREDENTIAL_NAME_PATH,
    SourceCredential,
    SourceCredentialDetail,
    SourceCredentialWrite,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    caller_app_id,
    caller_owner_id,
)

from agentclaw.community.api.source_credential_service import (
    SourceCredentialServiceProtocol,
)
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.di import Injected

router = APIRouter(
    prefix="/openapi/v1/bots/source-credentials",
    tags=["source-credentials"],
    route_class=PublicAPIRoute,
    # The floor the group carries itself (the market-group pattern) rather
    # than leaning on the mount's ``_PUBLIC_AUTH``: every operation here
    # is reachable only through a verified caller even in a fixture app
    # that mounts the router bare. The mutating handlers additionally
    # read the caller via ``PrincipalDep`` — the whole principal, because
    # ownership and audit are app-scoped.
    dependencies=[Depends(require_principal)],
)

#: The caller, for the operations that must name the application they act
#: under. Ownership and audit are app-scoped here, so the two mutating
#: handlers read the app off the verified principal — the same shape the
#: ``authorized`` group uses to scope by application identity.
PrincipalDep = Annotated[Principal, Depends(require_principal)]


def _owner_app_id(principal: Principal) -> int:
    """The calling application's id, or a 401-shaped refusal.

    The edge already requires an app credential on this group; a caller
    arriving without one is a misconfigured gateway, not a caller to
    answer — and ownership cannot be attributed to an application the
    credential does not name.
    """
    app_id = caller_app_id(principal)
    if app_id is None:
        raise MissingPrincipalError(
            "no verified application caller for this request"
        )
    return app_id


def _actor(app_id: int, principal: Principal) -> str:
    """The audit actor: the application, with any riding user named after it.

    The ``bots`` group's rule (``_audit_actor``) generalized to a caller
    that may carry no user at all: an application names itself, and keeps
    the user it acted for alongside when one is on the credential.
    """
    try:
        return f"app:{app_id}:on-behalf-of:{caller_owner_id(principal)}"
    except MissingPrincipalError:
        return f"app:{app_id}"


def _summary(record) -> SourceCredential:
    return SourceCredential(
        name=record.name,
        has_secret=record.has_secret,
        updated_at=record.updated_at,
    )


def _detail(record) -> SourceCredentialDetail:
    return SourceCredentialDetail(
        name=record.name,
        has_secret=record.has_secret,
        type=record.credential_type,
        header_name=record.header_name,
        allowed_prefixes=record.allowed_prefixes,
        owner_app_id=record.owner_app_id,
        updated_at=record.updated_at,
    )


@router.get("", response_model=Envelope[list[SourceCredential]])
@envelope_errors
async def list_source_credentials(
    request: Request,
    service: SourceCredentialServiceProtocol = Injected(
        SourceCredentialServiceProtocol
    ),
) -> Envelope[list[SourceCredential]]:
    """Every credential in the caller's tenant, masked metadata only.

    Sorted by name. The response never contains a secret or ciphertext —
    it is the metadata a calling application needs to rotate or reference:
    name, presence, last write.
    """
    records = service.list_credentials()
    return envelope([_summary(r) for r in records], request)


@router.get(
    "/{name}", response_model=Envelope[SourceCredentialDetail]
)
@envelope_errors
async def get_source_credential(
    name: CREDENTIAL_NAME_PATH,
    request: Request,
    service: SourceCredentialServiceProtocol = Injected(
        SourceCredentialServiceProtocol
    ),
) -> Envelope[SourceCredentialDetail]:
    """One credential by name: masked metadata including its scopes.

    404 when no credential is registered under this name in the caller's
    tenant.
    """
    record = service.get(name=name)
    return envelope(_detail(record), request)


@router.put(
    "/{name}",
    response_model=Envelope[SourceCredentialDetail],
)
@envelope_errors
async def put_source_credential(
    name: CREDENTIAL_NAME_PATH,
    body: SourceCredentialWrite,
    request: Request,
    principal: PrincipalDep,
    service: SourceCredentialServiceProtocol = Injected(
        SourceCredentialServiceProtocol
    ),
) -> Envelope[SourceCredentialDetail]:
    """Register or rotate the named credential. Takes effect on the next
    fetch that references it — no apply runs, ever.

    The body validates before any persistence: HTTPS-pinned prefixes,
    path-segment scopes, reserved mechanism types refused, and — under a
    fail-closed deployment (production profile) — writes refused entirely
    when the platform's master key is not resolvable, so a misconfigured
    key store surfaces as a loud 503 rather than plaintext tenant secrets
    at rest.

    Registering a free name makes the calling application its owner;
    rotating an existing one is the owner's call alone (403 for any other
    application of the tenant), because a rotation replaces the whole row
    every manifest citation of that name depends on.
    """
    app_id = _owner_app_id(principal)
    record = service.put(
        name=name,
        header_name=body.header_name or "",
        secret=body.secret,
        allowed_prefixes=body.allowed_prefixes,
        owner_app_id=app_id,
        credential_type=body.type,
        # The verified principal composes the audit actor — never the body.
        # Who rotated a tenant's token is the question this column exists
        # to answer, and here "who" is an application.
        modifier=_actor(app_id, principal),
    )
    return envelope(_detail(record), request)


@router.delete("/{name}", response_model=Envelope[Deleted])
@envelope_errors
async def delete_source_credential(
    name: CREDENTIAL_NAME_PATH,
    request: Request,
    principal: PrincipalDep,
    service: SourceCredentialServiceProtocol = Injected(
        SourceCredentialServiceProtocol
    ),
) -> Envelope[Deleted]:
    """Remove the named credential. Idempotent succeeds on re-delete.

    The delete is the owning application's alone (403 for any other
    application of the tenant). Manifests referencing the deleted name
    fail their next fetch with the credential's name — removal does not
    translate into silent unauthenticated fetches. Storage never guesses
    the reference graph.
    """
    service.delete(name=name, caller_app_id=_owner_app_id(principal))
    return deleted_envelope(request)
