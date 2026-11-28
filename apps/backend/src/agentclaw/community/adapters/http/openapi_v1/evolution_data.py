"""Narrow data callbacks for built-in RSI; never part of the public OpenAPI.

Only the Evolution control plane can sign a callback. Backend rechecks the
operator's current Bot access on every request, including after Run recovery.
"""

import asyncio
import hmac
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Literal

from agentclaw.community.api.bot_genome_promotion_service import (
    GenomePromotionServiceProtocol,
    ManualPromotionAuthorization,
)
from agentclaw.community.api.bot_genome_registry_service import GenomeRegistryProtocol
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_genome.canonical import canonicalize, loads_strict
from agentclaw.community.core.bot_genome.errors import LimitExceeded
from agentclaw.community.core.bot_genome.models import (
    BotRef,
    Provenance,
    ProvenanceKind,
    RevisionStatus,
    RunScope,
)
from agentclaw.community.core.bot_management.addressing import (
    BotAddressResolver,
    ResolvedBotContext,
)
from agentclaw.community.core.bot_management.owner_handoff.contracts import HandoffError
from agentclaw.community.core.bot_management.owner_handoff.guard import (
    assert_bot_mutable,
)
from agentclaw.community.core.engine_runtime.errors import EngineUpstreamError
from agentclaw.community.core.engine_runtime.models import BotFacts
from agentclaw.community.core.engine_runtime.session_key import SessionKeyCodecRegistry
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.di import Injected
from agentclaw.community.di.evolution_control_config import EvolutionControlConfig
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from agentclaw.community.utils.gateway_principal_config import (
    get_principal_verifier_config,
)
from avernet_evolution.adapters.backend_access import fingerprint
from avernet_evolution.adapters.backend_data import (
    HEADER,
    PREFIX,
    DataProof,
    mutation_scope_digest,
    verify_data_proof,
)
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from .bot_access import resolve_bot_permission
from .bots import genome
from .bots.schemas_genome import (
    GenomeDriftPayload,
    GenomeDriftResult,
    RecordPatchRequest,
)
from .contracts import BotIdPath, PageParams
from .engine_runtime.enums import RuntimeStage
from .engine_runtime.gating import resolve_operable_bot
from .engine_runtime.sessions.operator_reads import list_operator_messages, list_operator_sessions
from .engine_runtime.sessions.router import SessionIdPath
from .responses import envelope, mapped_error_response, unmapped_error_response
from .schemas_evolution_models import CompletionRequest, CompletionResult

# The signed JSON callback is bounded before parsing (also for chunked requests).
_MAX_BODY = 5 * 1024 * 1024


class _CallbackRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await handler(request)
            except HTTPException as exc:
                return unmapped_error_response(exc.status_code, request)
            except RequestValidationError:
                return unmapped_error_response(422, request)
            except Exception as exc:
                # Reuse registry/engine error semantics without capturing the
                # receipt, parsed patch or Bot row as public handler arguments.
                response = mapped_error_response(exc, request)
                if response is None:
                    raise
                return response

        return handle


router = APIRouter(prefix=PREFIX + "/{bot_id}", include_in_schema=False, route_class=_CallbackRoute)


@dataclass(frozen=True)
class _Access:
    proof: DataProof
    address: ResolvedBotContext
    body: bytes

    @property
    def bot(self) -> BotRef:
        return genome.bot_ref(dict(self.address.bot), owner_id=self.address.current_owner_id, bot_id=self.address.bot_id)

    def require_run(self) -> None:
        if not self.proof.run or not self.proof.parent or not self.proof.strategy:
            raise _refused()


def _refused() -> HTTPException:
    # Distinct from a missing Genome record (404).
    return HTTPException(403, "Evolution Bot data access unavailable")


