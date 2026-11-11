"""Management endpoint: backfill a Manifest's inline content into the content store (transitional).

Mounted under ``/api/system`` through the readiness router (see
``adapters/http/system/__init__.py``). Super-admin only. Remove with
``ContentStoreProtocol.ensure_stored`` once the rows filed before the bytes
moved to object storage have been backfilled.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from agentclaw.community.adapters.http.auth.dependencies import require_super_admin
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.api.manifest_inline_backfill_service import (
    InlineBackfillRowNotFound,
    ManifestInlineBackfillProtocol,
)
from agentclaw.community.core.content_store.errors import ContentStoreError, ContentStoreFault
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

logger = get_logger()

content_store_router = APIRouter(prefix="/content-store", tags=["content-store"])


class ManifestInlineBackfillRequest(BaseModel):
    """One ``ac_manifest_content`` row. The row's other columns may be sent along and are ignored."""

    model_config = ConfigDict(extra="ignore")

    entity_id: str = Field(..., min_length=1, description="The row's entity_id.")
    bot_id: str = Field(..., min_length=1, description="The row's bot_id.")
    digest: str = Field(..., min_length=1, description="The row's digest, sha256:<hex64>.")


class ManifestInlineBackfillEntry(BaseModel):
    category: str
    identity: str
    digest: str
    size_bytes: int


class ManifestInlineBackfillResponse(BaseModel):
    entity_id: str
    bot_id: str
    digest: str
    document_found: bool = Field(..., description="False when the bot has no stored Manifest any more.")
    row_digest_restored: bool = Field(
        ...,
        description=(
            "Whether the row's own digest is among the inline bodies stored. False with a document present "
            "means the Manifest no longer declares that inline content, or the row is not an inline one "
            "(manifest/tree, manifest/receipt): a re-apply restores those."
        ),
    )
    entries: list[ManifestInlineBackfillEntry] = Field(..., description="Every inline body stored.")


@content_store_router.post("/manifest-inline/backfill", response_model=ManifestInlineBackfillResponse)
def backfill_manifest_inline_content(
    request: ManifestInlineBackfillRequest,
    user: AuthenticatedUser = Depends(require_super_admin),  # COSEC: writes platform storage for any bot.
    service: ManifestInlineBackfillProtocol = Injected(ManifestInlineBackfillProtocol),
) -> ManifestInlineBackfillResponse:
    """Store the inline bodies of the Manifest this content row came from.

    Reads the bot's stored Manifest and writes every inline ``content`` body
    it declares (identity files and file resources) to the content store,
    skipping any the store already holds. Idempotent.

    A plain ``def``: the backfill reads the database and makes one storage
    request per inline body, so it runs in the thread pool rather than on
    the event loop.
    """
    logger.info(
        "content_store.manifest_inline_backfill.request actor_id=%s entity_id=%s bot_id=%s digest=%s",
        user.staffId, request.entity_id, request.bot_id, request.digest,
    )
    try:
        report = service.backfill(entity_id=request.entity_id, bot_id=request.bot_id, digest=request.digest)
    except InlineBackfillRowNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ContentStoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except ContentStoreFault as exc:
        logger.error("content_store.manifest_inline_backfill.failed bot_id=%s error=%s", request.bot_id, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from None
    return ManifestInlineBackfillResponse(
        entity_id=report.entity_id,
        bot_id=report.bot_id,
        digest=report.digest,
        document_found=report.document_found,
        row_digest_restored=report.row_digest_restored,
        entries=[ManifestInlineBackfillEntry(**vars(entry)) for entry in report.entries],
    )
