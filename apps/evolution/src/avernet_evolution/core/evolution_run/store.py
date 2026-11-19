"""§12 存储：SQLite（singlebox 默认；其他环境的服务端库替换此实现）。

每次响应所依赖的写入都在返回前提交。同步 sqlite3，服务层以 async 包裹。
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from avernet_genome.models import BotRef

from .budget import BudgetUsage
from .enums import ChargeSource, RunStatus
from .errors import (
    OperationNotFound,
    PreconditionFailed,
)
from .events import ExecutionEvents
from .records import (
    Operation,
    Run,
    RunCandidate,
    Workspace,
)
from .store_codec import (
    _iso,
    _json,
    _load,
    _operation_from_row,
    _run_from_row,
    _trigger_json,
    _usage_json,
    _workspace_from_row,
    utcnow,
)
from .store_jobs import JobStoreMixin, atomic

__all__ = ["EvolutionStore", "utcnow"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS evolution_artifact (
  artifact_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, name TEXT NOT NULL,
  content BLOB NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS evolution_artifact_run ON evolution_artifact (run_id);
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
  submission_complete INTEGER NOT NULL DEFAULT 0,
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


class EvolutionStore(JobStoreMixin):
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.conn = sqlite3.connect(str(path), timeout=30)
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.events = ExecutionEvents(self.conn)
        # Existing databases may contain partially submitted candidates. Reconcile
        # them on retry; a revision alone does not prove the ledger was written.
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(evolution_run_candidate)")}
        if "submission_complete" not in columns:
            self.conn.execute(
                "ALTER TABLE evolution_run_candidate ADD COLUMN "
                "submission_complete INTEGER NOT NULL DEFAULT 0"
            )
        self.conn.commit()

    def put_artifact(self, run_id: str, name: str, content: bytes) -> str:
        """Opaque IDs only; bytes are durable and names never become filesystem paths."""
        import secrets

        if self.get_run(run_id) is None:
            raise KeyError("artifact run does not exist")
        if not name or len(name) > 255 or len(content) > 5 * 1024 * 1024:
            raise ValueError("artifact name/content exceeds limits")
        artifact_id = "art_" + secrets.token_hex(16)
        with self.conn:
            self.conn.execute(
                "INSERT INTO evolution_artifact VALUES (?, ?, ?, ?, ?)",
                (artifact_id, run_id, name, content, _iso(utcnow())),
            )
        return artifact_id

    def get_artifact(self, run_id: str, artifact_id: str) -> bytes:
        row = self.conn.execute(
            "SELECT content FROM evolution_artifact WHERE run_id=? AND artifact_id=?",
            (run_id, artifact_id),
        ).fetchone()
        if row is None:
            raise KeyError("artifact not found in this run")
        return bytes(row["content"])

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
            self.conn.execute("SELECT 1 FROM evolution_run WHERE run_id=?", (run_id,)).fetchone() is not None
        )

    def find_run_by_key(self, owner_id: str, bot_id: str, key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT run_id FROM evolution_run_key WHERE owner_id=? AND bot_id=? AND idempotency_key=?",
            (owner_id, bot_id, key),
        ).fetchone()
        return None if row is None else self.get_run(row["run_id"])

    @atomic
    def insert_run(self, run: Run, owner_id: str, bot_id: str, key_hash: str) -> None:
        self._insert_run(run, owner_id, bot_id, key_hash)

    @atomic
    def enqueue_run(self, run: Run, key_hash: str) -> None:
        from .errors import BindingBusy

        active = self.conn.execute(
            "SELECT 1 FROM evolution_run WHERE owner_id=? AND bot_id=? AND binding_id=? "
            "AND status IN ('queued','running')",
            (run.bot.owner_id, run.bot.bot_id, run.binding_id),
        ).fetchone()
        if active is not None:
            raise BindingBusy(run.binding_id)
        self._insert_run(run, run.bot.owner_id, run.bot.bot_id, key_hash)
        self.conn.execute(
            "INSERT INTO evolution_job (job_id, run_id, attempt, open) VALUES (?, ?, 1, 1)",
            (f"{run.id}_1", run.id),
        )

    def _insert_run(self, run: Run, owner_id: str, bot_id: str, key_hash: str) -> None:
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

    def get_run(self, run_id: str) -> Run | None:
        row = self.conn.execute("SELECT * FROM evolution_run WHERE run_id=?", (run_id,)).fetchone()
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
        total = self.conn.execute(f"SELECT COUNT(*) FROM evolution_run WHERE {clause}", params).fetchone()[0]
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
    def reserve_candidate(self, run_id: str, candidate_id: str) -> RunCandidate:
        """Allocate one stable sequence, including on retries after a crash."""

        with self.conn:
            self.conn.execute(
                "INSERT INTO evolution_run_candidate (run_id, candidate_id, revision, seq) "
                "SELECT ?, ?, '', COALESCE(MAX(seq), 0) + 1 FROM evolution_run_candidate "
                "WHERE run_id=? ON CONFLICT(run_id, candidate_id) DO NOTHING",
                (run_id, candidate_id, run_id),
            )
        return next(row for row in self.candidates(run_id) if row.candidate_id == candidate_id)

    def submission_complete(self, run_id: str, candidate_id: str) -> bool:
        row = self.conn.execute(
            "SELECT submission_complete FROM evolution_run_candidate WHERE run_id=? AND candidate_id=?",
            (run_id, candidate_id),
        ).fetchone()
        return bool(row and row["submission_complete"])

    def complete_submission(self, run_id: str, candidate_id: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE evolution_run_candidate SET submission_complete=1 WHERE run_id=? AND candidate_id=?",
                (run_id, candidate_id),
            )

    def record_candidate_revision(self, run_id: str, candidate_id: str, revision: str) -> None:
        """Persist the revision and its count together, without overwriting run state."""

        with self.conn:
            self.conn.execute(
                "UPDATE evolution_run_candidate SET revision=? WHERE run_id=? AND candidate_id=?",
                (revision, run_id, candidate_id),
            )
            self.conn.execute(
                "UPDATE evolution_run SET candidate_count=(SELECT COUNT(*) "
                "FROM evolution_run_candidate WHERE run_id=? AND revision != '') WHERE run_id=?",
                (run_id, run_id),
            )

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
            RunCandidate(r["run_id"], r["candidate_id"], r["revision"], r["seq"], r["verdict"]) for r in rows
        ]

    # -- trigger firings ------------------------------------------------
    def firing(self, binding_id: str, fire_key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT status, reason FROM evolution_trigger_firing WHERE binding_id=? AND fire_key=?",
            (binding_id, fire_key),
        ).fetchone()
        return None if row is None else {"status": row["status"], "reason": row["reason"]}

    def record_firing(self, binding_id: str, fire_key: str, status: str, reason: str | None) -> None:
        self.conn.execute(
            "INSERT INTO evolution_trigger_firing (binding_id, fire_key, status, reason) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(binding_id, fire_key) DO NOTHING",
            (binding_id, fire_key, status, reason),
        )
        self.conn.commit()

    # -- budget ---------------------------------------------------------
    def charge(self, run_id: str, usage: BudgetUsage, source: ChargeSource) -> dict[str, Any]:
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
        row = self.conn.execute("SELECT budget_used FROM evolution_run WHERE run_id=?", (run_id,)).fetchone()
        data = _load(row["budget_used"]) if row else {}
        return BudgetUsage.of(
            usd=data.get("usd", 0),
            wall_clock_s=data.get("wall_clock_s", 0),
            rollouts=data.get("rollouts", 0),
            tokens=data.get("tokens", 0),
        )