async def _authorize(
    bot_id: BotIdPath,
    request: Request,
    config: Annotated[EvolutionControlConfig, Injected(EvolutionControlConfig)],
    bots: Annotated[BotRepository, Injected(BotRepository)],
) -> AsyncIterator[_Access]:
    tokens = request.headers.getlist(HEADER)
    if not config.builtin_data_tenant or len(tokens) != 1:
        raise _refused()
    try:
        proof = verify_data_proof(get_principal_verifier_config().signing_key, tokens[0])
    except PermissionError:
        raise _refused() from None
    if proof.tenant != config.builtin_data_tenant or proof.bot_id != bot_id:
        raise _refused()
    write = request.method == "POST"
    try:
        body = await genome.read_bounded_body(request, _MAX_BODY if write else 0)
    except LimitExceeded:
        raise HTTPException(413, "Evolution data request too large") from None
    try:
        query = request.scope["query_string"].decode("ascii")
    except UnicodeDecodeError:
        raise _refused() from None
    expected = fingerprint(request.method, request.scope["path"], query, body, request.headers)
    if not hmac.compare_digest(proof.request_hash, expected):
        raise _refused()

    with avernet_tenant_scope(proof.tenant):
        try:
            address = await asyncio.to_thread(BotAddressResolver(bots).resolve, bot_id=bot_id, entity_id=proof.entity_id)
        except Exception:
            raise _refused() from None
        if (address.bot_id, address.entity_id, address.current_owner_id) != (bot_id, proof.entity_id, proof.owner_id):
            raise _refused()
        level = await resolve_bot_permission(
            request, bot_id=bot_id, caller_id=proof.user_id, owner_id=address.entity_id,
            explicit=write, resolved_bot=address,
        )
        if level < (PermissionLevel.ADMIN if write else PermissionLevel.MEMBER):
            raise _refused()
        if write:
            try:
                assert_bot_mutable(address.bot)
            except HandoffError:
                raise _refused() from None
        yield _Access(proof, address, body)


_Authorized = Annotated[_Access, Depends(_authorize)]
_Registry = Annotated[GenomeRegistryProtocol, Injected(GenomeRegistryProtocol)]
_Relay = Annotated[EngineRuntimeRelayProtocol, Injected(EngineRuntimeRelayProtocol)]
_Stage = Literal["draft", "online"]
_FirstPage = Annotated[int, Query(ge=1, le=1)]


_Promotions = Annotated[GenomePromotionServiceProtocol, Injected(GenomePromotionServiceProtocol)]


@router.get("/genome/manual-promotions/{authorization_id}")
async def manual_promotion(authorization_id: str, request: Request, access: _Authorized, promotions: _Promotions):
    if access.proof.promotion_authorization_id != authorization_id:
        raise _refused()
    return envelope(genome._promotion(promotions.get_promotion(access.bot, authorization_id)), request)


@router.post("/genome/manual-promotions")
async def manual_promotion_apply(request: Request, access: _Authorized, promotions: _Promotions):
    access.require_run()
    try:
        auth = ManualPromotionAuthorization.model_validate(loads_strict(access.body.decode("utf-8")))
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(422, "Invalid manual authorization") from None
    proof = access.proof
    if (auth.id, auth.user_id, auth.run_id, auth.parent_revision_id, auth.strategy) != (
            proof.promotion_authorization_id, proof.user_id, proof.run, proof.parent, proof.strategy):
        raise _refused()
    return envelope(genome._promotion(await promotions.promote_manual(access.bot, auth)), request)


@router.get("/genome/drift")
async def drift(request: Request, access: _Authorized, registry: _Registry):
    observed = await registry.drift(access.bot)
    payload = None if observed is None else GenomeDriftPayload(active=observed.active, observed=observed.observed)
    return envelope(GenomeDriftResult(drift=payload), request)


@router.get("/genome/revisions/{rev:path}")
async def revision(rev: genome.RevPath, request: Request, access: _Authorized, registry: _Registry):
    return envelope(genome.revision_payload(registry, access.bot, rev), request)


@router.get("/genome/content/{digest}")
async def content(digest: genome.DigestPath, access: _Authorized, registry: _Registry):
    access.require_run()
    return Response(registry.read_content(access.bot, digest), media_type="application/octet-stream")


