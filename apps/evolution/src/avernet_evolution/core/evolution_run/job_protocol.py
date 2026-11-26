"""作业协议（06 §7、§9.1）：容器内 worker 与平台之间的全部交互。

传输无关：HTTP 适配器只需把 `JobChannel` 的方法映射成路由即可。凭据是
按 worker 签发的令牌（claim 时验证）与认领时返回的 fencing token（其余调用）。
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any, Protocol

from avernet_evolution.core.evolution_strategy.errors import CapabilityNotGranted

from .budget import BudgetUsage
from .data_scope import data_scope, run_data_scope
from .enums import ChargeSource
from .errors import JobNotFound, StaleFencingToken
from .ports import ExperienceQueryFactory

__all__ = ["JobChannel", "JobTokenIssuer", "JobUnauthorized", "WorkerTransport"]


class JobUnauthorized(Exception):
    """令牌或 fencing token 与作业不符。"""


@dataclass(frozen=True)
class JobTokenIssuer:
    """HMAC 令牌：沙箱创建时注入，claim 时校验，策略拿不到别的凭证。"""

    secret: bytes

    def issue(self, worker_id: str) -> str:
        return hmac.new(self.secret, worker_id.encode(), hashlib.sha256).hexdigest()

    def verify(self, worker_id: str, token: str) -> bool:
        return hmac.compare_digest(self.issue(worker_id), token or "")


class WorkerTransport(Protocol):
    """worker 侧的最小协议；`JobChannel` 与测试替身都实现它。"""

    async def claim(self, worker_id: str, token: str, strategy_refs: list[str]) -> dict[str, Any] | None: ...
    async def heartbeat(self, job_id: str, fencing_token: str) -> dict[str, Any]: ...
    async def files(self, job_id: str, fencing_token: str) -> dict[str, bytes]: ...
    async def submit(self, job_id: str, fencing_token: str, patch: dict[str, Any]) -> dict[str, Any]: ...
    async def verdict(self, job_id: str, fencing_token: str, candidate_id: str) -> dict[str, Any]: ...
    async def complete(self, job_id: str, fencing_token: str, summary: dict[str, Any]) -> None: ...
    async def fail(self, job_id: str, fencing_token: str, reason: str) -> None: ...

    # -- experience ----------------------------------------------------
    async def experience_sessions(
        self,
        job_id: str,
        fencing_token: str,
        *,
        days: int,
        limit: int = 500,
        revision: str | None = None,
    ) -> list[Any]: ...
    async def experience_feedback(
        self,
        job_id: str,
        fencing_token: str,
        *,
        days: int,
        kinds: list[str] | None = None,
        limit: int = 500,
    ) -> list[Any]: ...

    # -- models --------------------------------------------------------
    async def models_complete(
        self,
        job_id: str,
        fencing_token: str,
        *,
        messages: list[Any],
        model: str | None = None,
        max_tokens: int = 4096,
    ) -> dict[str, Any]: ...

    # -- agents --------------------------------------------------------
    async def agents_start(
        self,
        job_id: str,
        fencing_token: str,
        *,
        definition: str,
        workspace_id: str,
        prompt: str,
        idempotency_key: str,
        timeout_s: int = 1800,
    ) -> str: ...

    # -- operations ----------------------------------------------------
    async def operation_get(self, job_id: str, fencing_token: str, operation_id: str) -> dict[str, Any]: ...
    async def operation_cancel(
        self, job_id: str, fencing_token: str, operation_id: str
    ) -> dict[str, Any]: ...

    # -- log / artifacts -----------------------------------------------
    async def log(self, job_id: str, fencing_token: str, lines: list[dict[str, Any]]) -> None: ...
    async def artifacts_put(self, job_id: str, fencing_token: str, name: str, content: bytes) -> str: ...


@dataclass
class JobChannel:
    """平台侧的作业通道：认领、心跳、文件、提交、判定、能力与完成/失败。"""

    store: Any
    jobs: Any
    genome: Any
    promotion: Any = None
    ledger: Any = None
    verifier: Any = None
    tokens: JobTokenIssuer | None = None
    models: Any = None
    experience: Any = None  # Explicitly Bot-bound provider (single-Bot compositions only).
    experience_factory: ExperienceQueryFactory | None = None
    agents: Any = None
    evaluation: Any = None
    operations: Any = None
    budget: Any = None

    async def claim(self, worker_id: str, token: str, strategy_refs: list[str]) -> dict[str, Any] | None:
        if self.tokens is None or not self.tokens.verify(worker_id, token):
            raise JobUnauthorized(worker_id)
        claimed = await self.jobs.claim(worker_id, list(strategy_refs))
        if claimed is None:
            return None
        job, run = claimed.job, claimed.run
        return {
            "job_id": job.id,
            "fencing_token": job.fencing_token,
            "run_id": run.id,
            "strategy_ref": run.strategy_ref,
            "attempt": job.attempt,
            "parent_revision": run.parent_revision,
            "allowed_genes": list(run.allowed_genes),
            "mutation_scope": run.mutation_scope.model_dump(mode="json")
            if run.mutation_scope is not None
            else None,
            "params": dict(run.params),
            "granted": list(run.granted),
            "budget": dict(run.budget_max),
        }

    async def heartbeat(self, job_id: str, fencing_token: str) -> dict[str, Any]:
        self._run_for(job_id, fencing_token, allow_terminal=True)
        state = await self.jobs.heartbeat(job_id, fencing_token)
        return {
            "lease_expires_at": state.lease_expires_at.isoformat(),
            "cancelled": state.cancelled,
        }

    async def files(self, job_id: str, fencing_token: str) -> dict[str, bytes]:
        run = self._run_for(job_id, fencing_token)
        with data_scope(run_data_scope(run)):
            revision = await self.genome.resolve_revision(run.bot, run.parent_revision)
            return await self.genome.snapshot_files(revision, bot=run.bot)

    async def submit(self, job_id: str, fencing_token: str, patch: dict[str, Any]) -> dict[str, Any]:
        from avernet_genome.patch_models import GenomePatch

        from avernet_evolution.core.evolution_strategy import Candidate

        from .submission import submit_candidate

        run = self._run_for(job_id, fencing_token)
        self._requires(run, "candidates@1")
        parsed = GenomePatch.model_validate(patch)
        candidate = Candidate(patch=parsed, rationale=parsed.rationale, evidence=tuple(parsed.evidence))
        candidate_id = await submit_candidate(
            store=self.store,
            genome=self.genome,
            run=run,
            candidate=candidate,
            promotion=self.promotion,
            ledger=self.ledger,
            verifier=self.verifier,
        )
        found = next(row for row in self.store.candidates(run.id) if row.candidate_id == candidate_id)
        return {"candidate_id": candidate_id, "revision": found.revision or None}

    async def verdict(self, job_id: str, fencing_token: str, candidate_id: str) -> dict[str, Any]:
        run = self._run_for(job_id, fencing_token)
        self._requires(run, "candidates@1")
        # Check ownership even when no 07 verifier is installed. Never turn an
        # unknown/foreign id into a plausible pending verdict.
        candidate = self.store.get_candidate(run.id, candidate_id)
        if candidate is None:
            raise JobUnauthorized(candidate_id)
        view = None
        if self.verifier is not None and candidate.revision:
            try:
                view = await self.verifier.strategy_view(candidate_id, run.id)
            except KeyError:
                pass  # a historical submission may have no Run-scoped verdict
        if view is None:
            return {
                "candidate": candidate_id,
                "status": "reject" if not candidate.revision and candidate.verdict == "reject" else "pending",
                "revision": candidate.revision or None,
                "aggregates": {},
                "reasons": [],
            }
        return {
            "candidate": view.candidate,
            "status": view.status.value,
            "revision": view.revision,
            "aggregates": {name: vars(aggregate) for name, aggregate in view.aggregates.items()},
            "reasons": [reason.value for reason in view.reasons],
        }

    # -- experience ----------------------------------------------------
    async def experience_sessions(
        self,
        job_id: str,
        fencing_token: str,
        *,
        days: int,
        limit: int = 500,
        revision: str | None = None,
    ) -> list[Any]:
        run = self._run_for(job_id, fencing_token)
        self._requires(run, "experience.sessions@1")
        with data_scope(run_data_scope(run)):
            provider = self._experience_for(run)
            episodes = await provider.sessions(days=days, limit=limit, revision=revision)
        self._event(
            "experience.query.completed",
            job_id,
            run.id,
            details={
                "count": len(episodes),
                "episode_ids": [
                    e["episode_id"]
                    for e in episodes[:100]
                    if isinstance(e, dict) and isinstance(e.get("episode_id"), str)
                ],
            },
        )
        return episodes

    async def experience_feedback(
        self,
        job_id: str,
        fencing_token: str,
        *,
        days: int,
        kinds: list[str] | None = None,
        limit: int = 500,
    ) -> list[Any]:
        run = self._run_for(job_id, fencing_token)
        self._requires(run, "experience.feedback@1")
        with data_scope(run_data_scope(run)):
            provider = self._experience_for(run)
            return await provider.feedback(days=days, kinds=kinds, limit=limit)

    # -- models --------------------------------------------------------
    async def models_complete(
        self,
        job_id: str,
        fencing_token: str,
        *,
        messages: list[Any],
        model: str | None = None,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        run = self._run_for(job_id, fencing_token)
        self._requires(run, "models@1")
        provider = self.models
        if provider is None:
            raise NotImplementedError("no models provider configured")
        with data_scope(run_data_scope(run)):
            completion = await provider.complete(messages=messages, model=model, max_tokens=max_tokens)
        usage = BudgetUsage.of(
            usd=completion.cost_usd,
            tokens=getattr(completion, "tokens", 0),
        )
        if self.budget is not None:
            await self.budget.charge(run.id, usage, ChargeSource.MODEL_CALL)
        return {
            "text": getattr(completion, "text", ""),
            "model": getattr(completion, "model", model),
            "cost_usd": float(usage.usd),
        }

    # -- agents --------------------------------------------------------
    async def agents_start(
        self,
        job_id: str,
        fencing_token: str,
        *,
        definition: str,
        workspace_id: str,
        prompt: str,
        idempotency_key: str,
        timeout_s: int = 1800,
    ) -> str:
        run = self._run_for(job_id, fencing_token)
        self._requires(run, "agents@1")
        provider = self.agents
        if provider is None:
            raise NotImplementedError("no agents provider configured")
        return await provider.start(
            definition=definition,
            workspace=workspace_id,
            prompt=prompt,
            idempotency_key=idempotency_key,
            timeout_s=timeout_s,
            run_id=run.id,
        )

    # -- operations ----------------------------------------------------
    async def operation_get(self, job_id: str, fencing_token: str, operation_id: str) -> dict[str, Any]:
        run = self._run_for(job_id, fencing_token)
        record = await self.operations.get(run.id, operation_id)
        return {
            "id": record.id,
            "kind": record.kind.value if hasattr(record.kind, "value") else record.kind,
            "status": record.status.value if hasattr(record.status, "value") else record.status,
            "result": record.result,
            "error": record.error,
            "cost_usd": float(record.cost.usd),
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "finished_at": record.finished_at.isoformat() if record.finished_at else None,
        }

    async def operation_cancel(self, job_id: str, fencing_token: str, operation_id: str) -> dict[str, Any]:
        run = self._run_for(job_id, fencing_token)
        record = await self.operations.cancel(run.id, operation_id)
        return {
            "id": record.id,
            "status": record.status.value if hasattr(record.status, "value") else record.status,
        }

    # -- log / artifacts -----------------------------------------------
    async def log(self, job_id: str, fencing_token: str, lines: list[dict[str, Any]]) -> None:
        run = self._run_for(job_id, fencing_token)
        # Worker text/fields are untrusted and may contain credentials or PII.
        # Persist only hashes/counts; lifecycle labels remain explicitly self-reported.
        if len(lines) > 100:
            raise ValueError("worker log batch too large")
        for line in lines:
            message = str(line.get("msg", ""))
            kind = (
                f"worker.{message}"
                if message in ("strategy.started", "strategy.finished")
                else "worker.log.received"
            )
            self._event(
                kind,
                job_id,
                run.id,
                authority="worker-reported",
                details={
                    "message_sha256": hashlib.sha256(message.encode()).hexdigest(),
                },
            )

    async def artifacts_put(self, job_id: str, fencing_token: str, name: str, content: bytes) -> str:
        run = self._run_for(job_id, fencing_token)
        return self.store.put_artifact(run.id, name, content)

    async def complete(self, job_id: str, fencing_token: str, summary: dict[str, Any]) -> None:
        self._run_for(job_id, fencing_token)
        await self.jobs.complete(job_id, fencing_token, summary)

    async def fail(self, job_id: str, fencing_token: str, reason: str, retryable: bool = True) -> None:
        self._run_for(job_id, fencing_token)
        await self.jobs.fail(job_id, fencing_token, reason=reason, retryable=retryable)

    def _event(self, kind, job_id, run_id, **kwargs):
        row = self.store.conn.execute(
            "SELECT attempt,worker_id FROM evolution_job WHERE job_id=?", (job_id,)
        ).fetchone()
        self.store.events.append(
            kind, run_id=run_id, job_id=job_id, attempt=row["attempt"], worker_id=row["worker_id"], **kwargs
        )

    @staticmethod
    def _requires(run: Any, capability: str) -> None:
        if capability not in run.granted:
            raise CapabilityNotGranted(capability)

    def _experience_for(self, run: Any) -> Any:
        if self.experience_factory is not None:
            return self.experience_factory.for_bot(run.bot)
        if self.experience is None:
            raise NotImplementedError("no experience provider configured")
        return self.experience

    def _run_for(self, job_id: str, fencing_token: str, *, allow_terminal: bool = False) -> Any:
        try:
            self.store._require_token(job_id, fencing_token)
        except (JobNotFound, StaleFencingToken) as exc:
            raise JobUnauthorized(job_id) from exc
        row = self.store.conn.execute(
            "SELECT run_id, fencing_token FROM evolution_job WHERE job_id=? AND open=1",
            (job_id,),
        ).fetchone()
        if not fencing_token or row is None or row["fencing_token"] != fencing_token:
            raise JobUnauthorized(job_id)
        run = self.store.get_run(row["run_id"])
        if run is None or (run.terminal and not allow_terminal):
            raise JobUnauthorized(job_id)
        return run
