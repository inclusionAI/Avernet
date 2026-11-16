"""§12 存储：SQLite（singlebox 默认；其他环境的服务端库替换此实现）。

每次响应所依赖的写入都在返回前提交。同步 sqlite3，服务层以 async 包裹。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agentclaw.community.core.bot_genome.models import BotRef

from .budget import BudgetUsage
from .enums import ChargeSource, EndReason, OperationKind, OperationStatus, RunStatus
from .errors import (
    JobNotFound,
    OperationNotFound,
    PreconditionFailed,
    StaleFencingToken,
)
from .records import (
    Job,
    Operation,
    Run,
    RunCandidate,
    TriggerRecord,
    Workspace,
)

__all__ = ["EvolutionStore", "utcnow"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS evolution_bindings (
  owner_id TEXT NOT NULL, bot_id TEXT NOT NULL, etag TEXT NOT NULL,
  document TEXT NOT NULL, updated_by TEXT, updated_at TEXT NOT NULL,
  entity_id TEXT, engine_type TEXT, bot_type TEXT, entity_type TEXT,
  PRIMARY KEY (owner_id, bot_id));
CREATE TABLE IF NOT EXISTS evolution_run (
  run_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, entity_id TEXT NOT NULL, bot_id TEXT NOT NULL,
  engine_type TEXT NOT NULL, bot_type TEXT NOT NULL, entity_type TEXT NOT NULL,
  binding_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, body_hash TEXT NOT NULL,
  strategy TEXT NOT NULL, strategy_version TEXT NOT NULL,
  parent_ref TEXT NOT NULL, parent_revision TEXT NOT NULL,
  allowed_genes TEXT NOT NULL, verification_profile TEXT NOT NULL,
  params TEXT NOT NULL, budget_max TEXT NOT NULL, granted TEXT NOT NULL,
  agent_definitions TEXT NOT NULL, status TEXT NOT NULL, attempt INTEGER NOT NULL,
  max_attempts INTEGER NOT NULL, budget_used TEXT NOT NULL, candidate_count INTEGER NOT NULL,
  created_at TEXT NOT NULL, started_at TEXT, ended_at TEXT, end_reason TEXT,
  summary TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0, trigger TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evolution_run_key (
  owner_id TEXT NOT NULL, bot_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
  run_id TEXT NOT NULL, PRIMARY KEY (owner_id, bot_id, idempotency_key));
CREATE TABLE IF NOT EXISTS evolution_job (
  job_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, attempt INTEGER NOT NULL,
  worker_id TEXT, lease_expires_at TEXT, fencing_token TEXT,
  open INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS evolution_operation (
  operation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, kind TEXT NOT NULL,
  idempotency_key TEXT NOT NULL, status TEXT NOT NULL, cost TEXT NOT NULL,
  created_at TEXT, finished_at TEXT, result TEXT, error TEXT,
  UNIQUE (run_id, idempotency_key));
CREATE TABLE IF NOT EXISTS evolution_workspace (
  workspace_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, key TEXT NOT NULL,
  revision TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE (run_id, key));
CREATE TABLE IF NOT EXISTS evolution_run_candidate (
  run_id TEXT NOT NULL, candidate_id TEXT NOT NULL, revision TEXT NOT NULL,
  seq INTEGER NOT NULL, verdict TEXT NOT NULL DEFAULT 'pending',
  PRIMARY KEY (run_id, candidate_id));
CREATE TABLE IF NOT EXISTS evolution_budget_charge (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, source TEXT NOT NULL,
  usd TEXT NOT NULL, wall_clock_s INTEGER NOT NULL, rollouts INTEGER NOT NULL,
  tokens INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS evolution_charge_run ON evolution_budget_charge (run_id);
CREATE TABLE IF NOT EXISTS evolution_trigger_firing (
  binding_id TEXT NOT NULL, fire_key TEXT NOT NULL, status TEXT NOT NULL,
  reason TEXT, PRIMARY KEY (binding_id, fire_key));
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    return datetime.fromisoformat(text)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _load(text: str | None, default: Any = None) -> Any:
    return default if text is None else json.loads(text)


class EvolutionStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    # -- bindings -------------------------------------------------------
    def get_bindings(self, owner_id: str, bot_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT etag, document, updated_by, updated_at FROM evolution_bindings "
            "WHERE owner_id=? AND bot_id=?",
            (owner_id, bot_id),
        ).fetchone()
        if row is None:
            return None
        return {
            "etag": row["etag"],
            "document": _load(row["document"]),
            "updated_by": row["updated_by"],
            "updated_at": row["updated_at"],
        }

    def put_bindings(
        self,
        bot: BotRef,
        document: dict[str, Any],
        expected_etag: str | None,
        actor: str,
    ) -> str:
        owner_id, bot_id = bot.owner_id, bot.bot_id
        text = _json(document)
        etag = hashlib.sha256(text.encode()).hexdigest()[:16]
        now = _iso(utcnow())
        if expected_etag is None:
            exists = self.conn.execute(
                "SELECT 1 FROM evolution_bindings WHERE owner_id=? AND bot_id=?",
                (owner_id, bot_id),
            ).fetchone()
            if exists is not None:
                raise PreconditionFailed("bindings already exist for this bot")
        else:
            row = self.conn.execute(
                "SELECT etag FROM evolution_bindings WHERE owner_id=? AND bot_id=?",
                (owner_id, bot_id),
            ).fetchone()
            if row is None or row["etag"] != expected_etag:
                raise PreconditionFailed("etag mismatch")
        self.conn.execute(
            "INSERT INTO evolution_bindings (owner_id, bot_id, etag, document, updated_by, "
            "updated_at, entity_id, engine_type, bot_type, entity_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(owner_id, bot_id) DO UPDATE SET "
            "etag=excluded.etag, document=excluded.document, updated_by=excluded.updated_by, "
            "updated_at=excluded.updated_at, entity_id=excluded.entity_id, "
            "engine_type=excluded.engine_type, bot_type=excluded.bot_type, "
            "entity_type=excluded.entity_type",
            (
                owner_id,
                bot_id,
                etag,
                text,
                actor,
                now,
                bot.entity_id,
                bot.engine_type,
                bot.bot_type,
                bot.entity_type,
            ),
        )
        self.conn.commit()
        return etag

    def list_bindings(self) -> list[tuple[BotRef, dict[str, Any]]]:
        rows = self.conn.execute(
            "SELECT owner_id, bot_id, entity_id, engine_type, bot_type, entity_type, document "
            "FROM evolution_bindings"
        ).fetchall()
        return [
            (
                BotRef(
                    entity_id=row["entity_id"] or row["bot_id"],
                    bot_id=row["bot_id"],
                    owner_id=row["owner_id"],
                    engine_type=row["engine_type"] or "openclaw",
                    bot_type=row["bot_type"] or "personal",
                    entity_type=row["entity_type"] or "staff",
                ),
                _load(row["document"]) or {},
            )
            for row in rows
        ]

    # -- runs -----------------------------------------------------------
    def run_exists(self, run_id: str) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM evolution_run WHERE run_id=?", (run_id,)
            ).fetchone()
            is not None
        )

    def find_run_by_key(
        self, owner_id: str, bot_id: str, key: str
    ) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT run_id FROM evolution_run_key WHERE owner_id=? AND bot_id=? AND idempotency_key=?",
            (owner_id, bot_id, key),
        ).fetchone()
        return None if row is None else self.get_run(row["run_id"])

    def insert_run(self, run: Run, owner_id: str, bot_id: str, key_hash: str) -> None:
        now = _iso(run.created_at)
        values = {
            "run_id": run.id,
            "owner_id": owner_id,
            "entity_id": run.bot.entity_id,
            "bot_id": bot_id,
            "engine_type": run.bot.engine_type,
            "bot_type": run.bot.bot_type,
            "entity_type": run.bot.entity_type,
            "binding_id": run.binding_id,
            "idempotency_key": run.idempotency_key,
            "body_hash": key_hash,
            "strategy": run.strategy,
            "strategy_version": run.strategy_version,
            "parent_ref": run.parent_ref,
            "parent_revision": run.parent_revision,
            "allowed_genes": _json(run.allowed_genes),
            "verification_profile": run.verification_profile,
            "params": _json(run.params),
            "budget_max": _json(run.budget_max),
            "granted": _json(run.granted),
            "agent_definitions": _json(run.agent_definitions),
            "status": run.status.value,
            "attempt": run.attempt,
            "max_attempts": run.max_attempts,
            "budget_used": _json(_usage_json(run.budget_used)),
            "candidate_count": run.candidate_count,
            "created_at": now,
            "started_at": None,
            "ended_at": _iso(run.ended_at),
            "end_reason": run.end_reason.value if run.end_reason else None,
            "summary": _json(run.summary),
            "cancel_requested": int(run.cancel_requested),
            "trigger": _json(_trigger_json(run.trigger)),
        }
        columns = ", ".join(values)
        placeholders = ", ".join("?" * len(values))
        self.conn.execute(
            f"INSERT INTO evolution_run ({columns}) VALUES ({placeholders})",
            tuple(values.values()),
        )
        self.conn.execute(
            "INSERT INTO evolution_run_key (owner_id, bot_id, idempotency_key, run_id) VALUES (?, ?, ?, ?)",
            (owner_id, bot_id, run.idempotency_key, run.id),
        )
        self.conn.commit()

    def get_run(self, run_id: str) -> Run | None:
        row = self.conn.execute(
            "SELECT * FROM evolution_run WHERE run_id=?", (run_id,)
        ).fetchone()
        return None if row is None else _run_from_row(row)

    def list_runs(
        self,
        owner_id: str,
        bot_id: str,
        status: list[RunStatus] | None = None,
        binding_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Run], int]:
        where = ["owner_id=?", "bot_id=?"]
        params: list[Any] = [owner_id, bot_id]
        if status:
            where.append("status IN (" + ",".join("?" * len(status)) + ")")
            params.extend(s.value for s in status)
        if binding_id is not None:
            where.append("binding_id=?")
            params.append(binding_id)
        clause = " AND ".join(where)
        total = self.conn.execute(
            f"SELECT COUNT(*) FROM evolution_run WHERE {clause}", params
        ).fetchone()[0]
        rows = self.conn.execute(
            f"SELECT * FROM evolution_run WHERE {clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, page_size, (page - 1) * page_size),
        ).fetchall()
        return [_run_from_row(r) for r in rows], total

    def update_run(self, run: Run) -> None:
        self.conn.execute(
            "UPDATE evolution_run SET status=?, attempt=?, budget_used=?, candidate_count=?, "
            "started_at=?, ended_at=?, end_reason=?, summary=?, cancel_requested=? WHERE run_id=?",
            (
                run.status.value,
                run.attempt,
                _json(_usage_json(run.budget_used)),
                run.candidate_count,
                _iso(run.started_at),
                _iso(run.ended_at),
                run.end_reason.value if run.end_reason else None,
                _json(run.summary),
                int(run.cancel_requested),
                run.id,
            ),
        )
        self.conn.commit()

    # -- jobs -----------------------------------------------------------
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
        self.conn.commit()

    def claim_job(
        self,
        worker_id: str,
        strategy_refs: list[str],
        *,
        lease_seconds: int,
        fencing_token: str,
    ) -> tuple[Job, Run] | None:
        placeholders = ",".join("?" * len(strategy_refs))
        row = self.conn.execute(
            f"SELECT j.*, r.* FROM evolution_job j "
            f"JOIN evolution_run r ON r.run_id=j.run_id "
            f"WHERE j.open=1 AND r.status='queued' AND r.strategy || '@' || r.strategy_version IN ({placeholders}) "
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
        self.conn.commit()
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
        job = Job(
            id=row["job_id"], run_id=row["run_id"], attempt=row["attempt"], open=True
        )
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

    def heartbeat(
        self, job_id: str, fencing_token: str | None, lease_seconds: int
    ) -> tuple[datetime, bool] | None:
        row = self.conn.execute(
            "SELECT fencing_token, run_id FROM evolution_job WHERE job_id=? AND open=1",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        if row["fencing_token"] != fencing_token:
            raise StaleFencingToken(job_id, fencing_token)
        expires = utcnow() + timedelta(seconds=lease_seconds)
        self.conn.execute(
            "UPDATE evolution_job SET lease_expires_at=? WHERE job_id=?",
            (_iso(expires), job_id),
        )
        run_row = self.conn.execute(
            "SELECT cancel_requested FROM evolution_run WHERE run_id=?",
            (row["run_id"],),
        ).fetchone()
        self.conn.commit()
        return expires, bool(run_row and run_row["cancel_requested"])

    def complete_job(
        self, job_id: str, fencing_token: str | None, summary: dict[str, Any]
    ) -> None:
        self._require_token(job_id, fencing_token)
        row = self.conn.execute(
            "SELECT run_id FROM evolution_job WHERE job_id=? AND open=1", (job_id,)
        ).fetchone()
        if row is None:
            raise JobNotFound(job_id)
        self.conn.execute(
            "UPDATE evolution_run SET status='completed', ended_at=?, summary=? WHERE run_id=?",
            (_iso(utcnow()), _json(summary), row["run_id"]),
        )
        self.conn.execute("UPDATE evolution_job SET open=0 WHERE job_id=?", (job_id,))
        self.conn.commit()

    def fail_job(
        self, job_id: str, fencing_token: str | None, *, reason: str, retryable: bool
    ) -> None:
        self._require_token(job_id, fencing_token)
        row = self.conn.execute(
            "SELECT j.run_id, j.attempt, r.max_attempts FROM evolution_job j "
            "JOIN evolution_run r ON r.run_id=j.run_id WHERE j.job_id=? AND j.open=1",
            (job_id,),
        ).fetchone()
        if row is None:
            raise JobNotFound(job_id)
        if retryable and row["attempt"] < row["max_attempts"]:
            self._queue_next_attempt(row["run_id"], job_id, row["attempt"])
        else:
            # 不可重试，或最后一次允许的尝试已经用掉（§5.3、§7.2）。
            self._end_run_failed(
                row["run_id"],
                job_id,
                "max_attempts" if retryable else "worker_failed",
            )
        self.conn.commit()

    def requeue_job(self, job_id: str) -> str | None:
        """平台侧重派发：关闭一个开放作业，并为同一运行排队下一次尝试。

        `fail_job` 校验策略侧持有的 fencing token；宿主兜底时（worker 退出却留下
        开放作业）宿主进程不持有沙箱内的 token，所以走这条不校验 token 的路径。
        返回被重新排队的运行 id；没有剩余尝试时返回 None（运行已失败）。
        """

        row = self.conn.execute(
            "SELECT j.run_id, j.attempt, r.max_attempts FROM evolution_job j "
            "JOIN evolution_run r ON r.run_id=j.run_id WHERE j.job_id=? AND j.open=1",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        requeued = row["attempt"] < row["max_attempts"]
        if requeued:
            self._queue_next_attempt(row["run_id"], job_id, row["attempt"])
        else:
            self._end_run_failed(row["run_id"], job_id, "max_attempts")
        self.conn.commit()
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

    def _end_run_failed(self, run_id: str, job_id: str, end_reason: str) -> None:
        self.conn.execute("UPDATE evolution_job SET open=0 WHERE job_id=?", (job_id,))
        self.conn.execute(
            "UPDATE evolution_run SET status='failed', ended_at=?, end_reason=? WHERE run_id=?",
            (_iso(utcnow()), end_reason, run_id),
        )

    def expire_leases(self, now: datetime) -> list[str]:
        rows = self.conn.execute(
            "SELECT j.job_id, j.run_id, j.attempt, r.max_attempts, r.status "
            "FROM evolution_job j JOIN evolution_run r ON r.run_id=j.run_id "
            "WHERE j.open=1 AND j.lease_expires_at IS NOT NULL AND j.lease_expires_at < ?",
            (_iso(now),),
        ).fetchall()
        affected: list[str] = []
        for row in rows:
            affected.append(row["run_id"])
            if (
                row["status"] in ("queued", "running")
                and row["attempt"] < row["max_attempts"]
            ):
                self._queue_next_attempt(row["run_id"], row["job_id"], row["attempt"])
            else:
                self._end_run_failed(row["run_id"], row["job_id"], "max_attempts")
        self.conn.commit()
        return affected

    def cancel_unfinished_operations(self, run_id: str) -> int:
        cur = self.conn.execute(
            "UPDATE evolution_operation SET status='cancelled', finished_at=? WHERE run_id=? AND status NOT IN ('succeeded','failed','cancelled')",
            (_iso(utcnow()), run_id),
        )
        self.conn.commit()
        return cur.rowcount

    # -- operations -----------------------------------------------------
    def insert_operation(self, operation: Operation) -> str:
        row = self.conn.execute(
            "SELECT operation_id FROM evolution_operation WHERE run_id=? AND idempotency_key=?",
            (operation.run_id, operation.idempotency_key),
        ).fetchone()
        if row is not None:
            return row["operation_id"]
        self.conn.execute(
            "INSERT INTO evolution_operation (operation_id, run_id, kind, idempotency_key, status, cost, created_at) "
            "VALUES (?, ?, ?, ?, 'queued', ?, ?)",
            (
                operation.id,
                operation.run_id,
                operation.kind.value,
                operation.idempotency_key,
                _json(_usage_json(operation.cost)),
                _iso(operation.created_at),
            ),
        )
        self.conn.commit()
        return operation.id

    def get_operation(self, run_id: str, operation_id: str) -> Operation | None:
        row = self.conn.execute(
            "SELECT * FROM evolution_operation WHERE run_id=? AND operation_id=?",
            (run_id, operation_id),
        ).fetchone()
        return None if row is None else _operation_from_row(row)

    def cancel_operation(self, run_id: str, operation_id: str) -> Operation:
        row = self.get_operation(run_id, operation_id)
        if row is None:
            raise OperationNotFound(operation_id)
        self.conn.execute(
            "UPDATE evolution_operation SET status='cancelled', finished_at=? WHERE operation_id=?",
            (_iso(utcnow()), operation_id),
        )
        self.conn.commit()
        row = self.get_operation(run_id, operation_id)
        assert row is not None
        return row

    # -- workspaces -----------------------------------------------------
    def insert_workspace(self, ws: Workspace) -> None:
        self.conn.execute(
            "INSERT INTO evolution_workspace (workspace_id, run_id, key, revision, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (ws.id, ws.run_id, ws.key, ws.revision, _iso(ws.created_at)),
        )
        self.conn.commit()

    def find_workspace(self, run_id: str, key: str) -> Workspace | None:
        row = self.conn.execute(
            "SELECT * FROM evolution_workspace WHERE run_id=? AND key=?", (run_id, key)
        ).fetchone()
        return None if row is None else _workspace_from_row(row)

    # -- candidates -----------------------------------------------------
    def upsert_candidate(
        self,
        run_id: str,
        candidate_id: str,
        revision: str,
        seq: int,
        verdict: str = "pending",
    ) -> None:
        self.conn.execute(
            "INSERT INTO evolution_run_candidate (run_id, candidate_id, revision, seq, verdict) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(run_id, candidate_id) DO UPDATE SET "
            "revision=excluded.revision, verdict=excluded.verdict",
            (run_id, candidate_id, revision, seq, verdict),
        )
        self.conn.commit()

    def find_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT run_id, candidate_id, revision, seq, verdict "
            "FROM evolution_run_candidate WHERE candidate_id=? ORDER BY seq LIMIT 1",
            (candidate_id,),
        ).fetchone()
        return None if row is None else dict(row)

    def candidates(self, run_id: str) -> list[RunCandidate]:
        rows = self.conn.execute(
            "SELECT * FROM evolution_run_candidate WHERE run_id=? ORDER BY seq",
            (run_id,),
        ).fetchall()
        return [
            RunCandidate(
                r["run_id"], r["candidate_id"], r["revision"], r["seq"], r["verdict"]
            )
            for r in rows
        ]

    # -- trigger firings ------------------------------------------------
    def firing(self, binding_id: str, fire_key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT status, reason FROM evolution_trigger_firing "
            "WHERE binding_id=? AND fire_key=?",
            (binding_id, fire_key),
        ).fetchone()
        return (
            None if row is None else {"status": row["status"], "reason": row["reason"]}
        )

    def record_firing(
        self, binding_id: str, fire_key: str, status: str, reason: str | None
    ) -> None:
        self.conn.execute(
            "INSERT INTO evolution_trigger_firing (binding_id, fire_key, status, reason) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(binding_id, fire_key) DO NOTHING",
            (binding_id, fire_key, status, reason),
        )
        self.conn.commit()

    # -- budget ---------------------------------------------------------
    def charge(
        self, run_id: str, usage: BudgetUsage, source: ChargeSource
    ) -> dict[str, Any]:
        self.conn.execute(
            "INSERT INTO evolution_budget_charge (run_id, source, usd, wall_clock_s, rollouts, tokens, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                source.value,
                str(usage.usd),
                usage.wall_clock_s,
                usage.rollouts,
                usage.tokens,
                _iso(utcnow()),
            ),
        )
        rows = self.conn.execute(
            "SELECT usd, wall_clock_s, rollouts, tokens FROM evolution_budget_charge WHERE run_id=?",
            (run_id,),
        ).fetchall()
        total_usd = sum(float(r["usd"]) for r in rows)
        total_wall = max(int(r["wall_clock_s"]) for r in rows)
        total_rollouts = sum(int(r["rollouts"]) for r in rows)
        total_tokens = sum(int(r["tokens"]) for r in rows)
        used = {
            "usd": total_usd,
            "wall_clock_s": total_wall,
            "rollouts": total_rollouts,
            "tokens": total_tokens,
        }
        self.conn.execute(
            "UPDATE evolution_run SET budget_used=? WHERE run_id=?",
            (_json(used), run_id),
        )
        self.conn.commit()
        return used

    def used_budget(self, run_id: str) -> BudgetUsage:
        row = self.conn.execute(
            "SELECT budget_used FROM evolution_run WHERE run_id=?", (run_id,)
        ).fetchone()
        data = _load(row["budget_used"]) if row else {}
        return BudgetUsage.of(
            usd=data.get("usd", 0),
            wall_clock_s=data.get("wall_clock_s", 0),
            rollouts=data.get("rollouts", 0),
            tokens=data.get("tokens", 0),
        )

    def _require_token(self, job_id: str, fencing_token: str | None) -> None:
        row = self.conn.execute(
            "SELECT fencing_token FROM evolution_job WHERE job_id=? AND open=1",
            (job_id,),
        ).fetchone()
        if row is None:
            raise JobNotFound(job_id)
        if row["fencing_token"] != fencing_token:
            raise StaleFencingToken(job_id, fencing_token)


def _budget_max_json(budget_max: dict) -> dict:
    return budget_max


def _usage_json(usage: BudgetUsage) -> dict:
    return {
        "usd": str(usage.usd),
        "wall_clock_s": usage.wall_clock_s,
        "rollouts": usage.rollouts,
        "tokens": usage.tokens,
    }


def _trigger_json(trigger: TriggerRecord) -> dict:
    return {
        "kind": trigger.kind,
        "fire_time": _iso(trigger.fire_time),
        "event_id": trigger.event_id,
        "requested_by": trigger.requested_by,
    }


def _trigger_record(data: dict) -> TriggerRecord:
    return TriggerRecord(
        kind=data.get("kind", "manual"),
        fire_time=_parse(data.get("fire_time")),
        event_id=data.get("event_id"),
        requested_by=data.get("requested_by"),
    )


def _run_from_row(row: sqlite3.Row) -> Run:
    return Run(
        id=row["run_id"],
        bot=BotRef(
            entity_id=row["entity_id"],
            bot_id=row["bot_id"],
            owner_id=row["owner_id"],
            engine_type=row["engine_type"],
            bot_type=row["bot_type"],
            entity_type=row["entity_type"],
        ),
        binding_id=row["binding_id"],
        idempotency_key=row["idempotency_key"],
        trigger=_trigger_record(_load(row["trigger"]) or {}),
        strategy=row["strategy"],
        strategy_version=row["strategy_version"],
        parent_ref=row["parent_ref"],
        parent_revision=row["parent_revision"],
        allowed_genes=_load(row["allowed_genes"]),
        verification_profile=row["verification_profile"],
        params=_load(row["params"]),
        budget_max=_load(row["budget_max"]),
        granted=_load(row["granted"]),
        agent_definitions=_load(row["agent_definitions"]),
        status=RunStatus(row["status"]),
        attempt=row["attempt"],
        max_attempts=row["max_attempts"],
        budget_used=_usage_from_json(row["budget_used"]),
        candidate_count=row["candidate_count"],
        created_at=_parse(row["created_at"]),
        started_at=_parse(row["started_at"]),
        ended_at=_parse(row["ended_at"]),
        end_reason=EndReason(row["end_reason"]) if row["end_reason"] else None,
        summary=_load(row["summary"]),
        cancel_requested=bool(row["cancel_requested"]),
    )


def _job_from_row(row: sqlite3.Row) -> Job:
    return Job(
        id=row["job_id"],
        run_id=row["run_id"],
        attempt=row["attempt"],
        worker_id=row["worker_id"],
        lease_expires_at=_parse(row["lease_expires_at"]),
        fencing_token=row["fencing_token"],
        open=bool(row["open"]),
    )


def _operation_from_row(row: sqlite3.Row) -> Operation:
    return Operation(
        id=row["operation_id"],
        run_id=row["run_id"],
        kind=OperationKind(row["kind"]),
        idempotency_key=row["idempotency_key"],
        status=OperationStatus(row["status"]),
        cost=_usage_from_json(row["cost"]),
        created_at=_parse(row["created_at"]),
        finished_at=_parse(row["finished_at"]),
        result=_load(row["result"]),
        error=_load(row["error"]),
    )


def _workspace_from_row(row: sqlite3.Row) -> Workspace:
    return Workspace(
        id=row["workspace_id"],
        run_id=row["run_id"],
        key=row["key"],
        revision=row["revision"],
        created_at=_parse(row["created_at"]),
    )


def _usage_from_json(text: str | None) -> BudgetUsage:
    data = _load(text) or {}
    return BudgetUsage.of(
        usd=data.get("usd", 0),
        wall_clock_s=data.get("wall_clock_s", 0),
        rollouts=data.get("rollouts", 0),
        tokens=data.get("tokens", 0),
    )
