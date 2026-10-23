"""``/openapi/v1/bots/{bot_id}/genome`` — the Bot Genome Registry's public surface (RSI-03).

The router resolves the bot, calls the two Service APIs and shapes the
answer; every rule lives in ``core/bot_genome``. Route order is
load-bearing: ``:capture`` is a literal and ``/diff`` a suffix that the
``{rev:path}`` wildcard (needed for ``candidate/<run>/<n>``) must not
swallow, so both are registered before the get route.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError

from agentclaw.community.adapters.http.openapi_v1.admission import ActingCaller
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    USER_SCOPED_403,
    BotIdPath,
    Envelope,
    Page,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import OwnerIdDep
from agentclaw.community.adapters.http.openapi_v1.principal import ActingCallerDep, UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import envelope, envelope_errors, page
from agentclaw.community.api.bot_genome_promotion_service import GenomePromotionServiceProtocol
from agentclaw.community.api.bot_genome_registry_service import GenomeRegistryProtocol
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.bot_genome.canonical import NotCanonicalizable, canonicalize, loads_strict
from agentclaw.community.core.bot_genome.errors import LimitExceeded
from agentclaw.community.core.bot_genome.limits import MAX_UPLOAD_BYTES
from agentclaw.community.core.bot_genome.spec_models import GenomePolicy
from agentclaw.community.core.bot_genome.models import (
    BotRef,
    GenomeRef,
    Promotion,
    Provenance,
    ProvenanceKind,
    RecordedRevision,
    RefMover,
    RefUpdate,
    RevisionMetadata,
    RevisionStatus,
    RunScope,
)
from agentclaw.community.di import Injected

from .config_manifest_support import audit_actor, manifest_target
from .schemas_genome import (
    BotIdentityPayload,
    ContentStored,
    GenomeDiffPayload,
    GenomeRefPayload,
    GenomeRefsPayload,
    GenomeRevisionPayload,
    MoveRefRequest,
    PromoteRequest,
    PromotionPayload,
    RecordPatchRequest,
    RevisionRecorded,
    RevisionSummary,
)

router = APIRouter(prefix="/openapi/v1/bots/{bot_id}/genome", tags=["bots"], route_class=PublicAPIRoute)

# FastAPI binds each of these to the route's `{name}` placeholder by the parameter's name: `Path(...)` declares
# a path parameter (not a query one) and carries its OpenAPI description.
RevPath = Annotated[
    str,
    Path(description="A revision id (`sha256:…`), `r<seq>`, or a ref name (`active`, `draft`, `candidate/<run>/<n>`)."),
]
DigestPath = Annotated[
    str, Path(description="`sha256:<hex64>`, as a revision names it; the path parameter `{digest}`.")
]
PromotionIdPath = Annotated[str, Path(description="The id returned by `POST …/genome/promotions`.")]
PageQuery = Annotated[int, Query(ge=1, description="The page to return, counting from 1.")]
PageSizeQuery = Annotated[int, Query(ge=1, le=100, description="Items per page, at most 100.")]
StatusQuery = Annotated[
    Literal["draft", "candidate", "accepted", "rejected", "promoted", "archived"] | None,
    Query(
        description="Only revisions in this status: `draft`, `candidate`, `accepted`, `rejected`, `promoted` or "
        "`archived`."
    ),
]

_OCTET_STREAM_BODY = {
    "requestBody": {
        "required": True,
        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    }
}


def bot_ref(bot: dict[str, Any], *, owner_id: str, bot_id: str) -> BotRef:
    """The registry's view of a bot record: identity plus the engine and type facts the checks need."""
    return BotRef(
        entity_id=manifest_target(bot),
        bot_id=bot_id,
        owner_id=owner_id,
        engine_type=str(bot.get("active_engine") or ""),
        bot_type=str(bot.get("bot_type") or "personal"),
        entity_type=str(bot.get("entity_type") or "staff"),
    )


