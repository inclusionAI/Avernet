"""Shared persistence and hydration for the complete task execution graph."""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

from injector import inject
from sqlalchemy import and_, or_

from agentclaw.community.core.repository.implementations.task.task_graph_bbs_overview import (
    TaskGraphBbsOverviewMixin,
)
from agentclaw.community.core.repository.protocols.task import TaskGraphRepositoryProtocol
from agentclaw.community.core.task.domain.errors import GraphVersionConflictError, TaskStateError
from agentclaw.community.core.task.domain.models import (
    NodeActionEvent,
    Relation,
    RelationType,
    Status,
)
from agentclaw.community.core.task.repository.models import (
    TaskDeliveryIntentModel,
    TaskInfoModel,
    TaskIntakeSessionModel,
    TaskNodeModel,
    TaskNodeRelationModel,
    TaskNodeRunInfoModel,
)
from agentclaw.community.core.task.repository.serializers import (
    graph_from_parts,
    runtime_from_dict,
    task_spec_to_dict,
)
from agentclaw.community.utils.app_utils import get_current_executor_app
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation
from agentclaw.community.plugin_api.database import DatabasePlugin


_GRAPH_STATUS_KEY = "__graph_status"
# Hard cap for one durable handoff's retry storm. This is a safety net for
# unknown graph/status reconciliation bugs; legitimate retryable failures
# also mark the intent EXHAUSTED after this many delivery attempts.
_MAX_DELIVERY_ATTEMPTS = 100
_DELIVERY_EXHAUSTED_STATE = "EXHAUSTED"

_TERMINAL = {Status.DONE, Status.SUCCESS, Status.FAILED, Status.HUNG, Status.CANCELLED}


