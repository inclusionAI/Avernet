"""公共 API 的传输无关门面（09-evolution-api.zh-CN §2、§3；06 §14）。

HTTP 交付适配器是这里的薄包装：每个方法对应一个端点，返回可 JSON 化的字典，
抛出的类型错误由调用方映射为 §2.1 的错误信封。
"""

from __future__ import annotations

from typing import Any

from avernet_genome.models import BotRef

from avernet_evolution.core.evolution_promote import (
    ReviewNotFound,
    ReviewStatus,
    RiskTier,
)
from avernet_evolution.core.evolution_promote.report import (
    cost_summary,
    patch_summary,
    report_flags,
    review_json,
    split_summaries,
)
from avernet_evolution.core.evolution_promote.service import CandidateNotFound

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
    "GenomeBaselineMissing": ("409", "no_genome_baseline"),
    "RunAlreadyEnded": ("409", "run_already_ended"),
    "BudgetExhausted": ("409", "budget_exhausted"),
    "StaleFencingToken": ("409", "stale_fencing_token"),
    "CandidateNotFound": ("404", "not_found"),
    "ReviewNotFound": ("404", "not_found"),
    "NotReviewable": ("409", "not_reviewable"),
    "AlreadyDecided": ("409", "already_decided"),
    "PromotionNotReady": ("409", "promotion_not_ready"),
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
        verification: Any = None,
        promotion: Any = None,
        genome: Any = None,
    ) -> None:
        self.bindings = bindings
        self.runs = runs
        self.registry = registry
        self.ledger = ledger
        # 07/08 是可选端口：没有它们时 09 的评审面返回空，而不是假装有数据。
        self.verification = verification
        self.promotion = promotion
        self.genome = genome

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

    # -- 09 §10.1 候选报告与评审队列 -------------------------------------
    async def candidate_report(self, bot: BotRef, candidate_id: str) -> dict[str, Any]:
        """候选报告：diff 指针、验证摘要、门禁决定与评审状态。"""

        candidate_row, run_row = self._locate(bot, candidate_id)
        verdict = None
        if self.verification is not None:
            try:
                verdict = await self.verification.get_verdict(candidate_id, run_row["verification_profile"])
            except KeyError:
                verdict = None
        decision = self.promotion.store.get(candidate_id) if self.promotion is not None else None
        review = self.promotion.store.get_review(candidate_id) if self.promotion is not None else None
        entry = self._ledger_row(bot, run_row["run_id"], candidate_id)
        revision = candidate_row["revision"]
        parent = run_row["parent_revision"]

        return {
            "candidate_id": candidate_id,
            "bot": {"owner_id": bot.owner_id, "bot_id": bot.bot_id},
            "revision_id": revision,
            "revision_seq": await self._seq(bot, revision),
            "parent_revision_id": parent,
            "parent_revision_seq": await self._seq(bot, parent),
            "run_id": run_row["run_id"],
            "strategy": run_row["strategy"],
            "strategy_version": run_row["strategy_version"],
            "patch_summary": patch_summary(_json_or_none(entry["patch"]) if entry else None),
            "diff_path": f"/bots/{bot.bot_id}/genome/revisions/{revision}/diff?against={parent}",
            "rationale": (_json_or_none(entry["patch"]) or {}).get("rationale", "") if entry else "",
            "evidence": (_json_or_none(entry["evidence"]) or []) if entry else [],
            "verdict": verdict.status.value if verdict is not None else "pending",
            "splits": split_summaries(verdict) if verdict is not None else [],
            "cost": cost_summary(verdict) if verdict is not None else {},
            "flags": report_flags(verdict) if verdict is not None else [],
            # 判定仍为 pending 时报告照常返回，`gate` 与 `review` 为 null。
            "gate": (
                None
                if decision is None
                else {
                    "id": review.gate_decision_id if review is not None else None,
                    "outcome": decision.outcome.value,
                    "risk": {"tier": decision.risk_tier.value, "raised_by": []},
                    "verdict": decision.verdict_status,
                    "checks": [
                        {"name": check.name, "status": check.status.value, "detail": check.detail}
                        for check in decision.checks
                    ],
                    "decided_at": decision.decided_at.isoformat(),
                }
            ),
            "review": review_json(review),
        }

    async def review_queue(
        self,
        bot: BotRef,
        *,
        status: str = "open",
        min_tier: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """评审队列：默认只看 `open`；`status=all` 返回全部状态。"""

        if status not in {"open", "approved", "rejected", "superseded", "all"}:
            raise ValueError(f"unknown review status {status!r}")
        if self.promotion is None:
            return {"total": 0, "items": []}
        wanted = None if status == "all" else ReviewStatus(status)
        total, items = self.promotion.store.list_reviews(
            owner_id=bot.owner_id,
            bot_id=bot.bot_id,
            status=wanted,
            min_tier=RiskTier(min_tier) if min_tier else None,
            page=page,
            page_size=page_size,
        )
        return {
            "total": total,
            "items": [self._review_entry(bot, item) for item in items],
        }

    async def approve_candidate(
        self,
        bot: BotRef,
        candidate_id: str,
        *,
        reason: str,
        actor: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """批准一个开放评审条目。决定立即记录；apply 由晋升侧另行交付。"""

        if self.promotion is None:
            raise ReviewNotFound(candidate_id)
        key = self._idempotency_key(bot, candidate_id, "approve", idempotency_key)
        cached = self.promotion.store.replay(key)
        if cached is not None:
            return cached
        result = await self.promotion.approve(bot, candidate_id, reason=reason, actor=actor)
        self._review_event(result.review, actor=actor, outcome="approved")
        body = {
            "review": review_json(result.review),
            "promotion": None,
            "promotion_note": result.promotion_note,
        }
        self.promotion.store.remember(key, body)
        return body

    async def reject_candidate(
        self,
        bot: BotRef,
        candidate_id: str,
        *,
        reason: str,
        actor: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if self.promotion is None:
            raise ReviewNotFound(candidate_id)
        key = self._idempotency_key(bot, candidate_id, "reject", idempotency_key)
        cached = self.promotion.store.replay(key)
        if cached is not None:
            return cached
        result = await self.promotion.reject(bot, candidate_id, reason=reason, actor=actor)
        self._review_event(result.review, actor=actor, outcome="rejected")
        body = {"review": review_json(result.review)}
        self.promotion.store.remember(key, body)
        return body

    @staticmethod
    def _idempotency_key(bot: BotRef, candidate_id: str, action: str, supplied: str | None) -> str | None:
        if not supplied:
            return None
        return f"{bot.owner_id}:{bot.bot_id}:{candidate_id}:{action}:{supplied}"

    def _locate(self, bot: BotRef, candidate_id: str) -> tuple[Any, Any]:
        """按候选哈希定位候选行与 run 行，并把 bot 边界校验做完。"""

        rows = self.runs.store.conn.execute(
            "SELECT * FROM evolution_run_candidate WHERE candidate_id=?", (candidate_id,)
        ).fetchall()
        for candidate_row in rows:
            run_row = self.runs.store.conn.execute(
                "SELECT * FROM evolution_run WHERE run_id=?", (candidate_row["run_id"],)
            ).fetchone()
            if run_row is None:
                continue
            if run_row["owner_id"] != bot.owner_id:
                continue
            if run_row["bot_id"] not in {bot.bot_id, bot.entity_id}:
                continue
            return candidate_row, run_row
        raise CandidateNotFound(candidate_id)

    def _ledger_row(self, bot: BotRef, run_id: str, candidate_id: str) -> Any:
        if self.ledger is None:
            return None
        return self.ledger.conn.execute(
            "SELECT * FROM ledger_entries WHERE owner_id=? AND bot_id=? AND run_id=? AND candidate_id=?",
            (bot.owner_id, bot.bot_id, run_id, candidate_id),
        ).fetchone()

    def _review_entry(self, bot: BotRef, item: Any) -> dict[str, Any]:
        run_row = self.runs.store.conn.execute(
            "SELECT * FROM evolution_run WHERE run_id=?", (item.run_id,)
        ).fetchone()
        entry = self._ledger_row(bot, item.run_id, item.candidate_id)
        return {
            **review_json(item),
            "candidate_id": item.candidate_id,
            "bot": {"owner_id": item.owner_id, "bot_id": item.bot_id},
            "revision_id": item.revision_id,
            "revision_seq": None,
            "parent_revision_id": item.parent_revision_id,
            "run_id": item.run_id,
            "strategy": run_row["strategy"] if run_row is not None else "",
            "strategy_version": run_row["strategy_version"] if run_row is not None else "",
            "risk_tier": item.risk_tier.value,
            "gate_decision_id": item.gate_decision_id,
            "rationale": (_json_or_none(entry["patch"]) or {}).get("rationale", "") if entry else "",
            "evidence": (_json_or_none(entry["evidence"]) or []) if entry else [],
            "self_reported_metrics": {},
        }

    async def _seq(self, bot: BotRef, revision_id: str) -> int | None:
        """修订版序号只是展示字段；取不到就不显示，不让报告失败。"""

        if self.genome is None or not revision_id:
            return None
        try:
            revision = await self.genome.resolve_revision(bot, revision_id)
        except Exception:  # noqa: BLE001 - 展示字段的失败不外溢
            return None
        return getattr(getattr(revision, "revision", None), "seq", None)

    def _review_event(self, item: Any, *, actor: str, outcome: str) -> None:
        if item.run_id:
            self.runs.store.events.append(
                "review.decided",
                run_id=item.run_id,
                # 决定理由留在评审条目里；证据只记录"谁把状态改成了什么"。
                details={
                    "candidate_id": item.candidate_id,
                    "review_status": outcome,
                    "actor": actor,
                },
            )

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


def _json_or_none(value: Any) -> Any:
    import json

    if value is None or not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except ValueError:
        return None