def _actor(actor_id: str, caller: ActingCaller, *, run: RunScope | None = None) -> Provenance:
    """Who the record names, and who the apply-time MCP permission check is made against.

    A user's record names the principal; an admitted application is that
    user for scoping, but the audit columns (``recorded_as``) name the
    application with the user it acted for, as the Manifest routes do
    (``audit_actor``). A run's candidate names the strategy (contract:
    ``actor`` is its ``id@version``) and carries the principal the run acts
    for as ``principal``, so the permission check is still made against a user.
    Its audit identity is the strategy and the run id, on purpose: the
    revision row has one actor column and the contract says what it holds,
    while the application that submitted the candidate on the run's behalf
    is the run record's to keep (06-evolution-run.md), not every revision's.
    """
    if run is not None:
        return Provenance(
            ProvenanceKind.STRATEGY_RUN, run.strategy, run_id=run.run_id, step=run.step, principal=actor_id
        )
    audit = audit_actor(caller, actor_id)
    recorded_as = None if audit == actor_id else audit
    return Provenance(ProvenanceKind.USER, actor_id, recorded_as=recorded_as)


async def reject_non_canonical_body(request: Request) -> None:
    """Contract §3.1: a body outside the canonical JSON subset is refused before the model parses it.

    FastAPI's JSON parsing keeps the last of two duplicate members, so a body
    with `risk_overrides` twice would record the second silently; it also
    accepts an escaped lone surrogate (`"\\ud800"`) and a float such as
    `1.0`, which the model would normalise and the registry then refuse while
    hashing, as a `500`. The raw body is read through the registry's strict
    loader and canonicalised first; malformed JSON is left to FastAPI's own
    parsing to answer.
    """
    body = await request.body()
    if not body:
        return
    try:
        canonicalize(loads_strict(body.decode("utf-8")))
    except NotCanonicalizable as exc:
        raise RequestValidationError(
            [{"type": "value_error", "loc": ("body",), "msg": f"not a contract record: {exc}"}]
        ) from exc
    except (UnicodeDecodeError, ValueError):
        return


def _summary(meta: RevisionMetadata, bot: BotRef) -> RevisionSummary:
    return RevisionSummary(
        id=meta.id,
        seq=meta.seq,
        bot=BotIdentityPayload(owner_id=bot.owner_id, bot_id=bot.bot_id),
        parent=meta.parent,
        status=meta.status.value,
        created_by=meta.created_by.as_dict(),
        created_at=meta.created_at,
        patch_digest=meta.patch_digest,
        evidence=list(meta.evidence),
        evaluations=list(meta.evaluations),
        annotations=dict(meta.annotations),
    )


def _recorded(recorded: RecordedRevision, response: Response) -> RevisionRecorded:
    """201 for new content, 200 when the registry already held it."""
    meta = recorded.revision.revision
    response.status_code = 201 if recorded.created else 200
    return RevisionRecorded(
        id=meta.id,
        seq=meta.seq,
        status=meta.status.value,
        parent=meta.parent,
        patch_digest=meta.patch_digest,
        risk_tier=recorded.risk_tier,
        flags=list(recorded.flags),
        ref=recorded.ref,
        created=recorded.created,
    )


def _ref(ref: GenomeRef) -> GenomeRefPayload:
    return GenomeRefPayload(
        name=ref.name, revision=ref.revision, moved_at=ref.moved_at, moved_by=ref.moved_by.as_dict(), reason=ref.reason
    )


def _promotion(promotion: Promotion) -> PromotionPayload:
    return PromotionPayload(
        id=promotion.id,
        ref=promotion.ref,
        revision=promotion.revision,
        from_revision=promotion.from_revision,
        document_digest=promotion.document_digest,
        going_back=promotion.going_back,
        actor=promotion.actor.as_dict(),
        reason=promotion.reason,
        status=promotion.status,
        apply_kind=promotion.apply_kind,
        apply_reference=promotion.apply_reference,
        apply_detail=promotion.apply_detail,
        created_at=promotion.created_at,
        document=promotion.document,
    )


# -- revisions ----------------------------------------------------------------------------------


@router.get(
    "/revisions",
    response_model=Envelope[Page[RevisionSummary]],
    responses=USER_SCOPED_403,
    operation_id="list_bot_genome_revisions",
)
@envelope_errors
async def list_genome_revisions(
    bot_id: BotIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    status: StatusQuery = None,
    parent: Annotated[str | None, Query(description="Only revisions derived from this revision id.")] = None,
    page_no: PageQuery = 1,
    page_size: PageSizeQuery = 20,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    registry: GenomeRegistryProtocol = Injected(GenomeRegistryProtocol),
) -> Envelope[Page[RevisionSummary]]:
    """List this bot's revisions, newest `seq` first. Metadata only; read one with `GET …/revisions/{rev}`."""
    bot = bot_ref(bot_service.get_bot(bot_id, owner_id), owner_id=owner_id, bot_id=bot_id)
    listed = registry.list_revisions(
        bot, status=None if status is None else RevisionStatus(status), parent=parent, page=page_no, page_size=page_size
    )
    return page(listed.total, [_summary(item, bot) for item in listed.items], request)


