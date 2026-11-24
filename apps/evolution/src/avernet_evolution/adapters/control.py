"""Authenticated OpenAPI v1 delivery; 01–06 only, never promotes a candidate."""

import logging
import secrets
from http import HTTPStatus
from typing import Any

from fastapi import APIRouter, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from ..control_contract import BindingsBody, CancelBody, ReviewBody, RunBody
from ..core.evolution_run.api import error_envelope

BASE = "/openapi/v1/bots/{bot_id}/evolution"


def response(request: Request, data: Any = None, *, status: int = 200, headers=None, error=None):
    trace = getattr(request.state, "trace_id", None) or secrets.token_hex(16)
    body = {"code": status * 1000, "message": HTTPStatus(status).phrase, "data": data, "request_id": trace}
    # 09 §2.1 要 `error.code`；追加一个成员而不是改既有信封，老调用方不受影响。
    if error is not None:
        body["error"] = error
    return JSONResponse(body, status_code=status, headers={"X-Trace-Id": trace, **(headers or {})})


def install_errors(app: FastAPI):
    @app.middleware("http")
    async def trace_and_errors(request, call_next):
        request.state.trace_id = secrets.token_hex(16)
        try:
            result = await call_next(request)
        except Exception as exc:  # noqa: BLE001 - sanitize all provider failures at HTTP boundary
            # Internal exceptions can contain credentials/provider URLs. Do not
            # return them or automatically dump request data into logs.
            logging.getLogger("avernet_evolution.http").error(
                "evolution.request.failed trace_id=%s exception_type=%s",
                request.state.trace_id,
                type(exc).__name__,
            )
            return response(request, status=500)
        result.headers["X-Trace-Id"] = request.state.trace_id
        return result

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return response(request, status=422)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return response(request, status=exc.status_code)

    @app.exception_handler(PermissionError)
    async def denied(request, exc):
        return response(request, status=403)

    async def domain_error(request, exc):
        envelope = error_envelope(exc)
        return response(request, status=envelope["status"], error=envelope["body"]["error"])

    from ..core.evolution_promote import (
        AlreadyDecided,
        CandidateNotFound,
        NotReviewable,
        PromotionNotReady,
        ReviewNotFound,
    )
    from ..core.evolution_run import errors

    for name in (
        "GenomeBaselineMissing",
        "BindingCheckFailed",
        "PreconditionFailed",
        "EvolutionFrozen",
        "CeilingExceeded",
        "BindingBusy",
        "IdempotencyKeyReused",
        "RunNotFound",
        "RunAlreadyEnded",
        "BudgetExhausted",
    ):
        app.add_exception_handler(getattr(errors, name), domain_error)
    # 09 评审面：404/409 的形状与 09 §2.1 的错误信封一致。
    for kind in (CandidateNotFound, ReviewNotFound, NotReviewable, AlreadyDecided, PromotionNotReady):
        app.add_exception_handler(kind, domain_error)
    for kind in (ValueError, KeyError, TypeError):
        app.add_exception_handler(kind, domain_error)


