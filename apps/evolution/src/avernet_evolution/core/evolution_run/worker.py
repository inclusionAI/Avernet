"""容器内 worker（06 §7、§10）：认领作业、在本地沙箱里执行策略、回写结果。

它与 `InProcessHost` 的区别只有边界：这里的一切平台交互都走作业协议
（`WorkerTransport`），策略拿不到容器外的任何东西。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from avernet_evolution.core.evolution_run.errors import CandidateRejected
from avernet_evolution.core.evolution_strategy import (
    Budget,
    Candidate,
    Completion,
    Operation,
    OperationStatus,
    StaticRevision,
    Verdict,
    VerdictStatus,
    build_context,
)

__all__ = ["RemoteCandidates", "Worker"]


@dataclass
class RemoteCandidates:
    """提交与判定都走作业协议；worker 本地不持有候选的任何其他状态。"""

    transport: Any
    job_id: str
    fencing_token: str

    async def submit(self, candidate: Candidate) -> str:
        result = await self.transport.submit(self.job_id, self.fencing_token, candidate.patch.to_json())
        return result["candidate_id"]

    async def verdict(self, candidate_id: str) -> Verdict:
        payload = await self.transport.verdict(self.job_id, self.fencing_token, candidate_id)
        return Verdict(
            candidate=payload["candidate"],
            status=VerdictStatus(payload["status"]),
            revision=payload.get("revision"),
            aggregates=payload.get("aggregates") or {},
            reasons=tuple(payload.get("reasons") or ()),
        )


@dataclass
class RemoteModels:
    """单次模型调用走作业协议；成本由平台按 budgetservice 计入运行预算。"""

    transport: Any
    job_id: str
    fencing_token: str

    async def complete(
        self, *, messages: list[Any], model: str | None = None, max_tokens: int = 4096
    ) -> Completion:
        payload = await self.transport.models_complete(
            self.job_id,
            self.fencing_token,
            messages=messages,
            model=model,
            max_tokens=max_tokens,
        )
        return Completion(
            text=payload.get("text", ""),
            model=payload.get("model"),
            cost_usd=float(payload.get("cost_usd", 0.0)),
        )


@dataclass
class RemoteExperience:
    """读取 bot 会话与反馈，全部走作业协议回连平台。"""

    transport: Any
    job_id: str
    fencing_token: str

    async def sessions(self, *, days: int, limit: int = 500, revision: str | None = None) -> list[Any]:
        return await self.transport.experience_sessions(
            self.job_id,
            self.fencing_token,
            days=days,
            limit=limit,
            revision=revision,
        )

    async def feedback(self, *, days: int, kinds: list[str] | None = None, limit: int = 500) -> list[Any]:
        return await self.transport.experience_feedback(
            self.job_id,
            self.fencing_token,
            days=days,
            kinds=kinds,
            limit=limit,
        )


@dataclass
class RemoteAgents:
    """启动智能体会话，返回平台分配的操作 id。"""

    transport: Any
    job_id: str
    fencing_token: str

    async def start(
        self,
        definition: str,
        *,
        workspace: Any,
        prompt: str,
        idempotency_key: str,
        timeout_s: int = 1800,
    ) -> str:
        return await self.transport.agents_start(
            self.job_id,
            self.fencing_token,
            definition=definition,
            workspace_id=getattr(workspace, "id", workspace),
            prompt=prompt,
            idempotency_key=idempotency_key,
            timeout_s=timeout_s,
        )


@dataclass
class RemoteOperations:
    """长时操作状态按操作 id 查询，全部走作业协议。"""

    transport: Any
    job_id: str
    fencing_token: str

    async def get(self, op_id: str) -> Operation:
        payload = await self.transport.operation_get(self.job_id, self.fencing_token, op_id)
        return Operation(
            id=payload["id"],
            kind=payload["kind"],
            status=OperationStatus(payload["status"]),
            result=payload.get("result"),
            error=payload.get("error"),
            cost_usd=float(payload.get("cost_usd", 0.0)),
        )

    async def cancel(self, op_id: str) -> None:
        await self.transport.operation_cancel(self.job_id, self.fencing_token, op_id)

    async def wait(self, op_id: str, *, poll_s: float = 5) -> Operation:
        import asyncio

        while True:
            op = await self.get(op_id)
            if op.status in (
                OperationStatus.SUCCEEDED,
                OperationStatus.FAILED,
                OperationStatus.CANCELLED,
            ):
                return op
            await asyncio.sleep(poll_s)


@dataclass
class RemoteLog:
    """结构化日志行发往平台作业协议。"""

    transport: Any
    job_id: str
    fencing_token: str

    async def info(self, message: str, **fields: object) -> None:
        await self.transport.log(
            self.job_id,
            self.fencing_token,
            [{"level": "info", "msg": message, "fields": fields}],
        )

    async def warn(self, message: str, **fields: object) -> None:
        await self.transport.log(
            self.job_id,
            self.fencing_token,
            [{"level": "warn", "msg": message, "fields": fields}],
        )


@dataclass
class RemoteArtifacts:
    """运行制品上传到平台作业协议。"""

    transport: Any
    job_id: str
    fencing_token: str

    async def put(self, name: str, content: bytes) -> str:
        return await self.transport.artifacts_put(self.job_id, self.fencing_token, name, content)


@dataclass
class Worker:
    """一个容器内的策略执行器；一次认领一个作业。"""

    transport: Any
    strategies: dict[str, Callable[[], Any]]
    worker_id: str
    heartbeat_s: float = 20.0
    last_error: str | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    async def run_once(self, token: str) -> bool:
        grant = await self.transport.claim(self.worker_id, token, list(self.strategies))
        if grant is None:
            return False
        await self._execute(grant)
        return True

    async def run_pending(self, token: str, *, max_jobs: int = 100) -> int:
        count = 0
        while count < max_jobs and await self.run_once(token):
            count += 1
        return count

    async def _execute(self, grant: dict[str, Any]) -> None:
        job_id, fencing = grant["job_id"], grant["fencing_token"]
        try:
            strategy = self.strategies[grant["strategy_ref"]]
            instance = strategy if not isinstance(strategy, type) and hasattr(strategy, "run") else strategy()
            files = await self.transport.files(job_id, fencing)
            parent = StaticRevision(
                id=grant["parent_revision"],
                seq=grant["parent_revision"],
                files={path: data.decode("utf-8", "replace") for path, data in files.items()},
            )
            budget = grant.get("budget") or {}
            ctx = build_context(
                parent=parent,
                run_id=grant["run_id"],
                params=grant.get("params") or {},
                attempt=grant.get("attempt", 1),
                granted=grant.get("granted") or [],
                budget=Budget(
                    max_usd=float(budget.get("max_usd") or 0.0),
                    max_wall_clock_s=int(budget.get("max_wall_clock_s") or 0),
                ),
                models=RemoteModels(self.transport, job_id, fencing),
                experience=RemoteExperience(self.transport, job_id, fencing),
                agents=RemoteAgents(self.transport, job_id, fencing),
                operations=RemoteOperations(self.transport, job_id, fencing),
                log=RemoteLog(self.transport, job_id, fencing),
                artifacts=RemoteArtifacts(self.transport, job_id, fencing),
                candidates=RemoteCandidates(self.transport, job_id, fencing),
            )
            beat = asyncio.create_task(self._heartbeat(job_id, fencing))
            try:
                await ctx.log.info("strategy.started")
                summary = await instance.run(ctx)
                await ctx.log.info("strategy.finished")
                await self.transport.complete(job_id, fencing, _summary_dict(summary))
            finally:
                self._stop.set()
                beat.cancel()
                try:
                    await beat
                except asyncio.CancelledError:
                    pass
                self._stop = asyncio.Event()
        except asyncio.CancelledError:
            raise
        except CandidateRejected as exc:
            # 平台已有同内容的修订版：这不是策略失败，重试也不会改变结果
            # （同一父代 + 同一补丁 → 同一 revision id）。让运行以 completed
            # 收敛，并把「该做什么」写进 summary，而不是烧完 3 次尝试。
            await self.transport.complete(
                job_id,
                fencing,
                {
                    "summary": f"no new candidate: {exc.code.value}",
                    "metrics": {"already_recorded": 1.0},
                },
            )
        except Exception as exc:  # noqa: BLE001 - 策略异常是可重试失败
            self.last_error = f"{type(exc).__name__}: {exc}"
            try:
                await self.transport.fail(job_id, fencing, str(exc))
            except Exception as inner:  # noqa: BLE001 - 回写失败只记录
                self.last_error = f"{self.last_error}; fail-write failed: {inner}"

    async def _heartbeat(self, job_id: str, fencing_token: str) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self.heartbeat_s)
            if self._stop.is_set():
                return
            try:
                state = await self.transport.heartbeat(job_id, fencing_token)
            except Exception:  # noqa: BLE001 - 失去租约就停止续期
                return
            if state.get("cancelled"):
                return


def _summary_dict(summary: Any) -> dict[str, Any]:
    if summary is None:
        return {}
    return {
        "summary": getattr(summary, "summary", ""),
        "metrics": dict(getattr(summary, "metrics", {}) or {}),
    }