@router.post(
    "/revisions",
    response_model=Envelope[RevisionRecorded],
    responses=USER_SCOPED_403,
    status_code=201,
    operation_id="commit_bot_genome_patch",
)
@envelope_errors
async def commit_genome_patch(
    bot_id: BotIdPath,
    body: RecordPatchRequest,
    request: Request,
    response: Response,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    caller: ActingCallerDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    registry: GenomeRegistryProtocol = Injected(GenomeRegistryProtocol),
    _: None = Depends(reject_non_canonical_body),
) -> Envelope[RevisionRecorded]:
    """Commit a Genome Patch: apply it to its own `base` and record the result as a new revision.

    Every op must apply exactly to `patch.base`; the result must pass the
    checks apply would run. A `422` lists every reason. `201` for a new
    revision, `200` with the same id and seq when the content already
    existed. A `candidate` is named `candidate/<run>/<n>` and needs `run`.
    """
    bot = bot_ref(bot_service.get_bot(bot_id, owner_id), owner_id=owner_id, bot_id=bot_id)
    run = None
    if body.run is not None:
        run = RunScope(
            run_id=body.run.run_id,
            strategy=body.run.strategy,
            allowed_genes=tuple(body.run.allowed_genes),
            step=body.run.step,
        )
    actor = _actor(actor_id, caller, run=run)
    recorded = await registry.commit_patch(
        bot, body.patch, created_by=actor, status=RevisionStatus(body.status), run=run
    )
    return envelope(_recorded(recorded, response), request)


@router.post(
    "/revisions:capture",
    response_model=Envelope[RevisionRecorded],
    responses=USER_SCOPED_403,
    status_code=201,
    operation_id="capture_bot_genome_revision",
)
@envelope_errors
async def capture_genome_revision(
    bot_id: BotIdPath,
    request: Request,
    response: Response,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    caller: ActingCallerDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    registry: GenomeRegistryProtocol = Injected(GenomeRegistryProtocol),
) -> Envelope[RevisionRecorded]:
    """Record the bot's current state as a revision (parent `active`, or none for the first).

    `200` when the state is unchanged since the last record. A `422` names
    what cannot be captured (a resource path outside the Manifest rule, a
    platform-managed bot).
    """
    bot = bot_ref(bot_service.get_bot(bot_id, owner_id), owner_id=owner_id, bot_id=bot_id)
    recorded = await registry.capture(bot, created_by=_actor(actor_id, caller))
    return envelope(_recorded(recorded, response), request)


@router.get(
    "/revisions/{rev:path}/diff",
    response_model=Envelope[GenomeDiffPayload],
    responses=USER_SCOPED_403,
    operation_id="diff_bot_genome_revision",
)
@envelope_errors
async def diff_genome_revision(
    bot_id: BotIdPath,
    rev: RevPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    against: Annotated[
        str, Query(min_length=1, description="The revision to compare with (id, `r<seq>` or ref name).")
    ],
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    registry: GenomeRegistryProtocol = Injected(GenomeRegistryProtocol),
) -> Envelope[GenomeDiffPayload]:
    """What `rev` changes against `against`, per gene, with unified diffs for text files."""
    bot = bot_ref(bot_service.get_bot(bot_id, owner_id), owner_id=owner_id, bot_id=bot_id)
    diff = registry.diff(bot, rev, against)
    return envelope(
        GenomeDiffPayload(
            revision=diff.revision,
            against=diff.against,
            risk_tier=diff.risk_tier,
            rewrite_flags=list(diff.rewrite_flags),
            changes=[{"gene": c.gene, "kind": c.kind, "diff": c.diff} for c in diff.changes],
        ),
        request,
    )


# `{rev:path}` is Starlette's `path` converter: the parameter may contain `/`, which a candidate ref name
# (`candidate/<run>/<n>`) does; the plain `{rev}` would stop at the first slash.
@router.get(
    "/revisions/{rev:path}",
    response_model=Envelope[GenomeRevisionPayload],
    responses=USER_SCOPED_403,
    operation_id="get_bot_genome_revision",
)
@envelope_errors
async def get_genome_revision(
    bot_id: BotIdPath,
    rev: RevPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    registry: GenomeRegistryProtocol = Injected(GenomeRegistryProtocol),
) -> Envelope[GenomeRevisionPayload]:
    """One revision, whole: metadata, `spec` and `policy`. A revision of another bot is not found."""
    bot = bot_ref(bot_service.get_bot(bot_id, owner_id), owner_id=owner_id, bot_id=bot_id)
    revision = registry.get_revision(bot, rev)
    return envelope(
        GenomeRevisionPayload(
            genome_schema=revision.genome_schema,
            revision=_summary(revision.revision, bot),
            spec=revision.spec,
            policy=revision.policy,
        ),
        request,
    )