class TaskGraphRepository(TaskGraphBbsOverviewMixin, TaskGraphRepositoryProtocol):
    """Aggregate repository over the existing task graph tables.

    The repository deliberately owns one transaction for all current-state
    tables. Action history is written through the separate action-log model in
    the same transaction, but is never joined into normal graph reads.
    """

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._instance_id = os.environ.get("HOSTNAME") or f"pid-{os.getpid()}"

    def _transaction(self):
        method = getattr(self._db, "transactional_orm_session", None)
        return method() if method is not None else self._db.orm_session()

    @staticmethod
    def _json(value: Any) -> str | None:
        return json.dumps(value, ensure_ascii=False) if value is not None else None

    @staticmethod
    def _persistable_extend_props(props: dict[str, Any]) -> dict[str, Any]:
        """Make run_info.extend_props JSON-serializable for persistence without
        mutating the live node. ``pending_group_formation`` carries a live
        GroupFormation from dispatch to _drain; serialize it to a dict here and
        restore via ``_hydrated_extend_props`` on load (cross-instance round-trip)."""
        if not props:
            return props
        gf = props.get("pending_group_formation")
        if isinstance(gf, GroupFormation):
            persisted = dict(props)
            persisted["pending_group_formation"] = gf.to_dict()
            return persisted
        return props

    @staticmethod
    def _hydrated_extend_props(props: dict[str, Any]) -> dict[str, Any]:
        """Inverse of ``_persistable_extend_props``: restore a serialized
        GroupFormation so downstream attribute access (gf.collab_mode / bot_ids /
        extend_props / form_coop_group) works on a graph loaded from the store."""
        if not props:
            return props
        gf = props.get("pending_group_formation")
        if isinstance(gf, dict):
            restored = dict(props)
            restored["pending_group_formation"] = GroupFormation.from_dict(gf)
            return restored
        return props

    @staticmethod
    def _graph_status(info: TaskInfoModel) -> Status:
        props = json.loads(info.graph_extend_props) if info.graph_extend_props else {}
        fallback = info.status.value if isinstance(info.status, Status) else str(info.status)
        raw = props.get(_GRAPH_STATUS_KEY, fallback)
        return Status(raw)

    @staticmethod
    def _require_environment_scope(info: TaskInfoModel) -> None:
        """Environment-only read scope for presentation and diagnostic queries."""
        env = get_current_env()
        if info.env != env:
            raise TaskStateError(
                f"task environment mismatch: task_id={info.task_id}, "
                f"task={info.env}, current={env}"
            )

    @staticmethod
    def _require_task_scope(info: TaskInfoModel) -> None:
        TaskGraphRepository._require_environment_scope(info)
        executor_app = get_current_executor_app()
        if info.executor_app != executor_app:
            raise TaskStateError(
                f"task executor app mismatch: task_id={info.task_id}, "
                f"task={info.executor_app}, current={executor_app}"
            )

    @staticmethod
    def _require_intent_scope(intent: TaskDeliveryIntentModel) -> None:
        env = get_current_env()
        if intent.env != env:
            raise TaskStateError(
                f"delivery intent environment mismatch: "
                f"task_id={intent.task_id}, node_id={intent.node_id}, "
                f"intent={intent.env}, current={env}"
            )
        executor_app = get_current_executor_app()
        if intent.executor_app != executor_app:
            raise TaskStateError(
                "delivery intent executor app mismatch: "
                f"task_id={intent.task_id}, node_id={intent.node_id}, "
                f"intent={intent.executor_app}, current={executor_app}"
            )

    def _require_task_scope_for_id(self, db, task_id: str) -> bool:
        info = db.query(TaskInfoModel).filter(TaskInfoModel.task_id == task_id).first()
        if info is None:
            return False
        self._require_task_scope(info)
        return True

    def _require_environment_scope_for_id(self, db, task_id: str) -> bool:
        info = db.query(TaskInfoModel).filter(TaskInfoModel.task_id == task_id).first()
        if info is None:
            return False
        self._require_environment_scope(info)
        return True

    def load_graph(self, task_id: str):
        with self._db.orm_session() as db:
            info = db.query(TaskInfoModel).filter(TaskInfoModel.task_id == task_id).first()
            if info is None:
                return None
            self._require_environment_scope(info)
            node_rows = (
                db.query(TaskNodeModel)
                .filter(
                    TaskNodeModel.task_id == task_id,
                    TaskNodeModel.is_deleted.is_(False),
                )
                .order_by(TaskNodeModel.id.asc())
                .all()
            )
            run_rows = (
                db.query(TaskNodeRunInfoModel)
                .filter(TaskNodeRunInfoModel.task_id == task_id)
                .order_by(TaskNodeRunInfoModel.node_id.asc(), TaskNodeRunInfoModel.retry.desc())
                .all()
            )
            latest_runs = {}
            for row in run_rows:
                latest_runs.setdefault(row.node_id, row)
            relation_rows = (
                db.query(TaskNodeRelationModel)
                .filter(TaskNodeRelationModel.task_id == task_id)
                .order_by(TaskNodeRelationModel.id.asc())
                .all()
            )
            nodes = []
            for row in node_rows:
                runtime_row = latest_runs.get(row.node_id)
                runtime = runtime_from_dict({
                    "run_mode": runtime_row.run_mode if runtime_row else None,
                    "assignee": runtime_row.assignee if runtime_row else None,
                    "start_time": runtime_row.start_time if runtime_row else None,
                    "end_time": runtime_row.end_time if runtime_row else None,
                    "output": json.loads(runtime_row.output) if runtime_row and runtime_row.output else {},
                    "acceptance_result": (
                        json.loads(runtime_row.acceptance_result)
                        if runtime_row and runtime_row.acceptance_result else None
                    ),
                    "actual_goal": (
                        json.loads(runtime_row.actual_goal)
                        if runtime_row and runtime_row.actual_goal else None
                    ),
                    "extend_props": self._hydrated_extend_props(
                        json.loads(runtime_row.extend_props)
                        if runtime_row and runtime_row.extend_props else {}
                    ),
                })
                nodes.append((row.node_id, Status(row.status), json.loads(row.task_spec), runtime))
            relations = [
                Relation(
                    src_id=row.src_node_id,
                    dst_id=row.dst_node_id,
                    type=RelationType(row.relation_type),
                    extend_props=json.loads(row.extend_props) if row.extend_props else {},
                )
                for row in relation_rows
            ]
            return graph_from_parts(
                task_id=task_id,
                run_id=info.graph_run_id,
                loop_round=info.graph_loop_round,
                status=self._graph_status(info),
                output=json.loads(info.graph_output) if info.graph_output else {},
                extend_props={
                    key: value
                    for key, value in (
                        json.loads(info.graph_extend_props)
                        if info.graph_extend_props else {}
                    ).items()
                    if key != _GRAPH_STATUS_KEY
                },
                nodes=nodes,
                relations=relations,
            )

    def create_graph(self, graph, *, runtime_status: Status):
        with self._transaction() as db:
            info = db.query(TaskInfoModel).filter(TaskInfoModel.task_id == graph.task_id).with_for_update().first()
            if info is None:
                # Lightweight in-memory/test callers may initialize a graph
                # without a task_info row. Production execute persists the row
                # first, so this is a compatibility no-op for that isolated path.
                return 0
            self._require_task_scope(info)
            props = dict(graph.extend_props)
            props[_GRAPH_STATUS_KEY] = graph.status.value
            info.status = runtime_status.value
            info.graph_run_id = str(graph.run_id)
            info.graph_loop_round = graph.loop_round
            info.graph_output = self._json(graph.output)
            info.graph_extend_props = self._json(props)
            info.graph_version = max(int(info.graph_version or 0), 1)
            self._upsert_current_rows(db, graph)
            self._ensure_relay_delivery_intents(db, graph)
            db.flush()
            return info.graph_version

    def create_intake_graph(self, task_info, graph, session_id: str) -> int:
        """One transaction commits the confirmed source and root graph.

        The unique ``intake_session_id`` index arbitrates concurrent /start
        attempts. A Relay root is executed in the already-open intake Bot
        conversation, so only successor nodes own Backend delivery intents.
        """
        with self._transaction() as db:
            intake = db.get(TaskIntakeSessionModel, session_id)
            if intake is None or intake.status != "CONFIRMED":
                raise TaskStateError("intake session is not confirmed")
            if intake.owner_user_id != task_info.owner_user_id:
                raise TaskStateError("intake owner mismatch")
            props = dict(graph.extend_props)
            props[_GRAPH_STATUS_KEY] = graph.status.value
            db.add(TaskInfoModel(
                task_id=task_info.task_id,
                intake_session_id=session_id,
                source_type=task_info.source_type,
                owner_user_id=task_info.owner_user_id,
                owner_bot_id=task_info.owner_bot_id,
                execution_config=self._json(task_info.execution_config),
                task_spec=self._json(task_info.task_spec.to_dict()),
                status=Status.RUNNING.value,
                env=get_current_env(),
                executor_app=get_current_executor_app(),
                graph_run_id=str(graph.run_id),
                graph_loop_round=graph.loop_round,
                graph_output=self._json(graph.output),
                graph_extend_props=self._json(props),
                graph_version=1,
            ))
            db.flush()
            self._upsert_current_rows(db, graph)
            db.flush()
        return 1

    def save_graph(
        self,
        graph,
        *,
        expected_version: int,
        runtime_status: Status,
        action_events: list[NodeActionEvent],
        instance_id: str | None = None,
        callback_audit=None,
        published_artifacts: list | None = None,
    ) -> int:
        with self._transaction() as db:
            info = db.query(TaskInfoModel).filter(TaskInfoModel.task_id == graph.task_id).with_for_update().first()
            if info is None:
                return expected_version
            self._require_task_scope(info)
            actual = int(info.graph_version or 0)
            if actual != expected_version:
                raise GraphVersionConflictError(
                    f"task_id={graph.task_id} expected version={expected_version}, actual={actual}"
                )
            props = dict(graph.extend_props)
            props[_GRAPH_STATUS_KEY] = graph.status.value
            info.status = runtime_status.value
            info.graph_run_id = str(graph.run_id)
            info.graph_loop_round = graph.loop_round
            info.graph_output = self._json(graph.output)
            info.graph_extend_props = self._json(props)
            info.graph_version = actual + 1
            info.lease_owner = instance_id or self._instance_id
            info.heartbeat_at = int(time.time() * 1000)
            self._upsert_current_rows(db, graph)
            self._ensure_relay_delivery_intents(db, graph)
            self._insert_action_rows(db, graph.task_id, action_events, instance_id)
            if callback_audit is not None:
                self._upsert_callback_audit(db, callback_audit)
            if published_artifacts:
                self._insert_published_artifacts(db, published_artifacts)
            db.flush()
            return info.graph_version

    def _insert_published_artifacts(self, db, records) -> None:
        """Append the immutable artifact manifest rows inside the current
        transaction (spec 2026-10-20-task-artifact-domain §5.2/§5.3).

        select-first dedupe on ``(task_id, node_id, attempt, kind, content_hash)``
        absorbs version-conflict replays; any NULL key short-circuits the lookup
        (SQL NULL never participates in equality — a tolerance row just inserts,
        it is never "matched"). Write failures propagate: the whole graph
        mutation rolls back, no swallowing, no downgrade."""
        from datetime import datetime

        from agentclaw.community.core.task.repository.models import TaskArtifactModel
        from agentclaw.community.core.task.task_context.task_artifact import (
            content_to_json,
        )

        for record in records:
            keys = (
                record.task_id,
                record.node_id,
                record.attempt,
                record.kind,
                record.content_hash,
            )
            if all(key is not None for key in keys):
                existing = (
                    db.query(TaskArtifactModel)
                    .filter(
                        TaskArtifactModel.task_id == record.task_id,
                        TaskArtifactModel.node_id == record.node_id,
                        TaskArtifactModel.attempt == record.attempt,
                        TaskArtifactModel.kind == record.kind,
                        TaskArtifactModel.content_hash == record.content_hash,
                    )
                    .first()
                )
                if existing is not None:
                    continue
            moment = datetime.fromtimestamp(record.created_at / 1000)
            db.add(TaskArtifactModel(
                artifact_id=record.artifact_id,
                artifact_name=record.artifact_name,
                task_id=record.task_id,
                node_id=record.node_id,
                attempt=record.attempt,
                scope_session_id=record.scope_session_id,
                kind=record.kind,
                storage=record.storage,
                content=content_to_json(record.content),
                content_hash=record.content_hash,
                creator=record.creator,
                modifier=record.creator,  # 行不可变:modifier 恒等于 creator
                gmt_create=moment,
                gmt_modify=moment,
            ))

    def _ensure_relay_delivery_intents(self, db, graph) -> None:
        if not graph.is_relay:
            return
        for node in graph.tasks:
            if node.run_info.extend_props.get("relay_root"):
                # The first baton stays in the conversation where the user
                # confirmed the task. Dispatching it through Runner would create
                # a second session (or permission-solving collaboration group).
                continue
            if node.status not in {Status.PENDING, Status.RUNNING}:
                continue
            if node.run_info.run_mode not in {"single_bot", "coop_group", "bbs"}:
                continue
            if node.run_info.run_mode == "bbs" and node.status == Status.RUNNING:
                # BBS claim marks the node RUNNING and immediately sends the
                # winner its instruction inside the existing publish operation.
                # A claimed BBS node is therefore delivered; keep the durable
                # row for run-generation identity, but settle it so the redriver
                # cannot redispatch a completed auction later.
                if node.run_info.extend_props.get("bbs_owner"):
                    relay_token = node.run_info.extend_props.get("relay_token")
                    existing = db.query(TaskDeliveryIntentModel).filter(
                        TaskDeliveryIntentModel.task_id == graph.task_id,
                        TaskDeliveryIntentModel.node_id == node.node_id,
                    ).with_for_update().first()
                    now = int(time.time() * 1000)
                    has_active_lease = (
                        existing is not None
                        and existing.state == "DELIVERING"
                        and existing.lease_until_ms is not None
                        and existing.lease_until_ms >= now
                    )
                    if existing is not None and relay_token and not has_active_lease:
                        existing.relay_token = relay_token
                        existing.state = "DELIVERED"
                        existing.lease_until_ms = None
                        existing.lease_token = None
                        existing.last_error = None
                continue
            if (
                node.run_info.run_mode != "bbs"
                and not node.run_info.assignee
                and not (
                    node.run_info.run_mode == "coop_group"
                    and node.run_info.extend_props.get("pending_group_formation")
                )
            ):
                continue
            relay_token = node.run_info.extend_props.get("relay_token")
            if not relay_token:
                continue
            existing = db.query(TaskDeliveryIntentModel).filter(
                TaskDeliveryIntentModel.task_id == graph.task_id,
                TaskDeliveryIntentModel.node_id == node.node_id,
            ).with_for_update().first()
            if existing is None:
                db.add(TaskDeliveryIntentModel(
                    task_id=graph.task_id, node_id=node.node_id,
                    relay_token=relay_token,
                    env=get_current_env(),
                    executor_app=get_current_executor_app(),
                    state="PENDING", attempt=0,
                ))
            elif existing.relay_token != relay_token:
                # A retry or reassignment is a new run generation of the same
                # node. The old delivery lease may no longer acknowledge it.
                existing.relay_token = relay_token
                existing.state = "PENDING"
                existing.lease_until_ms = None
                existing.lease_token = None
                existing.last_error = None

    def list_pending_delivery_intents(self, *, limit: int = 100) -> list[tuple[str, str]]:
        now = int(time.time() * 1000)
        with self._db.orm_session() as db:
            rows = (
                db.query(TaskDeliveryIntentModel.task_id, TaskDeliveryIntentModel.node_id)
                .filter(
                    TaskDeliveryIntentModel.env == get_current_env(),
                    TaskDeliveryIntentModel.executor_app == get_current_executor_app(),
                    or_(
                        and_(
                            TaskDeliveryIntentModel.state == "PENDING",
                            TaskDeliveryIntentModel.attempt < _MAX_DELIVERY_ATTEMPTS,
                        ),
                        and_(
                            TaskDeliveryIntentModel.state == "DELIVERING",
                            TaskDeliveryIntentModel.lease_until_ms < now,
                            TaskDeliveryIntentModel.attempt < _MAX_DELIVERY_ATTEMPTS,
                        ),
                    ),
                )
                .order_by(TaskDeliveryIntentModel.id.asc())
                .limit(max(1, min(limit, 500))).all()
            )
            return [(str(row[0]), str(row[1])) for row in rows]

    def claim_delivery_intent(
        self, task_id: str, node_id: str, relay_token: str, *, lease_ms: int = 60000,
    ) -> tuple[str, str | None]:
        now = int(time.time() * 1000)
        token = uuid.uuid4().hex
        with self._transaction() as db:
            info = db.query(TaskInfoModel).filter(TaskInfoModel.task_id == task_id).first()
            if info is not None:
                self._require_task_scope(info)
            row = (
                db.query(TaskDeliveryIntentModel)
                .filter(
                    TaskDeliveryIntentModel.task_id == task_id,
                    TaskDeliveryIntentModel.node_id == node_id,
                )
                .with_for_update()
                .first()
            )
            if row is None:
                return "MISSING", None
            self._require_intent_scope(row)
            if row.relay_token != relay_token:
                return "STALE", None
            if row.state == _DELIVERY_EXHAUSTED_STATE:
                return row.state, None
            if row.attempt >= _MAX_DELIVERY_ATTEMPTS and row.state != "DELIVERED":
                row.state = _DELIVERY_EXHAUSTED_STATE
                db.flush()
                return row.state, None
            if not (
                row.state == "PENDING"
                or (row.state == "DELIVERING" and row.lease_until_ms is not None and row.lease_until_ms < now)
            ):
                return row.state, None
            row.state = "DELIVERING"
            row.attempt += 1
            row.lease_until_ms = now + lease_ms
            row.lease_token = token
            db.flush()
            return "CLAIMED", token

    def mark_delivery_intent(self, task_id: str, node_id: str, lease_token: str, *, delivered: bool,
                             error: str | None = None) -> None:
        with self._transaction() as db:
            row = (
                db.query(TaskDeliveryIntentModel)
                .filter(
                    TaskDeliveryIntentModel.task_id == task_id,
                    TaskDeliveryIntentModel.node_id == node_id,
                    TaskDeliveryIntentModel.state == "DELIVERING",
                    TaskDeliveryIntentModel.lease_token == lease_token,
                )
                .with_for_update()
                .first()
            )
            if row is None:
                raise TaskStateError("delivery intent is not owned by this attempt")
            self._require_intent_scope(row)
            row.state = (
                "DELIVERED" if delivered
                else "PENDING" if row.attempt < _MAX_DELIVERY_ATTEMPTS
                else _DELIVERY_EXHAUSTED_STATE
            )
            row.lease_until_ms = None
            row.lease_token = None
            row.last_error = None if delivered else str(error or "delivery_failed")[:1000]
            db.flush()

    def requeue_delivery_intent(
        self, task_id: str, node_id: str, relay_token: str, *, reason: str,
    ) -> None:
        """Reopen the same run generation after a delivered instruction stalled."""
        with self._transaction() as db:
            row = (
                db.query(TaskDeliveryIntentModel)
                .filter(
                    TaskDeliveryIntentModel.task_id == task_id,
                    TaskDeliveryIntentModel.node_id == node_id,
                    TaskDeliveryIntentModel.relay_token == relay_token,
                )
                .with_for_update()
                .first()
            )
            if row is None:
                raise TaskStateError("delivery intent cannot be requeued")
            self._require_intent_scope(row)
            row.state = "PENDING"
            row.lease_until_ms = None
            row.lease_token = None
            row.last_error = str(reason or "relay_resume")[:1000]
            db.flush()

    def settle_delivery_intent(
        self, task_id: str, node_id: str, relay_token: str,
    ) -> bool:
        """Settle a stale intent without redispatching an already-finished node."""
        with self._transaction() as db:
            row = (
                db.query(TaskDeliveryIntentModel)
                .filter(
                    TaskDeliveryIntentModel.task_id == task_id,
                    TaskDeliveryIntentModel.node_id == node_id,
                    TaskDeliveryIntentModel.relay_token == relay_token,
                )
                .with_for_update()
                .first()
            )
            if row is None:
                return False
            self._require_intent_scope(row)
            if row.state == "DELIVERED":
                return True
            row.state = "DELIVERED"
            row.lease_until_ms = None
            row.lease_token = None
            row.last_error = None
            return True

    def _upsert_callback_audit(self, db, record) -> None:
        """Write the inbound callback audit row with ``process_status='PROCESSED'``
        inside the current transaction (same commit as the graph mutation).

        Idempotency: any row already carrying ``record.event_id`` is treated as
        already-processed and left as-is (the preceding idempotency check should
        have short-circuited replay; this is the hard guard). Otherwise the row
        keyed by ``(run_id, node_id)`` is refreshed with the audit payload + the
        event id / process status."""
        from datetime import datetime

        from agentclaw.community.core.task.repository.models import TaskCallbackModel

        if getattr(record, "event_id", None):
            existing_event = (
                db.query(TaskCallbackModel)
                .filter(TaskCallbackModel.event_id == record.event_id)
                .first()
            )
            if existing_event is not None:
                # Already processed (or concurrent winner); do not mutate further.
                return
        row = (
            db.query(TaskCallbackModel)
            .filter(
                TaskCallbackModel.run_id == record.run_id,
                TaskCallbackModel.node_id == record.node_id,
            )
            .first()
        )
        if row is None:
            row = TaskCallbackModel(
                invoker=record.invoker,
                run_id=record.run_id,
                node_id=record.node_id,
                main_session_id=record.main_session_id,
                orig_callback_data=record.orig_callback_data,
            )
            db.add(row)
        row.status = record.status
        row.orig_callback_data = record.orig_callback_data
        row.execution_graph = self._json(record.execution_graph) if record.execution_graph is not None else None
        row.result = self._json(record.result) if record.result is not None else None
        row.result_success = record.result_success
        row.exec_error = record.exec_error
        row.extend_props = self._json(record.extend_props) if record.extend_props is not None else None
        if getattr(record, "event_id", None):
            row.event_id = record.event_id
        row.process_status = "PROCESSED"
        row.processed_at = datetime.utcnow()

    def _upsert_current_rows(self, db, graph) -> None:
        current_node_ids = {node.node_id for node in graph.tasks}
        for row in (
            db.query(TaskNodeModel)
            .filter(TaskNodeModel.task_id == graph.task_id)
            .all()
        ):
            if row.node_id not in current_node_ids:
                # Logical delete: retain the node row for audit/history and keep
                # task_node_run_info linked to the original execution attempt.
                row.is_deleted = True
        current_relation_keys = {(rel.src_id, rel.dst_id) for rel in graph.relations}
        for row in (
            db.query(TaskNodeRelationModel)
            .filter(TaskNodeRelationModel.task_id == graph.task_id)
            .all()
        ):
            if (row.src_node_id, row.dst_node_id) not in current_relation_keys:
                db.delete(row)
        for node in graph.tasks:
            row = (
                db.query(TaskNodeModel)
                .filter(
                    TaskNodeModel.task_id == graph.task_id,
                    TaskNodeModel.node_id == node.node_id,
                )
                .first()
            )
            if row is None:
                row = TaskNodeModel(task_id=graph.task_id, node_id=node.node_id)
                db.add(row)
            row.task_spec = json.dumps(task_spec_to_dict(node.task_spec), ensure_ascii=False)
            row.status = node.status.value
            row.is_deleted = False
            retry = int(node.run_info.extend_props.get("retry", 0))
            run_row = (
                db.query(TaskNodeRunInfoModel)
                .filter(
                    TaskNodeRunInfoModel.task_id == graph.task_id,
                    TaskNodeRunInfoModel.node_id == node.node_id,
                    TaskNodeRunInfoModel.retry == retry,
                )
                .first()
            )
            if run_row is None:
                run_row = TaskNodeRunInfoModel(
                    task_id=graph.task_id,
                    node_id=node.node_id,
                    retry=retry,
                )
                db.add(run_row)
            run_row.run_mode = node.run_info.run_mode
            run_row.assignee = node.run_info.assignee
            run_row.output = self._json(node.run_info.output)
            actual_goal = node.run_info.actual_goal
            run_row.actual_goal = self._json(
                actual_goal.to_dict() if actual_goal is not None else None
            )
            acceptance = node.run_info.acceptance_result
            run_row.acceptance_result = self._json(
                {
                    "verdict": acceptance.verdict.value,
                    "done_items": list(acceptance.done_items),
                    "gap_items": list(acceptance.gap_items),
                }
                if acceptance is not None
                else None
            )
            run_row.session_id = node.run_info.extend_props.get("session_id")
            run_row.extend_props = self._json(self._persistable_extend_props(node.run_info.extend_props))
            run_row.start_time = node.run_info.start_time
            run_row.end_time = node.run_info.end_time
            run_row.update_time = int(time.time() * 1000)
        existing = {
            (row.src_node_id, row.dst_node_id)
            for row in db.query(TaskNodeRelationModel)
            .filter(TaskNodeRelationModel.task_id == graph.task_id)
            .all()
        }
        for relation in graph.relations:
            key = (relation.src_id, relation.dst_id)
            if key in existing:
                continue
            db.add(TaskNodeRelationModel(
                task_id=graph.task_id,
                src_node_id=relation.src_id,
                dst_node_id=relation.dst_id,
                relation_type=relation.type.value,
                extend_props=self._json(relation.extend_props),
            ))

    def _insert_action_rows(self, db, task_id: str, events: list[NodeActionEvent], instance_id: str | None) -> None:
        if not events:
            return
        from agentclaw.community.core.task.repository.models import TaskActionLogModel

        for event in events:
            db.add(TaskActionLogModel(
                event_id=f"{task_id}:{event.payload.get('__node_id') or task_id}:{event.seq}",
                task_id=task_id,
                node_id=str(event.payload.get("__node_id") or task_id),
                seq=event.seq,
                action=event.action.value,
                loop_round=event.loop_round,
                attempt=event.attempt,
                status_from=event.status_from.value if event.status_from else None,
                status_to=event.status_to.value if event.status_to else None,
                payload=json.dumps(event.payload, ensure_ascii=False),
                instance_id=instance_id or self._instance_id,
            ))

    def get_version(self, task_id: str) -> int | None:
        with self._db.orm_session() as db:
            row = db.query(TaskInfoModel).filter(TaskInfoModel.task_id == task_id).first()
            if row is None:
                return None
            self._require_environment_scope(row)
            return int(row.graph_version or 0)

    def next_action_seq(self, task_id: str, node_id: str) -> int:
        from agentclaw.community.core.task.repository.models import TaskActionLogModel

        with self._db.orm_session() as db:
            self._require_task_scope_for_id(db, task_id)
            row = (
                db.query(TaskActionLogModel.seq)
                .filter(
                    TaskActionLogModel.task_id == task_id,
                    TaskActionLogModel.node_id == node_id,
                )
                .order_by(TaskActionLogModel.seq.desc())
                .first()
            )
            return int(row[0]) + 1 if row is not None else 1

    def load_action_logs(
        self,
        task_id: str,
        *,
        node_id: str | None = None,
        limit: int = 200,
    ) -> dict[str, list]:
        from agentclaw.community.core.task.repository.models import TaskActionLogModel
        from agentclaw.community.core.task.repository.serializers import action_from_dict

        limit = max(1, min(limit, 200))
        with self._db.orm_session() as db:
            self._require_environment_scope_for_id(db, task_id)
            query = db.query(TaskActionLogModel).filter(TaskActionLogModel.task_id == task_id)
            if node_id is not None:
                query = query.filter(TaskActionLogModel.node_id == node_id)
            rows = (
                query.order_by(TaskActionLogModel.node_id.asc(), TaskActionLogModel.seq.asc())
                .limit(limit)
                .all()
            )
            grouped: dict[str, list] = {}
            for row in rows:
                grouped.setdefault(row.node_id, []).append(
                    action_from_dict({
                        "seq": row.seq,
                        "action": row.action,
                        "loop_round": row.loop_round,
                        "attempt": row.attempt,
                        "status_from": row.status_from,
                        "status_to": row.status_to,
                        "payload": json.loads(row.payload),
                        "ts": int(row.gmt_create.timestamp() * 1000) if row.gmt_create else 0,
                    })
                )
            return grouped

    def list_recoverable(self, *, limit: int = 100) -> list[str]:
        now = int(time.time() * 1000)
        with self._db.orm_session() as db:
            rows = (
                db.query(TaskInfoModel.task_id)
                .filter(
                    TaskInfoModel.env == get_current_env(),
                    TaskInfoModel.executor_app == get_current_executor_app(),
                    TaskInfoModel.status.in_(
                        [Status.PENDING.value, Status.PLANNING.value, Status.RUNNING.value]
                    ),
                )
                .filter((TaskInfoModel.lease_until.is_(None)) | (TaskInfoModel.lease_until < now))
                .order_by(TaskInfoModel.gmt_modified.asc())
                .limit(max(1, min(limit, 100)))
                .all()
            )
            return [row[0] for row in rows]

    def heartbeat(self, task_id: str, *, instance_id: str, lease_seconds: int) -> bool:
        now = int(time.time() * 1000)
        until = now + lease_seconds * 1000
        with self._transaction() as db:
            row = (
                db.query(TaskInfoModel)
                .filter(
                    TaskInfoModel.task_id == task_id,
                    TaskInfoModel.lease_owner == instance_id,
                )
                .with_for_update()
                .first()
            )
            if row is None:
                return False
            self._require_task_scope(row)
            row.lease_until = until
            row.heartbeat_at = now
            db.flush()
            return True

    def release_lease(self, task_id: str, *, instance_id: str) -> bool:
        with self._transaction() as db:
            row = (
                db.query(TaskInfoModel)
                .filter(
                    TaskInfoModel.task_id == task_id,
                    TaskInfoModel.lease_owner == instance_id,
                )
                .with_for_update()
                .first()
            )
            if row is None:
                return False
            self._require_task_scope(row)
            row.lease_owner = None
            row.lease_until = None
            db.flush()
            return True

    def acquire_lease(self, task_id: str, *, instance_id: str, lease_seconds: int) -> bool:
        now = int(time.time() * 1000)
        until = now + lease_seconds * 1000
        with self._transaction() as db:
            row = (
                db.query(TaskInfoModel)
                .filter(TaskInfoModel.task_id == task_id)
                .filter((TaskInfoModel.lease_until.is_(None)) | (TaskInfoModel.lease_until < now))
                .with_for_update()
                .first()
            )
            if row is None:
                return False
            self._require_task_scope(row)
            row.lease_owner = instance_id
            row.lease_until = until
            row.heartbeat_at = now
            db.flush()
            return True

    def claim_bbs_owner(self, task_id: str, bot_id: str) -> bool:
        """Cross-instance BBS relay claim via a row lock on the root runtime row.

        SELECT ... FOR UPDATE serializes concurrent claimers on OceanBase: the
        second claimer blocks until the first commits, then reads the new
        ``bbs_owner`` and loses. SQLite ignores ``FOR UPDATE`` (single-instance),
        so the in-process lock + this conditional check remain authoritative
        there. The claim lives in the root ``task_node_run_info.extend_props``
        JSON (where ``load_graph`` restores it and existing reads expect it);
        no JSON-level atomic CAS is required because the row lock guards the
        read-modify-write window.
        """
        now = int(time.time() * 1000)
        with self._transaction() as db:
            self._require_task_scope_for_id(db, task_id)
            run_row = (
                db.query(TaskNodeRunInfoModel)
                .filter(
                    TaskNodeRunInfoModel.task_id == task_id,
                    TaskNodeRunInfoModel.node_id == task_id,
                )
                .order_by(TaskNodeRunInfoModel.retry.desc())
                .with_for_update()
                .first()
            )
            if run_row is None:
                return False
            props = json.loads(run_row.extend_props) if run_row.extend_props else {}
            owner = props.get("bbs_owner")
            if owner is not None and owner != bot_id:
                return False
            props["bbs_owner"] = bot_id
            props["bbs_claim_at"] = now
            run_row.extend_props = self._json(props)
            db.flush()
            return True

    def release_bbs_owner(self, task_id: str, bot_id: str) -> bool:
        """Clear the BBS relay claim held by ``bot_id`` (idempotent release)."""
        with self._transaction() as db:
            self._require_task_scope_for_id(db, task_id)
            run_row = (
                db.query(TaskNodeRunInfoModel)
                .filter(
                    TaskNodeRunInfoModel.task_id == task_id,
                    TaskNodeRunInfoModel.node_id == task_id,
                )
                .order_by(TaskNodeRunInfoModel.retry.desc())
                .with_for_update()
                .first()
            )
            if run_row is None:
                return False
            props = json.loads(run_row.extend_props) if run_row.extend_props else {}
            if props.get("bbs_owner") != bot_id:
                return False
            props.pop("bbs_owner", None)
            props.pop("bbs_claim_at", None)
            run_row.extend_props = self._json(props)
            db.flush()
            return True