def build_control_router(runtime, *, backend_access=None) -> APIRouter:
    router = APIRouter(tags=["evolution"])

    async def grant(request, bot_id, write=False):
        owner = request.query_params.get("entity_id") or request.query_params.get("owner_id", "")
        if backend_access is not None:
            from .backend_access import HEADER, fingerprint

            return backend_access.verify(
                request.headers.get(HEADER, ""),
                bot_id=bot_id,
                owner_hint=owner,
                request_hash=fingerprint(
                    request.method, request.url.path, request.url.query, await request.body(), request.headers
                ),
            )
        return await runtime.platform.access.authorize(
            bot_id=bot_id, owner_hint=owner, credentials=request.headers, write=write
        )

    @router.get(BASE + "/bindings")
    async def bindings(request: Request, bot_id: str):
        access = await grant(request, bot_id)
        data = await runtime.api.get_bindings(access.bot)
        return response(request, data, headers={"ETag": f'"{data["etag"]}"'})

    @router.put(BASE + "/bindings")
    async def put_bindings(request: Request, bot_id: str, payload: BindingsBody):
        access = await grant(request, bot_id, True)
        tag = request.headers.get("if-match")
        if tag is None:
            return response(request, status=428)
        if len(tag) < 2 or not (tag.startswith('"') and tag.endswith('"')):
            return response(request, status=412)
        data = await runtime.api.put_bindings(
            access.bot, payload.bindings, expected_etag=tag[1:-1] or None, actor=access.actor
        )
        return response(request, data, headers={"ETag": f'"{data["etag"]}"'})

    @router.post(BASE + "/runs", status_code=202)
    async def submit(request: Request, bot_id: str, payload: RunBody):
        access = await grant(request, bot_id, True)
        key = request.headers.get("idempotency-key", "")
        if not key.strip() or len(key) > 200:
            return response(request, status=400)
        data = await runtime.api.submit_run(
            access.bot, {**payload.model_dump(exclude_none=True), "idempotency_key": key}, actor=access.actor
        )
        return response(request, data, status=202)

    @router.get(BASE + "/runs")
    async def runs(
        request: Request, bot_id: str, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)
    ):
        access = await grant(request, bot_id)
        return response(request, await runtime.api.list_runs(access.bot, page=page, page_size=page_size))

    @router.get(BASE + "/runs/{run_id}")
    async def run(request: Request, bot_id: str, run_id: str):
        access = await grant(request, bot_id)
        return response(request, await runtime.api.get_run(access.bot, run_id))

    @router.post(BASE + "/runs/{run_id}:cancel")
    async def cancel(request: Request, bot_id: str, run_id: str, payload: CancelBody):
        access = await grant(request, bot_id, True)
        return response(
            request,
            await runtime.api.cancel_run(access.bot, run_id, reason=payload.reason, actor=access.actor),
        )

    @router.get(BASE + "/runs/{run_id}/candidates")
    async def candidates(request: Request, bot_id: str, run_id: str):
        access = await grant(request, bot_id)
        return response(request, await runtime.api.run_candidates(access.bot, run_id))

    @router.get(BASE + "/runs/{run_id}/evidence")
    async def evidence(request: Request, bot_id: str, run_id: str, after: int = Query(0, ge=0)):
        access = await grant(request, bot_id)
        return response(request, await runtime.api.run_evidence(access.bot, run_id, after=after))

    @router.get(BASE + "/candidates/{candidate_id}")
    async def candidate_report(request: Request, bot_id: str, candidate_id: str):
        access = await grant(request, bot_id)
        return response(request, await runtime.api.candidate_report(access.bot, candidate_id))

    @router.get(BASE + "/review-queue")
    async def review_queue(
        request: Request,
        bot_id: str,
        status: str = Query("open"),
        min_tier: str | None = Query(None),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
    ):
        access = await grant(request, bot_id)
        return response(
            request,
            await runtime.api.review_queue(
                access.bot, status=status, min_tier=min_tier, page=page, page_size=page_size
            ),
        )

    @router.post(BASE + "/candidates/{candidate_id}:approve")
    async def approve(request: Request, bot_id: str, candidate_id: str, payload: ReviewBody):
        access = await grant(request, bot_id, True)
        key = request.headers.get("idempotency-key", "")
        if len(key) > 200:
            return response(request, status=400)
        return response(
            request,
            await runtime.api.approve_candidate(
                access.bot,
                candidate_id,
                reason=payload.reason,
                actor=access.actor,
                idempotency_key=key or None,
            ),
        )

    @router.post(BASE + "/candidates/{candidate_id}:reject")
    async def reject(request: Request, bot_id: str, candidate_id: str, payload: ReviewBody):
        access = await grant(request, bot_id, True)
        key = request.headers.get("idempotency-key", "")
        if len(key) > 200:
            return response(request, status=400)
        return response(
            request,
            await runtime.api.reject_candidate(
                access.bot,
                candidate_id,
                reason=payload.reason,
                actor=access.actor,
                idempotency_key=key or None,
            ),
        )

    @router.get(BASE + "/strategies")
    @router.get("/openapi/v1/evolution/strategies", include_in_schema=False)
    async def strategies(request: Request, bot_id: str):
        # Catalog access uses the same authoritative Bot permission check.
        await grant(request, bot_id)
        records = await runtime.platform.registry.list()
        return response(
            request,
            {
                "strategies": [
                    {
                        "id": r.registration.id,
                        "version": r.registration.version,
                        "enabled": r.enabled,
                        "conformance": r.conformance.status.value,
                        "needs": r.registration.needs,
                        "params_schema": r.registration.params_schema,
                    }
                    for r in records
                ]
            },
        )

    return router