@router.put(
    "/policy",
    response_model=Envelope[RevisionRecorded],
    responses=USER_SCOPED_403,
    status_code=201,
    operation_id="set_bot_genome_policy",
)
@envelope_errors
async def set_genome_policy(
    bot_id: BotIdPath,
    body: GenomePolicy,
    request: Request,
    response: Response,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    caller: ActingCallerDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    registry: GenomeRegistryProtocol = Injected(GenomeRegistryProtocol),
    _: None = Depends(reject_non_canonical_body),
) -> Envelope[RevisionRecorded]:
    """Record a revision with `active`'s content and this policy. The policy is not bot state: it is what automation may
    change.
    """
    bot = bot_ref(bot_service.get_bot(bot_id, owner_id), owner_id=owner_id, bot_id=bot_id)
    recorded = registry.set_policy(bot, body, created_by=_actor(actor_id, caller))
    return envelope(_recorded(recorded, response), request)


# -- refs ---------------------------------------------------------------------------------------


@router.get(
    "/refs", response_model=Envelope[GenomeRefsPayload], responses=USER_SCOPED_403, operation_id="list_bot_genome_refs"
)
@envelope_errors
async def list_genome_refs(
    bot_id: BotIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    registry: GenomeRegistryProtocol = Injected(GenomeRegistryProtocol),
) -> Envelope[GenomeRefsPayload]:
    """Every ref of the bot (`active`, `previous`, `draft`, `candidate/<run>/<n>`, …) and where it points."""
    bot = bot_ref(bot_service.get_bot(bot_id, owner_id), owner_id=owner_id, bot_id=bot_id)
    return envelope(GenomeRefsPayload(refs=[_ref(r) for r in registry.list_refs(bot)]), request)


@router.put(
    "/refs/draft",
    response_model=Envelope[GenomeRefPayload],
    responses=USER_SCOPED_403,
    operation_id="move_bot_genome_draft_ref",
)
@envelope_errors
async def move_genome_draft_ref(
    bot_id: BotIdPath,
    body: MoveRefRequest,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    caller: ActingCallerDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    registry: GenomeRegistryProtocol = Injected(GenomeRegistryProtocol),
) -> Envelope[GenomeRefPayload]:
    """Point the owner's `draft` ref at a revision, by compare-and-swap.

    `draft` is the owner's bookmark for the revision they are working on;
    only the owner moves it (promotion moves `active` and `previous`, runs
    name candidates). `revision` is where it points afterwards, given as an
    id, `r<seq>` or a ref name. `expected_revision` is the revision id the
    caller last saw `draft` at, or null if `draft` does not exist yet; the
    move happens only if that is still true, otherwise `409` carries the
    current revision so the caller can re-read and decide. `404` for a
    revision that is not this bot's.
    """
    bot = bot_ref(bot_service.get_bot(bot_id, owner_id), owner_id=owner_id, bot_id=bot_id)
    target = registry.get_revision(bot, body.revision).revision.id
    update = RefUpdate("draft", target, body.expected_revision, reason=body.reason)
    return envelope(_ref(registry.move_ref(bot, update, actor=_actor(actor_id, caller), mover=RefMover.OWNER)), request)


# -- content ------------------------------------------------------------------------------------


@router.get(
    "/content/{digest}",
    response_model=None,
    responses={**USER_SCOPED_403, 200: {"content": {"application/octet-stream": {}}}},
    operation_id="read_bot_genome_content",
)
@envelope_errors
async def read_genome_content(
    bot_id: BotIdPath,
    digest: DigestPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    registry: GenomeRegistryProtocol = Injected(GenomeRegistryProtocol),
) -> Response:
    """The bytes a digest names, hash-verified. Only digests this bot's revisions or uploads reach are found.

    The body is the stored bytes themselves (a Markdown file, a CSV, an
    acm-tree-v1 tree, …), not JSON, so there is no envelope: the response
    is `application/octet-stream` and the caller checks the digest against
    what it received (contract §9.1).
    """
    bot = bot_ref(bot_service.get_bot(bot_id, owner_id), owner_id=owner_id, bot_id=bot_id)
    return Response(content=registry.read_content(bot, digest), media_type="application/octet-stream")


