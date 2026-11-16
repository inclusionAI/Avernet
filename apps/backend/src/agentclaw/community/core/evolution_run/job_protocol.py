"""作业协议（06 §7、§9.1）：容器内 worker 与平台之间的全部交互。

传输无关：HTTP 适配器只需把 `JobChannel` 的方法映射成路由即可。凭据是
按 worker 签发的令牌（claim 时验证）与认领时返回的 fencing token（其余调用）。
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any, Protocol

from .budget import BudgetUsage
from .enums import ChargeSource

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

    async def claim(
        self, worker_id: str, token: str, strategy_refs: list[str]
    ) -> dict[str, Any] | None: ...
    async def heartbeat(self, job_id: str, fencing_token: str) -> dict[str, Any]: ...
    async def files(self, job_id: str, fencing_token: str) -> dict[str, bytes]: ...
    async def submit(
        self, job_id: str, fencing_token: str, patch: dict[str, Any]
    ) -> dict[str, Any]: ...
    async def verdict(
        self, job_id: str, fencing_token: str, candidate_id: str
    ) -> dict[str, Any]: ...
    async def complete(
        self, job_id: str, fencing_token: str, summary: dict[str, Any]
    ) -> None: ...
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
    async def operation_get(
        self, job_id: str, fencing_token: str, operation_id: str
    ) -> dict[str, Any]: ...
    async def operation_cancel(
        self, job_id: str, fencing_token: str, operation_id: str
    ) -> dict[str, Any]: ...

    # -- log / artifacts -----------------------------------------------
    async def log(
        self, job_id: str, fencing_token: str, lines: list[dict[str, Any]]
    ) -> None: ...
    async def artifacts_put(
        self, job_id: str, fencing_token: str, name: str, content: bytes
    ) -> str: ...


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
    experience: Any = None
    agents: Any = None
    evaluation: Any = None
    operations: Any = None
    budget: Any = None

    async def claim(
        self, worker_id: str, token: str, strategy_refs: list[str]
    ) -> dict[str, Any] | None:
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
            "params": dict(run.params),
            "granted": list(run.granted),
            "budget": dict(run.budget_max),
        }

    async def heartbeat(self, job_id: str, fencing_token: str) -> dict[str, Any]:
        state = await self.jobs.heartbeat(job_id, fencing_token)
        return {
            "lease_expires_at": state.lease_expires_at.isoformat(),
            "cancelled": state.cancelled,
        }

    async def files(self, job_id: str, fencing_token: str) -> dict[str, bytes]:
        run = self._run_for(job_id, fencing_token)
        revision = await self.genome.resolve_revision(run.bot, run.parent_revision)
        return await self.genome.snapshot_files(revision, bot=run.bot)

    async def submit(
        self, job_id: str, fencing_token: str, patch: dict[str, Any]
    ) -> dict[str, Any]:
        from agentclaw.community.core.bot_genome.patch_models import GenomePatch
        from agentclaw.community.core.evolution_strategy import Candidate

        from .submission import submit_candidate

        run = self._run_for(job_id, fencing_token)
        parsed = GenomePatch.model_validate(patch)
        candidate = Candidate(
            patch=parsed, rationale=parsed.rationale, evidence=tuple(parsed.evidence)
        )
        candidate_id = await submit_candidate(
            store=self.store,
            genome=self.genome,
            run=run,
            candidate=candidate,
            promotion=self.promotion,
            ledger=self.ledger,
            verifier=self.verifier,
        )
        found = self.store.find_candidate(candidate_id) or {}
        return {"candidate_id": candidate_id, "revision": found.get("revision")}

    async def verdict(
        self, job_id: str, fencing_token: str, candidate_id: str
    ) -> dict[str, Any]:
        run = self._run_for(job_id, fencing_token)
        view = await self.verifier.strategy_view(candidate_id, run.id)
        return {
            "candidate": view.candidate,
            "status": view.status.value,
            "revision": view.revision,
            "aggregates": {
                name: vars(aggregate) for name, aggregate in view.aggregates.items()
            },
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
        self._run_for(job_id, fencing_token)
        provider = self.experience
        if provider is None:
            raise NotImplementedError("no experience provider configured")
        return await provider.sessions(days=days, limit=limit, revision=revision)

    async def experience_feedback(
        self,
        job_id: str,
        fencing_token: str,
        *,
        days: int,
        kinds: list[str] | None = None,
        limit: int = 500,
    ) -> list[Any]:
        self._run_for(job_id, fencing_token)
        provider = self.experience
        if provider is None:
            raise NotImplementedError("no experience provider configured")
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
        provider = self.models
        if provider is None:
            raise NotImplementedError("no models provider configured")
        completion = await provider.complete(
            messages=messages, model=model, max_tokens=max_tokens
        )
        usage = BudgetUsage.of(
            usd=getattr(getattr(completion, "cost", None), "usd", 0) or 0,
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
    async def operation_get(
        self, job_id: str, fencing_token: str, operation_id: str
    ) -> dict[str, Any]:
        run = self._run_for(job_id, fencing_token)
        record = await self.operations.get(run.id, operation_id)
        return {
            "id": record.id,
            "kind": record.kind.value if hasattr(record.kind, "value") else record.kind,
            "status": record.status.value
            if hasattr(record.status, "value")
            else record.status,
            "result": record.result,
            "error": record.error,
            "cost_usd": float(record.cost.usd),
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "finished_at": record.finished_at.isoformat()
            if record.finished_at
            else None,
        }

    async def operation_cancel(
        self, job_id: str, fencing_token: str, operation_id: str
    ) -> dict[str, Any]:
        run = self._run_for(job_id, fencing_token)
        record = await self.operations.cancel(run.id, operation_id)
        return {
            "id": record.id,
            "status": record.status.value
            if hasattr(record.status, "value")
            else record.status,
        }

    # -- log / artifacts -----------------------------------------------
    async def log(
        self, job_id: str, fencing_token: str, lines: list[dict[str, Any]]
    ) -> None:
        self._run_for(job_id, fencing_token)
        if not hasattr(self, "_log_entries"):
            self._log_entries: list[list[dict[str, Any]]] = []
        self._log_entries.append(lines)

    async def artifacts_put(
        self, job_id: str, fencing_token: str, name: str, content: bytes
    ) -> str:
        self._run_for(job_id, fencing_token)
        if not hasattr(self, "_artifacts"):
            self._artifacts: dict[str, bytes] = {}
        import secrets

        artifact_id = "art_" + secrets.token_hex(4)
        self._artifacts[artifact_id] = content
        return artifact_id

    async def complete(
        self, job_id: str, fencing_token: str, summary: dict[str, Any]
    ) -> None:
        await self.jobs.complete(job_id, fencing_token, summary)

    async def fail(
        self, job_id: str, fencing_token: str, reason: str, retryable: bool = True
    ) -> None:
        await self.jobs.fail(job_id, fencing_token, reason=reason, retryable=retryable)

    def _run_for(self, job_id: str, fencing_token: str) -> Any:
        row = self.store.conn.execute(
            "SELECT run_id, fencing_token FROM evolution_job WHERE job_id=? AND open=1",
            (job_id,),
        ).fetchone()
        if row is None or row["fencing_token"] != fencing_token:
            raise JobUnauthorized(job_id)
        run = self.store.get_run(row["run_id"])
        if run is None:
            raise JobUnauthorized(job_id)
        return run
