"""SQLite record codecs; internal storage detail, not a wire contract."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from avernet_genome.models import BotRef
from avernet_genome.mutation_scope import FrozenMutationScope

from .budget import BudgetUsage
from .enums import EndReason, OperationKind, OperationStatus, RunStatus
from .records import Job, Operation, Run, TriggerRecord, Workspace


def utcnow() -> datetime:
    return datetime.now(UTC)


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
        "operator_user_id": trigger.operator_user_id,
    }


def _trigger_record(data: dict) -> TriggerRecord:
    return TriggerRecord(
        kind=data.get("kind", "manual"),
        fire_time=_parse(data.get("fire_time")),
        event_id=data.get("event_id"),
        requested_by=data.get("requested_by"),
        operator_user_id=data.get("operator_user_id"),
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
        mutation_scope=FrozenMutationScope.model_validate_json(row["mutation_scope"])
        if row["mutation_scope"] is not None
        else None,
        verification_profile=row["verification_profile"],
        auto_promote_ceiling=row["auto_promote_ceiling"],
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
