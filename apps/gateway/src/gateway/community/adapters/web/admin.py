"""Admin endpoints — issue access keys, register apps, migrate secbaas keys.

NOT FOR PRODUCTION: these endpoints are **unauthenticated** (single-box / dev
convenience). A production deployment must gate them behind an admin credential
(not in scope for this workstream).

``POST /apps/migrate-from-baas`` is the one exception to that sentence, and it
is worth being precise about why: it is authenticated, just not by an admin
credential. The caller presents their own secbaas API key, and the migration
proceeds only if that key verifies against an ACTIVE ``baas_api_key`` row — so
the endpoint can only ever act on a credential its caller already holds. It
needs to work that way: nobody, operator included, can list a user's secbaas
keys, so a migration that required an administrator could not be performed at
all.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from gateway.community.api.app_registration import AppNameTakenError
from gateway.community.logger import get_logger

logger = get_logger("admin")

router = APIRouter(tags=["admin"])


class AccessKeyRequest(BaseModel):
    access_key: str
    tenant: str
    expire_at: datetime
    creator: str = Field(min_length=1)


class AppRequest(BaseModel):
    app_name: str
    owners: str
    app_type: str = "UNKNOWN"
    tenant: str
    creator: str = Field(min_length=1)
    # Constrained, not free text: authentication compares this to "ACTIVE"
    # exactly, so accepting "active" would mint a key that can never
    # authenticate while returning 201 as though registration had worked.
    # REVOKED is excluded deliberately — it is a transition applied to an
    # existing app, and registering straight into it would mint a credential
    # that is dead on arrival. Migrated rows may still carry it; those are
    # refused at lookup, which is the intended handling.
    status: Literal["ACTIVE", "INACTIVE"] = "ACTIVE"
    env: str = ""
    config: dict[str, Any] = Field(default_factory=dict)


class BaasKeyMigrationRequest(BaseModel):
    """Migrate one secbaas API key onto the gateway.

    ``api_key`` is the caller's existing **plaintext** secbaas key. It is the
    credential being migrated and the authorization for migrating it; it is
    never stored (the row copies the hash secbaas already holds), never logged,
    and never echoed back.

    ``app_name`` is the caller's own choice and need not match anything in
    secbaas — ``baas_api_key`` has no per-environment unique name to carry over.
    It must be unique within the migrated environment, so a taken name comes back
    as a 409 naming both halves rather than as a failed insert.

    Both fields carry a default instead of being required, and that is a
    disclosure decision rather than laxity. FastAPI renders a "field required"
    error with ``input`` set to the **whole request body** — which on this
    endpoint holds the caller's plaintext key — so a client that forgot
    ``app_name`` would get its own credential quoted back inside a 422. Fields
    that always parse produce no such error; emptiness is checked in the handler
    instead, which names the missing field without quoting anything. ``app_name``
    likewise carries no ``max_length`` here: the core refuses an over-long name
    with a message of its own, and a constraint in this model would re-render it
    through the echoing path for no gain.
    """

    api_key: str = Field(default="", description="Plaintext secbaas API key")
    app_name: str = Field(
        default="",
        description="Name for the migrated application (unique per environment)",
    )


# How each migration outcome is reported. Keyed by the string values of
# ``api.baas_migration.MigrationOutcome``. The enum is not imported: the outcome
# reaches here as a value on a result object, and a table of literals keeps this
# module free of any dependency for a subsystem that is meant to be deleted.
# ``tests/unit/adapters/web/test_admin_migration_outcomes.py`` pins the two sides
# together, so an outcome added upstream fails a test rather than silently
# falling through to the 400 default below.
#
# ``already_migrated`` and ``app_name_taken`` are both 409 but mean opposite
# things to the caller: the first says the work is done and no retry is needed,
# the second says the same request will succeed with one field changed. The
# response body's ``data`` carries what tells them apart.
_MIGRATION_ERROR_STATUS: dict[str, tuple[int, int]] = {
    "key_not_found": (404, 1),
    "already_migrated": (409, 1),
    "app_name_taken": (409, 2),
    "prefix_conflict": (409, 3),
    "wildcard_policy": (422, 1),
    "invalid_grant_targets": (422, 2),
    "unsupported_app_type": (422, 3),
    "value_too_long": (422, 4),
}


def _error(
    status: int, subcode: int, message: str, data: Any | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"code": status * 1000 + subcode, "message": message, "data": data},
    )


@router.post("/access-keys", status_code=201)
async def issue_access_key(payload: AccessKeyRequest, request: Request) -> JSONResponse:
    issuer = request.app.state.access_key_issuer
    try:
        issued = await issuer.issue(
            payload.access_key,
            payload.tenant,
            payload.expire_at,
            creator=payload.creator,
        )
    except Exception:
        logger.exception("access key issuance failed")
        return _error(500, 1, "access key issuance failed")
    return JSONResponse(
        status_code=201,
        content={
            "access_key": issued.access_key,
            "tenant": issued.tenant,
            "expire_at": issued.expire_at.isoformat(),
            "token": issued.token,
        },
    )


@router.post("/apps", status_code=201)
async def register_app(payload: AppRequest, request: Request) -> JSONResponse:
    registrar = request.app.state.app_registrar
    try:
        issued = await registrar.register(
            payload.app_name,
            payload.owners,
            payload.app_type,
            payload.tenant,
            creator=payload.creator,
            status=payload.status,
            env=payload.env,
            config=payload.config,
        )
    except AppNameTakenError as exc:
        # A real, actionable refusal rather than a fault: ``(app_name, env)`` is
        # unique, and the caller resolves it by choosing another name. Caught
        # before the blanket handler below, which would otherwise report this as
        # a 500 and leave them with nothing to change.
        return _error(
            409,
            1,
            f"app_name {exc.app_name!r} is already used in env {exc.env!r}",
            {"app_name": exc.app_name, "env": exc.env},
        )
    except Exception:
        logger.exception("app registration failed")
        return _error(500, 1, "app registration failed")
    # ``api_key`` is returned here and nowhere else, ever: the registry keeps
    # only its hash, so a caller who loses it must be issued a new one.
    return JSONResponse(
        status_code=201,
        content={
            "id": issued.id,
            "app_name": issued.app_name,
            "owners": issued.owners,
            "app_type": issued.app_type,
            "tenant": issued.tenant,
            "status": payload.status,
            "env": payload.env,
            "api_key": issued.api_key,
        },
    )


@router.post("/apps/migrate-from-baas", status_code=201)
async def migrate_baas_key(
    payload: BaasKeyMigrationRequest, request: Request
) -> JSONResponse:
    """Copy an ACTIVE secbaas API key into the gateway, with its bot grants.

    The caller keeps using the key they already have: the row copies secbaas's
    stored hash, and the two registries run the same PBKDF2, so no new
    credential is minted and none is returned here.

    Either everything lands or nothing does — the application row and every
    grant are one transaction. A refusal therefore always means the caller's
    secbaas key is still their only working credential, which is what makes
    retrying safe.
    """
    # Stripped, not rejected, for surrounding whitespace: both values are
    # routinely pasted, neither can legitimately contain it (a key is base62,
    # and a leading space would silently break the prefix lookup), and two app
    # names differing only in padding are indistinguishable to the human reading
    # a listing but distinct to the unique index.
    api_key = payload.api_key.strip()
    app_name = payload.app_name.strip()
    missing = [
        name
        for name, value in (("api_key", api_key), ("app_name", app_name))
        if not value
    ]
    if missing:
        # Reported here rather than by the request model — see its docstring.
        return _error(
            400,
            1,
            f"missing required field(s): {', '.join(missing)}",
            {"missing": missing},
        )

    migrator = request.app.state.baas_key_migrator
    try:
        result = await migrator.migrate(api_key=api_key, app_name=app_name)
    except Exception:
        # ``.exception`` logs the active traceback (it is ``.error`` with
        # ``exc_info=True``), so the stack is in the log without being in the
        # response — a fault here is a server bug, and a caller who can post a
        # credential should not be shown the internals it failed in.
        #
        # ``app_name`` is logged with it because the trace alone does not say
        # WHICH migration raised, and concurrent attempts produce interleaved
        # traces that are otherwise indistinguishable. It is the caller's own
        # value and unique per environment, so it identifies the attempt exactly.
        #
        # Neither ``payload`` nor ``api_key`` nor its prefix is passed: the
        # logger renders whatever it is given, and no part of a live credential
        # belongs in a log file. The source row is recoverable from ``app_name``
        # by way of the request that carried it.
        logger.exception("baas key migration failed: app_name=%s", app_name)
        return _error(500, 1, "baas key migration failed")

    if not result.succeeded:
        # An unmapped outcome is a gap in this adapter, not something the caller
        # did — so it reports as a fault rather than as a refusal they could act
        # on. Unreachable while the pinning test passes.
        status, subcode = _MIGRATION_ERROR_STATUS.get(str(result.outcome), (500, 2))
        return _error(
            status,
            subcode,
            result.message,
            {"outcome": str(result.outcome), **dict(result.detail)},
        )

    app = result.app
    return JSONResponse(
        status_code=201,
        content={
            "id": app.id,
            "app_name": app.app_name,
            "app_type": app.app_type,
            "owners": app.owners,
            "tenant": app.tenant,
            "env": app.env,
            "api_key_prefix": app.api_key_prefix,
            "source_baas_key_id": app.source_key_id,
            # No ``api_key``: unlike registration, this endpoint mints nothing.
            # The caller's existing key is the credential and they already hold
            # it.
            "grants_created": len(app.grants),
            "grants": [
                {
                    "bot_id": g.bot_id,
                    "user_id": g.user_id,
                    "owner_id": g.owner_id,
                    "env": g.env,
                }
                for g in app.grants
            ],
        },
    )
