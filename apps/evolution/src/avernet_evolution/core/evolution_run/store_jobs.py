"""Atomic job leases and attempt transitions for the SQLite repository."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from functools import wraps
from typing import Any

from .errors import JobNotFound, StaleFencingToken
from .records import Job, Run
from .store_codec import _iso, _json, _parse, utcnow

FAILURE_REASON_LIMIT = 2000


def _clip(reason: str | None, limit: int = FAILURE_REASON_LIMIT) -> str | None:
    """把失败原因压成单行并截断，避免把整段落栈塞进 `summary`。"""

    if reason is None:
        return None
    text = " ".join(str(reason).split())
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


def atomic(fn):
    """Serialize read/modify/write transitions across SQLite connections."""

    @wraps(fn)
    def wrapped(self, *args, **kwargs):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            result = fn(self, *args, **kwargs)
            self.conn.commit()
            return result
        except BaseException:
            self.conn.rollback()
            raise

    return wrapped


class JobStoreMixin:
    conn: sqlite3.Connection
    get_run: Any

    # -- jobs -----------------------------------------------------------
    @atomic
    def insert_job(self, job: Job) -> None:
        self.conn.execute(
            "INSERT INTO evolution_job (job_id, run_id, attempt, worker_id, lease_expires_at, "
            "fencing_token, open) VALUES (?, ?, ?, ?, ?, ?, 1)",
            (
                job.id,
                job.run_id,
                job.attempt,
                None,
                _iso(job.lease_expires_at),
                job.fencing_token,
            ),
        )

    @atomic
    def claim_job(
        self,
        worker_id: str,
        strategy_refs: list[str],
        *,
        lease_seconds: int,
        fencing_token: str,
    ) -> tuple[Job, Run] | None:
        if not strategy_refs:
            return None
        placeholders = ",".join("?" * len(strategy_refs))
        row = self.conn.execute(
            f"SELECT j.*, r.* FROM evolution_job j "
            f"JOIN evolution_run r ON r.run_id=j.run_id "
            f"WHERE j.open=1 AND j.worker_id IS NULL AND r.status='queued' AND r.strategy || '@' || r.strategy_version IN ({placeholders}) "
            f"ORDER BY r.created_at LIMIT 1",
            strategy_refs,
        ).fetchone()
        if row is None:
            return None
        expires = utcnow() + timedelta(seconds=lease_seconds)
        self.conn.execute(
            "UPDATE evolution_job SET worker_id=?, fencing_token=?, lease_expires_at=? WHERE job_id=?",
            (worker_id, fencing_token, _iso(expires), row["job_id"]),
        )
        self.conn.execute(
            "UPDATE evolution_run SET status='running', started_at=COALESCE(started_at, ?) WHERE run_id=?",
            (_iso(utcnow()), row["run_id"]),
        )
        job = Job(
            id=row["job_id"],
            run_id=row["run_id"],
            attempt=row["attempt"],
            worker_id=worker_id,
            lease_expires_at=expires,
            fencing_token=fencing_token,
            open=True,
        )
        run = self.get_run(row["run_id"])
        assert run is not None
        return job, run

    def has_queued_job(self, strategy_refs: list[str]) -> bool:
        """是否有该策略集合下处于 queued 的作业（容器宿主的启动闸门）。"""

        if not strategy_refs:
            return False
        placeholders = ",".join("?" * len(strategy_refs))
        row = self.conn.execute(
            f"SELECT 1 FROM evolution_job j JOIN evolution_run r ON r.run_id=j.run_id "
            f"WHERE j.open=1 AND r.status='queued' "
            f"AND r.strategy || '@' || r.strategy_version IN ({placeholders}) LIMIT 1",
            strategy_refs,
        ).fetchone()
        return row is not None

    def queued_job(self, strategy_refs: list[str]) -> tuple[Job, Run] | None:
        """第一个排队中的作业（超时兜底时用来找到要取消的运行）。"""

        if not strategy_refs:
            return None
        placeholders = ",".join("?" * len(strategy_refs))
        row = self.conn.execute(
            f"SELECT j.* FROM evolution_job j JOIN evolution_run r ON r.run_id=j.run_id "
            f"WHERE j.open=1 AND r.status='queued' "
            f"AND r.strategy || '@' || r.strategy_version IN ({placeholders}) "
            f"ORDER BY r.created_at LIMIT 1",
            strategy_refs,
        ).fetchone()
        if row is None:
            return None
        job = Job(id=row["job_id"], run_id=row["run_id"], attempt=row["attempt"], open=True)
        run = self.get_run(row["run_id"])
        return None if run is None else (job, run)

    def open_job_for_worker(self, worker_id: str) -> tuple[Job, Run] | None:
        """某个 worker 名下仍开放的作业（容器宿主等待其结束）。"""

        row = self.conn.execute(
            "SELECT * FROM evolution_job WHERE worker_id=? AND open=1 LIMIT 1",
            (worker_id,),
        ).fetchone()
        if row is None:
            return None
        job = Job(
            id=row["job_id"],
            run_id=row["run_id"],
            attempt=row["attempt"],
            worker_id=row["worker_id"],
            fencing_token=row["fencing_token"],
            open=True,
        )
        run = self.get_run(row["run_id"])
        return None if run is None else (job, run)

    @atomic
    def heartbeat(
        self, job_id: str, fencing_token: str | None, lease_seconds: int
    ) -> tuple[datetime, bool] | None:
        self._require_token(job_id, fencing_token)
        row = self.conn.execute("SELECT run_id FROM evolution_job WHERE job_id=?", (job_id,)).fetchone()
        expires = utcnow() + timedelta(seconds=lease_seconds)
        self.conn.execute(
            "UPDATE evolution_job SET lease_expires_at=? WHERE job_id=?",
            (_iso(expires), job_id),
        )
        run_row = self.conn.execute(
            "SELECT cancel_requested FROM evolution_run WHERE run_id=?",
            (row["run_id"],),
        ).fetchone()
        return expires, bool(run_row and run_row["cancel_requested"])

    @atomic
    def complete_job(self, job_id: str, fencing_token: str | None, summary: dict[str, Any]) -> None:
        self._require_token(job_id, fencing_token)
        row = self.conn.execute(
            "SELECT run_id FROM evolution_job WHERE job_id=? AND open=1", (job_id,)
        ).fetchone()
        if row is None:
            raise JobNotFound(job_id)
        self.conn.execute(
            "UPDATE evolution_run SET status='completed', ended_at=?, summary=? "
            "WHERE run_id=? AND status='running' AND cancel_requested=0",
            (_iso(utcnow()), _json(summary), row["run_id"]),
        )
        self.conn.execute("UPDATE evolution_job SET open=0 WHERE job_id=?", (job_id,))

    @atomic
    def fail_job(self, job_id: str, fencing_token: str | None, *, reason: str, retryable: bool) -> None:
        self._require_token(job_id, fencing_token)
        row = self.conn.execute(
            "SELECT j.run_id, j.attempt, r.max_attempts, r.status FROM evolution_job j "
            "JOIN evolution_run r ON r.run_id=j.run_id WHERE j.job_id=? AND j.open=1",
            (job_id,),
        ).fetchone()
        if row is None:
            raise JobNotFound(job_id)
        if row["status"] not in ("queued", "running"):
            self.conn.execute("UPDATE evolution_job SET open=0 WHERE job_id=?", (job_id,))
            return
        if retryable and row["attempt"] < row["max_attempts"]:
            self._queue_next_attempt(row["run_id"], job_id, row["attempt"])
        else:
            # 不可重试，或最后一次允许的尝试已经用掉（§5.3、§7.2）。
            self._end_run_failed(
                row["run_id"],
                job_id,
                "max_attempts" if retryable else "worker_failed",
                reason=reason,
            )

    @atomic
    def requeue_job(self, job_id: str) -> str | None:
        """平台侧重派发：关闭一个开放作业，并为同一运行排队下一次尝试。

        `fail_job` 校验策略侧持有的 fencing token；宿主兜底时（worker 退出却留下
        开放作业）宿主进程不持有沙箱内的 token，所以走这条不校验 token 的路径。
        返回被重新排队的运行 id；没有剩余尝试时返回 None（运行已失败）。
        """

        row = self.conn.execute(
            "SELECT j.run_id, j.attempt, r.max_attempts, r.status FROM evolution_job j "
            "JOIN evolution_run r ON r.run_id=j.run_id WHERE j.job_id=? AND j.open=1",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        if row["status"] not in ("queued", "running"):
            self.conn.execute("UPDATE evolution_job SET open=0 WHERE job_id=?", (job_id,))
            return None
        requeued = row["attempt"] < row["max_attempts"]
        if requeued:
            self._queue_next_attempt(row["run_id"], job_id, row["attempt"])
        else:
            self._end_run_failed(row["run_id"], job_id, "max_attempts")
        return row["run_id"] if requeued else None

    def _queue_next_attempt(self, run_id: str, job_id: str, attempt: int) -> None:
        """为同一运行排队下一次尝试：关闭当前作业、运行回 queued、插入新作业行。

        新作业的 `lease_expires_at` 保持 NULL：租约由 `claim_job` 在认领时写入。
        写成「现在」会让它在下一轮 `expire_leases` 清扫时立刻被判过期，白烧一次
        尝试（§7.2）。
        """

        next_attempt = attempt + 1
        self.conn.execute("UPDATE evolution_job SET open=0 WHERE job_id=?", (job_id,))
        self.conn.execute(
            "UPDATE evolution_run SET status='queued', attempt=? WHERE run_id=?",
            (next_attempt, run_id),
        )
        self.conn.execute(
            "INSERT INTO evolution_job (job_id, run_id, attempt, worker_id, "
            "lease_expires_at, fencing_token, open) VALUES (?, ?, ?, NULL, NULL, NULL, 1)",
            (f"{run_id}_{next_attempt}", run_id, next_attempt),
        )

    def _end_run_failed(
        self, run_id: str, job_id: str, end_reason: str, *, reason: str | None = None
    ) -> None:
        self.conn.execute("UPDATE evolution_job SET open=0 WHERE job_id=?", (job_id,))
        # 失败原因必须落库：否则 Run 只能看到 `max_attempts`，无法判断是策略异常、
        # 凭据缺失还是平台不可达。summary 是 JSON 列，所以原因包成
        # `{"failure": {"reason": ...}}`；策略自己写过的 summary 不会被覆盖。
        clipped = _clip(reason)
        failure = _json({"failure": {"reason": clipped}}) if clipped else None
        # 注意 `update_run` 会把空 summary 序列化成字符串 `'null'`，所以这里不能
        # 只用 COALESCE：`'null'` 是非 NULL，会把失败原因挡掉。
        self.conn.execute(
            "UPDATE evolution_run SET status='failed', ended_at=?, end_reason=?, "
            "summary=CASE WHEN summary IS NULL OR summary='null' THEN ? ELSE summary END "
            "WHERE run_id=?",
            (_iso(utcnow()), end_reason, failure, run_id),
        )

    @atomic
    def expire_leases(self, now: datetime) -> list[str]:
        rows = self.conn.execute(
            "SELECT j.job_id, j.run_id, j.attempt, r.max_attempts, r.status "
            "FROM evolution_job j JOIN evolution_run r ON r.run_id=j.run_id "
            "WHERE j.open=1 AND j.lease_expires_at IS NOT NULL AND j.lease_expires_at <= ?",
            (_iso(now),),
        ).fetchall()
        affected: list[str] = []
        for row in rows:
            affected.append(row["run_id"])
            if row["status"] not in ("queued", "running"):
                self.conn.execute("UPDATE evolution_job SET open=0 WHERE job_id=?", (row["job_id"],))
                continue
            if row["status"] in ("queued", "running") and row["attempt"] < row["max_attempts"]:
                self._queue_next_attempt(row["run_id"], row["job_id"], row["attempt"])
            else:
                self._end_run_failed(row["run_id"], row["job_id"], "max_attempts", reason="lease expired")
        return affected

    def _require_token(self, job_id: str, fencing_token: str | None) -> None:
        row = self.conn.execute(
            "SELECT fencing_token, lease_expires_at FROM evolution_job WHERE job_id=? AND open=1",
            (job_id,),
        ).fetchone()
        if row is None:
            raise JobNotFound(job_id)
        expiry = _parse(row["lease_expires_at"])
        if not fencing_token or row["fencing_token"] != fencing_token or expiry is None or expiry <= utcnow():
            raise StaleFencingToken(job_id, fencing_token)