@router.put(
    "/content",
    response_model=Envelope[ContentStored],
    responses=USER_SCOPED_403,
    status_code=201,
    openapi_extra=_OCTET_STREAM_BODY,
    operation_id="put_bot_genome_content",
)
@envelope_errors
async def put_genome_content(
    bot_id: BotIdPath,
    request: Request,
    response: Response,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    caller: ActingCallerDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    registry: GenomeRegistryProtocol = Injected(GenomeRegistryProtocol),
) -> Envelope[ContentStored]:
    """Upload raw bytes (`application/octet-stream`) for a later patch to name by digest. `200` when this bot already
    held them.
    """
    bot = bot_ref(bot_service.get_bot(bot_id, owner_id), owner_id=owner_id, bot_id=bot_id)
    data = await read_bounded_body(request, MAX_UPLOAD_BYTES)
    receipt = registry.put_content(bot, data, provenance=_actor(actor_id, caller))
    response.status_code = 201 if receipt.created else 200
    return envelope(ContentStored(digest=receipt.digest, size=receipt.size, created=receipt.created), request)


async def read_bounded_body(request: Request, limit: int) -> bytes:
    """The request body, refused as ``LimitExceeded`` before more than ``limit`` bytes are read."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise LimitExceeded(f"an upload is at most {limit} bytes")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise LimitExceeded(f"an upload is at most {limit} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


# -- promotions ---------------------------------------------------------------------------------


@router.post(
    "/promotions",
    response_model=Envelope[PromotionPayload],
    responses=USER_SCOPED_403,
    status_code=201,
    operation_id="promote_bot_genome_revision",
)
@envelope_errors
async def promote_genome_revision(
    bot_id: BotIdPath,
    body: PromoteRequest,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    caller: ActingCallerDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    promotion: GenomePromotionServiceProtocol = Injected(GenomePromotionServiceProtocol),
) -> Envelope[PromotionPayload]:
    """Move `active` to `revision` by compare-and-swap and hand the compiled revision to delivery.

    Going back is promoting an earlier revision again (`previous`). Until
    direct delivery lands (RSI-04) every promotion is refused as `422` before
    any ref moves. A delivery that fails to start is recorded as
    `apply_failed` with the refs moved; the owner goes back by promoting
    `previous`.
    """
    bot = bot_ref(bot_service.get_bot(bot_id, owner_id), owner_id=owner_id, bot_id=bot_id)
    promoted = await promotion.promote(
        bot,
        revision=body.revision,
        ref="active",
        expected_revision=body.expected_revision,
        actor=_actor(actor_id, caller),
        reason=body.reason,
    )
    return envelope(_promotion(promoted), request)


@router.get(
    "/promotions",
    response_model=Envelope[Page[PromotionPayload]],
    responses=USER_SCOPED_403,
    operation_id="list_bot_genome_promotions",
)
@envelope_errors
async def list_genome_promotions(
    bot_id: BotIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    page_no: PageQuery = 1,
    page_size: PageSizeQuery = 20,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    promotion: GenomePromotionServiceProtocol = Injected(GenomePromotionServiceProtocol),
) -> Envelope[Page[PromotionPayload]]:
    """This bot's promotions, newest first, without their documents."""
    bot = bot_ref(bot_service.get_bot(bot_id, owner_id), owner_id=owner_id, bot_id=bot_id)
    listed = promotion.list_promotions(bot, page=page_no, page_size=page_size)
    return page(listed.total, [_promotion(item) for item in listed.items], request)


@router.get(
    "/promotions/{promotion_id}",
    response_model=Envelope[PromotionPayload],
    responses=USER_SCOPED_403,
    operation_id="get_bot_genome_promotion",
)
@envelope_errors
async def get_genome_promotion(
    bot_id: BotIdPath,
    promotion_id: PromotionIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    promotion: GenomePromotionServiceProtocol = Injected(GenomePromotionServiceProtocol),
) -> Envelope[PromotionPayload]:
    """One promotion with the compiled document it handed to delivery."""
    bot = bot_ref(bot_service.get_bot(bot_id, owner_id), owner_id=owner_id, bot_id=bot_id)
    return envelope(_promotion(promotion.get_promotion(bot, promotion_id)), request)
