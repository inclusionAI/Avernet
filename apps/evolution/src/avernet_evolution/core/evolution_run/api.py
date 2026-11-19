"""公共 API 的传输无关门面（09-evolution-api.zh-CN §2、§3；06 §14）。

HTTP 交付适配器是这里的薄包装：每个方法对应一个端点，返回可 JSON 化的字典，
抛出的类型错误由调用方映射为 §2.1 的错误信封。
"""

from __future__ import annotations

from typing import Any

from avernet_genome.models import BotRef

from .bindings import Binding
from .bindings_service import BindingsService
from .budget import Budget
from .enums import RunStatus, TriggerKind
from .records import TriggerRecord
from .run_service import RunService

__all__ = ["EvolutionApi", "error_envelope"]

_ERROR_CODES = {
    "BindingCheckFailed": ("422", "binding_check_failed"),
    "PreconditionFailed": ("412", "precondition_failed"),
    "EvolutionFrozen": ("409", "evolution_frozen"),
    "CeilingExceeded": ("409", "ceiling_exceeded"),
    "BindingBusy": ("409", "binding_busy"),
    "IdempotencyKeyReused": ("409", "idempotency_key_reused"),
    "RunNotFound": ("404", "run_not_found"),
    "RunAlreadyEnded": ("409", "run_already_ended"),
    "BudgetExhausted": ("409", "budget_exhausted"),
    "StaleFencingToken": ("409", "stale_fencing_token"),
}


def error_envelope(error: Exception) -> dict[str, Any]:
    """09 §2.1 的错误信封：`{error: {code, message, details}}`。"""

    name = type(error).__name__
    status, code = _ERROR_CODES.get(name, ("400", "invalid_request"))
    details = getattr(error, "problems", None)
    return {
        "status": int(status),
        "body": {"error": {"code": code, "message": str(error), "details": details or []}},
    }


class EvolutionApi:
    def __init__(
        self,
        *,
        bindings: BindingsService,
        runs: RunService,
        registry: Any = None,
        ledger: Any = None,
    ) -> None:
        self.bindings = bindings
        self.runs = runs
        self.registry = registry
        self.ledger = ledger

    # -- §14.1 bindings -------------------------------------------------
    async def get_bindings(self, bot: BotRef) -> dict[str, Any]:
        document = await self.bindings.get_bindings(bot)
        return {
            "bindings": [_binding_json(b) for b in document.bindings],
            "etag": document.etag,
        }

    async def put_bindings(
        self,
        bot: BotRef,
        payload: list[dict[str, Any]],
        *,
        expected_etag: str | None,
        actor: str,
    ) -> dict[str, Any]:
        bindings = [self._parse_binding(entry) for entry in payload]
        document = await self.bindings.put_bindings(bot, bindings, expected_etag=expected_etag, actor=actor)
        return {
            "bindings": [_binding_json(b) for b in document.bindings],
            "etag": document.etag,
        }

    # -- §14.1 runs -----------------------------------------------------
    async def submit_run(self, bot: BotRef, payload: dict[str, Any], *, actor: str) -> dict[str, Any]:
        trigger = payload.get("trigger") or {"manual": True}
        record = (
            TriggerRecord(kind=TriggerKind.MANUAL, requested_by=actor)
            if "manual" in trigger
            else TriggerRecord(
                kind=TriggerKind(trigger.get("kind", "manual")),
                fire_time=trigger.get("fire_time"),
                event_id=trigger.get("event_id"),
                requested_by=actor,
            )
        )
        budget = payload.get("budget")
        run_id = await self.runs.submit(
            bot,
            payload["binding_id"],
            idempotency_key=payload["idempotency_key"],
            trigger=record,
            params=payload.get("params"),
            budget=_budget_from(budget) if budget else None,
        )
        return {"run": run_id}

    async def list_runs(
        self,
        bot: BotRef,
        *,
        status: list[str] | None = None,
        binding_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        statuses = [RunStatus(s) for s in status] if status else None
        result = await self.runs.list(
            bot, status=statuses, binding_id=binding_id, page=page, page_size=page_size
        )
        return {
            "runs": [_run_json(r) for r in result.items],
            "total": result.total,
            "page": result.page,
            "page_size": result.page_size,
        }

    async def get_run(self, bot: BotRef, run_id: str) -> dict[str, Any]:
        return {"run": _run_json(await self.runs.get(bot, run_id))}

    async def cancel_run(self, bot: BotRef, run_id: str, *, reason: str, actor: str) -> dict[str, Any]:
        return {"run": _run_json(await self.runs.cancel(bot, run_id, reason=reason, actor=actor))}

    async def run_candidates(self, bot: BotRef, run_id: str) -> dict[str, Any]:
        rows = await self.runs.candidates(bot, run_id)
        return {
            "candidates": [
                {
                    "candidate_id": r.candidate_id,
                    "revision": r.revision,
                    "verdict": r.verdict,
                }
                for r in rows
            ]
        }

    async def run_evidence(self, bot: BotRef, run_id: str, *, after: int = 0) -> dict[str, Any]:
        """Metadata-only evidence, after authorization and Run ownership checks."""
        await self.runs.get(bot, run_id)
        store = self.runs.store
        jobs = store.conn.execute(
            "SELECT job_id, attempt, worker_id, lease_expires_at, open FROM evolution_job "
            "WHERE run_id=? ORDER BY attempt",
            (run_id,),
        ).fetchall()
        events = store.events.for_run(run_id, after=after)
        return {
            "jobs": [dict(row) for row in jobs],
            "events": events,
            "next_cursor": events[-1]["id"] if events else after,
        }

    def _parse_binding(self, entry: dict[str, Any]) -> Binding:
        from .bindings_service import _binding_from_document

        return _binding_from_document(entry)


def _binding_json(binding: Binding) -> dict[str, Any]:
    from .bindings_service import _binding_document

    return _binding_document(binding)


def _budget_from(payload: dict[str, Any]) -> Budget:
    return Budget(
        max_usd=payload["max_usd"],
        max_wall_clock_s=payload["max_wall_clock_s"],
        max_rollouts=payload.get("max_rollouts"),
        max_tokens=payload.get("max_tokens"),
    )


def _run_json(run) -> dict[str, Any]:
    return {
        "id": run.id,
        "binding_id": run.binding_id,
        "strategy": run.strategy_ref,
        "status": run.status.value,
        "attempt": run.attempt,
        "parent_revision": run.parent_revision,
        "candidate_count": run.candidate_count,
        "budget_used": run.budget_used.as_dict(),
        "created_at": run.created_at.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "ended_at": run.ended_at.isoformat() if run.ended_at else None,
        "end_reason": run.end_reason.value if run.end_reason else None,
        "summary": run.summary,
    }
