"""Unified publish operation ledger repository (prod OceanBase + local SQLite).

One ORM body behind :class:`PublishOperationRepositoryProtocol`, mirroring
``BotPublishRepository``: the only per-environment difference is the injected
:class:`DatabasePlugin`, whose ``orm_session()`` yields a SQLAlchemy ``Session``
in both runtimes.

State transitions are **single optimistic-lock UPDATEs** — a bulk
``.update(..., synchronize_session=False)`` with ``WHERE id=? AND state IN
(<allowed sources>)``. When no row matches the allowed source state the
transition lost the CAS and ``None`` is returned (no SELECT-first guard).
``gmt_modified`` is set ``func.now()`` DB-side on every UPDATE (a Core/bulk
UPDATE fires neither ``onupdate`` nor a Python default on SQLite). ``params`` /
``result`` are stored as JSON strings; a blind full-overwrite is the contract
(callers read-modify-write), matching ``ac_bot_publish.ext``.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from injector import inject
from sqlalchemy import func

from agentclaw.community.core.service_bot.repository.models import (
    PublishOperationModel,
    PublishOperationRecord,
    PublishOperationState,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()

_TERMINAL = [s.value for s in PublishOperationState.terminal()]


class PublishOperationRepository:
    """Unified ORM ``PublishOperationRepositoryProtocol`` implementation."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self.Model = PublishOperationModel

    # ── insert ──────────────────────────────────────────────────────────
    def insert(self, data: Dict[str, Any]) -> PublishOperationRecord:
        params = data.get("params")
        result = data.get("result")
        with self._db.orm_session() as db:
            row = self.Model(
                publish_id=data["publish_id"],
                operation_kind=data["operation_kind"],
                stage=data.get("stage", ""),
                attempt=data.get("attempt", 1),
                state=data.get("state", PublishOperationState.PENDING.value),
                request_id=data["request_id"],
                bot_uuid=data.get("bot_uuid", ""),
                baas_publish_id=data.get("baas_publish_id"),
                params=json.dumps(params, ensure_ascii=False) if params is not None else None,
                result=json.dumps(result, ensure_ascii=False) if result is not None else None,
                last_error=data.get("last_error"),
                operator=data.get("operator", ""),
                env=data.get("env", get_current_env()),
            )
            db.add(row)
            db.flush()
            new_id = row.id
            logger.info(
                "[publish_operation:insert] id=%s publish_id=%s kind=%s stage=%s attempt=%s",
                new_id, data["publish_id"], data["operation_kind"],
                data.get("stage", ""), data.get("attempt", 1),
            )
        return self.get_by_id(new_id)

    # ── queries ─────────────────────────────────────────────────────────
    def get_by_id(self, op_id: int) -> Optional[PublishOperationRecord]:
        with self._db.orm_session() as db:
            row = db.query(self.Model).filter(self.Model.id == op_id).first()
            return row.to_record() if row else None

    def get_by_key(
        self,
        publish_id: int,
        operation_kind: str,
        stage: str,
        attempt: int,
    ) -> Optional[PublishOperationRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model)
                .filter(
                    self.Model.publish_id == publish_id,
                    self.Model.operation_kind == operation_kind,
                    self.Model.stage == stage,
                    self.Model.attempt == attempt,
                )
                .first()
            )
            return row.to_record() if row else None

    def get_latest_by_kind(
        self,
        publish_id: int,
        operation_kind: str,
        stage: str,
    ) -> Optional[PublishOperationRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model)
                .filter(
                    self.Model.publish_id == publish_id,
                    self.Model.operation_kind == operation_kind,
                    self.Model.stage == stage,
                )
                .order_by(self.Model.attempt.desc())
                .first()
            )
            return row.to_record() if row else None

    def list_by_publish(self, publish_id: int) -> List[PublishOperationRecord]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self.Model)
                .filter(self.Model.publish_id == publish_id)
                .order_by(self.Model.id.asc())
                .all()
            )
            return [r.to_record() for r in rows]

    def list_by_bot(self, bot_uuid: str, env: str) -> List[PublishOperationRecord]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self.Model)
                .filter(
                    self.Model.bot_uuid == bot_uuid,
                    self.Model.env == env,
                )
                .order_by(self.Model.id.asc())
                .all()
            )
            return [r.to_record() for r in rows]

    def max_attempt(
        self,
        publish_id: int,
        operation_kind: str,
        stage: str,
    ) -> int:
        with self._db.orm_session() as db:
            value = (
                db.query(func.max(self.Model.attempt))
                .filter(
                    self.Model.publish_id == publish_id,
                    self.Model.operation_kind == operation_kind,
                    self.Model.stage == stage,
                )
                .scalar()
            )
        return int(value) if value is not None else 0

    # ── CAS state transitions ───────────────────────────────────────────
    def record_workflow(
        self,
        op_id: int,
        *,
        baas_publish_id: int,
        bot_uuid: Optional[str] = None,
    ) -> Optional[PublishOperationRecord]:
        values: Dict[Any, Any] = {
            self.Model.baas_publish_id: baas_publish_id,
            self.Model.state: PublishOperationState.ID_RECORDED.value,
            self.Model.gmt_modified: func.now(),
        }
        if bot_uuid is not None:
            values[self.Model.bot_uuid] = bot_uuid
        return self._cas_update(
            op_id, values, allowed_sources=[PublishOperationState.PENDING.value]
        )

    def complete(self, op_id: int) -> Optional[PublishOperationRecord]:
        return self._cas_update(
            op_id,
            {
                self.Model.state: PublishOperationState.COMPLETED.value,
                self.Model.gmt_modified: func.now(),
            },
            allowed_sources=[PublishOperationState.ID_RECORDED.value],
        )

    def fail(self, op_id: int, error: str) -> Optional[PublishOperationRecord]:
        return self._cas_update(
            op_id,
            {
                self.Model.state: PublishOperationState.FAILED.value,
                self.Model.last_error: error,
                self.Model.gmt_modified: func.now(),
            },
            forbidden_sources=_TERMINAL,
        )

    def abandon(self, op_id: int, reason: str) -> Optional[PublishOperationRecord]:
        return self._cas_update(
            op_id,
            {
                self.Model.state: PublishOperationState.ABANDONED.value,
                self.Model.last_error: reason,
                self.Model.gmt_modified: func.now(),
            },
            forbidden_sources=_TERMINAL,
        )

    # ── field updates (no state change) ─────────────────────────────────
    def update_result(
        self,
        op_id: int,
        result: Dict[str, Any],
    ) -> Optional[PublishOperationRecord]:
        with self._db.orm_session() as db:
            affected = (
                db.query(self.Model)
                .filter(self.Model.id == op_id)
                .update(
                    {
                        self.Model.result: json.dumps(result, ensure_ascii=False),
                        self.Model.gmt_modified: func.now(),
                    },
                    synchronize_session=False,
                )
            )
        if affected == 0:
            return None
        return self.get_by_id(op_id)

    # ── internals ───────────────────────────────────────────────────────
    def _cas_update(
        self,
        op_id: int,
        values: Dict[Any, Any],
        *,
        allowed_sources: Optional[List[str]] = None,
        forbidden_sources: Optional[List[str]] = None,
    ) -> Optional[PublishOperationRecord]:
        """One optimistic-lock UPDATE guarded on the current ``state``.

        ``allowed_sources`` — the update matches only these states.
        ``forbidden_sources`` — the update matches any state NOT in this set.
        Exactly one of the two is given. Returns the updated record, or ``None``
        when no row matched the guard (lost the CAS)."""
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(self.Model.id == op_id)
            if allowed_sources is not None:
                query = query.filter(self.Model.state.in_(allowed_sources))
            if forbidden_sources is not None:
                query = query.filter(self.Model.state.notin_(forbidden_sources))
            affected = query.update(values, synchronize_session=False)
        if affected == 0:
            return None
        return self.get_by_id(op_id)
