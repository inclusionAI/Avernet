"""JobService（06-evolution-run.zh-CN §7、§13）。"""

from __future__ import annotations

import logging
import secrets
from dataclasses import replace
from datetime import datetime
from typing import Any

from .enums import RunStatus
from .errors import JobNotFound
from .records import ClaimedJob, LeaseState
from .store import EvolutionStore, utcnow

__all__ = ["JobService"]


class JobService:
    def __init__(self, store: EvolutionStore, *, lease_seconds: int = 60, max_attempts: int = 3) -> None:
        self.store = store
        self._logger = logging.getLogger(__name__)
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts

    async def claim(self, worker_id: str, strategy_ids: list[str]) -> ClaimedJob | None:
        """租一个 queued 作业；认领即把运行转入 running（首次开始记时间）。"""

        claimed = self.store.claim_job(
            worker_id,
            list(strategy_ids),
            lease_seconds=self.lease_seconds,
            fencing_token="ft_" + secrets.token_hex(6),
        )
        if claimed is None:
            return None
        job, run = claimed
        if run.status is RunStatus.QUEUED:
            running = replace(run, status=RunStatus.RUNNING, started_at=run.started_at or utcnow())
            self.store.update_run(running)
            run = running
        self._event("job.claimed", job.id)
        return ClaimedJob(job=job, run=run)

    async def heartbeat(self, job_id: str, fencing_token: str | None) -> LeaseState:
        result = self.store.heartbeat(job_id, fencing_token, self.lease_seconds)
        if result is None:
            raise JobNotFound(job_id)
        expires, cancelled = result
        self._event("job.heartbeat", job_id)
        return LeaseState(lease_expires_at=expires, cancelled=cancelled)

    async def complete(self, job_id: str, fencing_token: str | None, summary: dict[str, Any]) -> None:
        self.store.complete_job(job_id, fencing_token, summary)
        self.store.cancel_unfinished_operations(
            self.store.conn.execute("SELECT run_id FROM evolution_job WHERE job_id=?", (job_id,)).fetchone()[
                0
            ]
        )

        self._event("job.completed", job_id)

    async def fail(self, job_id: str, fencing_token: str | None, *, reason: str, retryable: bool) -> None:
        self.store.fail_job(job_id, fencing_token, reason=reason, retryable=retryable)
        # 事件流的 details 只放白名单元数据，所以失败原因走 `run.summary`（store 侧）
        # 和进程日志两条路；`kubectl logs` 只看到 exit code 是没法排障的。
        if self._logger is not None:
            self._logger.warning(
                "evolution.job.failed job_id=%s retryable=%s reason=%s",
                job_id,
                retryable,
                " ".join((reason or "").split())[:500],
            )

    async def requeue(self, job_id: str) -> str | None:
        """宿主兜底的平台侧重派发（§7.2），不校验策略侧持有的 fencing token。"""

        run_id = self.store.requeue_job(job_id)
        if run_id is not None:
            attempt = self.store.get_run(run_id).attempt
            self._event("job.requeued", job_id, {"next_job_id": f"{run_id}_{attempt}"})
        return run_id

    async def expire_leases(self, now: datetime | None = None) -> list[str]:
        return self.store.expire_leases(now or utcnow())

    def _event(self, kind: str, job_id: str, details=None) -> None:
        row = self.store.conn.execute("SELECT * FROM evolution_job WHERE job_id=?", (job_id,)).fetchone()
        self.store.events.append(
            kind,
            run_id=row["run_id"],
            job_id=job_id,
            attempt=row["attempt"],
            worker_id=row["worker_id"],
            details=details,
        )
