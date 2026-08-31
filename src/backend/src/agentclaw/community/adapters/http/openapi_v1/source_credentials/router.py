"""Source-credentials group — ``/openapi/v1/source-credentials`` (W3, #1471).

Tenant-level named credentials a manifest references by name; the platform
presents the secret while fetching within the stored prefixes. The write
path is REFUSED for machine-only callers (a secret write is the one
operation on this surface with no business running headless); reads are
REFUSED identically — the tenant's credential *names* and their scopes are
operator-visible metadata, not a machine-grantable surface.

Secrets never ride a response: PUT answers masked metadata, GET answers
masked metadata, and the apply-time report (W4) records the name only.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Deleted,
    Envelope,
    USER_SCOPED_403,
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
from agentclaw.community.adapters.http.openapi_v1.principal import (
    ActingCallerDep,
    refuse_app_only_caller,
)
from agentclaw.community.adapters.http.openapi_v1.bots.router import (
    _audit_actor,
)

from agentclaw.community.api.source_credential_service import (
    SourceCredentialServiceProtocol,
)
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.di import Injected

router = APIRouter(
    prefix="/openapi/v1/source-credentials",
    tags=["source-credentials"],
    route_class=PublicAPIRoute,
    # REFUSED made visible on the mount, not only in admission.py: the
    # central require_principal check refuses app-only callers either way;
    # the dependency keeps the decision legible and armour against a
    # mislabelled table entry (the admission-inventory gate's rule).
    dependencies=[Depends(refuse_app_only_caller)],
)


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
    it is the metadata an operator needs to rotate or reference: name,
    presence, last write.
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
    responses=USER_SCOPED_403,
)
@envelope_errors
async def put_source_credential(
    name: CREDENTIAL_NAME_PATH,
    body: SourceCredentialWrite,
    request: Request,
    caller: ActingCallerDep,
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
    """
    record = service.put(
        name=name,
        header_name=body.header_name or "",
        secret=body.secret,
        allowed_prefixes=body.allowed_prefixes,
        credential_type=body.type,
        # The verified principal composes the audit actor — the same rule
        # the bots group uses, and never the body. Who rotated a tenant's
        # token is the question this column exists to answer.
        modifier=_audit_actor(caller, caller.user_id),
    )
    return envelope(_detail(record), request)


@router.delete("/{name}", response_model=Envelope[Deleted])
@envelope_errors
async def delete_source_credential(
    name: CREDENTIAL_NAME_PATH,
    request: Request,
    service: SourceCredentialServiceProtocol = Injected(
        SourceCredentialServiceProtocol
    ),
) -> Envelope[Deleted]:
    """Remove the named credential. Idempotent succeeds on re-delete.

    Manifests referencing the deleted name fail their next fetch with the
    credential's name — removal does not translate into silent unauthenticated
    fetches. Storage never guesses the reference graph.
    """
    service.delete(name=name)
    return deleted_envelope(request)