@router.post("/genome/revisions", status_code=201)
async def record_candidate(
    request: Request, response: Response, access: _Authorized, registry: _Registry
):
    access.require_run()
    proof = access.proof
    try:
        document = loads_strict(access.body.decode("utf-8"))
        canonicalize(document)
        body = RecordPatchRequest.model_validate(document)
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(422, "Invalid Evolution candidate") from None
    proposed = body.run
    if (
        body.status != "candidate" or proposed is None or proposed.step is not None
        or (proposed.run_id, proposed.strategy, proposed.allowed_genes, body.patch.base)
        != (proof.run, proof.strategy, proof.genes, proof.parent)
    ):
        raise _refused()
    if mutation_scope_digest(proposed.mutation_scope) != proof.mutation_scope_digest:
        raise HTTPException(
            status_code=403, detail="candidate mutation scope differs from frozen Run"
        )
    run = RunScope(
        proposed.run_id,
        proposed.strategy,
        tuple(proposed.allowed_genes),
        mutation_scope=proposed.mutation_scope,
    )
    principal = Provenance(ProvenanceKind.STRATEGY_RUN, run.strategy, run_id=run.run_id, principal=proof.user_id)
    recorded = await registry.commit_patch(access.bot, body.patch, created_by=principal, status=RevisionStatus.CANDIDATE, run=run)
    return envelope(genome.recorded_payload(recorded, response), request)


async def _runtime(access: _Access, relay: EngineRuntimeRelayProtocol, stage: str, *, surface: str = "sessions") -> BotFacts:
    access.require_run()
    facts = await resolve_operable_bot(
        relay, access.address.bot_id, caller_id=access.proof.user_id, owner_id=access.address.entity_id,
        stage=stage, surface=surface,
    )
    if facts.bot_id != access.address.bot_id or facts.owner_id != access.address.current_owner_id:
        raise _refused()
    return facts


@router.get("/sessions")
async def sessions(
    request: Request, access: _Authorized, relay: _Relay, stage: _Stage = "draft",
    page: _FirstPage = 1, page_size: Annotated[int, Query(ge=1, le=20)] = 20,
):
    facts = await _runtime(access, relay, stage)
    params: dict[str, int | str] = {"limit": page_size + 1, "offset": 0}
    if facts.owner_id == access.proof.user_id:
        params.update(user_id=access.proof.user_id, source="all_but_others")
    return await list_operator_sessions(
        request, relay, access.address.bot_id, access.address.entity_id, facts, RuntimeStage(stage),
        PageParams(page, page_size), params,
    )


@router.get("/sessions/{session_id}/messages")
async def messages(
    session_id: SessionIdPath, request: Request, access: _Authorized, relay: _Relay,
    codecs: Annotated[SessionKeyCodecRegistry, Injected(SessionKeyCodecRegistry)],
    stage: _Stage = "draft", page: _FirstPage = 1, page_size: Annotated[int, Query(ge=1, le=100)] = 100,
):
    if not session_id or session_id in {".", ".."} or any(
        c in "/\\?#%" or ord(c) < 32 or ord(c) == 127 for c in session_id
    ):
        raise HTTPException(422, "Invalid Evolution session id")
    facts = await _runtime(access, relay, stage)
    return await list_operator_messages(
        request, relay, codecs, access.address.bot_id, access.address.entity_id, facts, RuntimeStage(stage),
        session_id, PageParams(page, page_size),
    )


@router.post("/models:complete")
async def complete_model(
    request: Request, access: _Authorized, relay: _Relay, stage: _Stage = "draft",
):
    # POST authorization requires a live ADMIN operator; require a persisted
    # Run too. URLs, headers, keys, session IDs and tools are not accepted.
    facts = await _runtime(access, relay, stage, surface="models")
    try:
        body = CompletionRequest.model_validate(loads_strict(access.body.decode("utf-8")))
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(422, "Invalid Evolution model request") from None
    result = await relay.call(
        bot_id=access.address.bot_id, owner_id=access.address.entity_id, facts=facts,
        stage=stage, method="POST", path="/api/engine/models:complete",
        body=body.model_dump(), timeout=200,
    )
    try:
        completion = CompletionResult.model_validate(result.data)
        if body.model is not None and completion.model != body.model:
            raise ValueError("model mismatch")
    except ValueError:
        raise EngineUpstreamError("invalid engine model completion response") from None
    return envelope(completion, request)
