"""Shared persistence and hydration for the complete task execution graph."""
from __future__ import annotations

import json
import os
import time
from typing import Any

from injector import inject
from sqlalchemy import and_, case, func, or_

from agentclaw.community.core.repository.protocols.task import TaskGraphRepositoryProtocol
from agentclaw.community.core.task.domain.errors import GraphVersionConflictError
from agentclaw.community.core.task.domain.models import (
    NodeActionEvent,
    Relation,
    RelationType,
    Status,
)
from agentclaw.community.core.task.repository.models import (
    TaskInfoModel,
    TaskNodeModel,
    TaskNodeRelationModel,
    TaskNodeRunInfoModel,
)
from agentclaw.community.core.task.repository.serializers import (
    graph_from_parts,
    runtime_from_dict,
    task_spec_to_dict,
)
from agentclaw.community.core.task.repository.types import BbsTaskOverviewRecord
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation
from agentclaw.community.plugin_api.database import DatabasePlugin


_GRAPH_STATUS_KEY = "__graph_status"
_TERMINAL = {Status.DONE, Status.SUCCESS, Status.FAILED, Status.HUNG, Status.CANCELLED}


class TaskGraphRepository(TaskGraphRepositoryProtocol):
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

    def load_graph(self, task_id: str):
        with self._db.orm_session() as db:
            info = db.query(TaskInfoModel).filter(TaskInfoModel.task_id == task_id).first()
            if info is None:
                return None
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
            props = dict(graph.extend_props)
            props[_GRAPH_STATUS_KEY] = graph.status.value
            info.status = runtime_status.value
            info.graph_run_id = str(graph.run_id)
            info.graph_loop_round = graph.loop_round
            info.graph_output = self._json(graph.output)
            info.graph_extend_props = self._json(props)
            info.graph_version = max(int(info.graph_version or 0), 1)
            self._upsert_current_rows(db, graph)
            db.flush()
            return info.graph_version

    def save_graph(
        self,
        graph,
        *,
        expected_version: int,
        runtime_status: Status,
        action_events: list[NodeActionEvent],
        instance_id: str | None = None,
        callback_audit=None,
    ) -> int:
        with self._transaction() as db:
            info = db.query(TaskInfoModel).filter(TaskInfoModel.task_id == graph.task_id).with_for_update().first()
            if info is None:
                return expected_version
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
            self._insert_action_rows(db, graph.task_id, action_events, instance_id)
            if callback_audit is not None:
                self._upsert_callback_audit(db, callback_audit)
            db.flush()
            return info.graph_version

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
            acceptance = node.run_info.acceptance_result
            run_row.acceptance_result = self._json(
                {
                    "verdict": acceptance.verdict.value,
                    "acceptances_metric": list(acceptance.acceptances_metric),
                    "gaps": list(acceptance.gaps),
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
            row = db.query(TaskInfoModel.graph_version).filter(TaskInfoModel.task_id == task_id).first()
            return int(row[0]) if row is not None else None

    def next_action_seq(self, task_id: str, node_id: str) -> int:
        from agentclaw.community.core.task.repository.models import TaskActionLogModel

        with self._db.orm_session() as db:
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
                .filter(TaskInfoModel.status.in_([Status.PENDING.value, Status.PLANNING.value, Status.RUNNING.value]))
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
            count = (
                db.query(TaskInfoModel)
                .filter(
                    TaskInfoModel.task_id == task_id,
                    TaskInfoModel.lease_owner == instance_id,
                )
                .update({
                    TaskInfoModel.lease_until: until,
                    TaskInfoModel.heartbeat_at: now,
                }, synchronize_session=False)
            )
            return count == 1

    def release_lease(self, task_id: str, *, instance_id: str) -> bool:
        with self._transaction() as db:
            count = (
                db.query(TaskInfoModel)
                .filter(
                    TaskInfoModel.task_id == task_id,
                    TaskInfoModel.lease_owner == instance_id,
                )
                .update({
                    TaskInfoModel.lease_owner: None,
                    TaskInfoModel.lease_until: None,
                }, synchronize_session=False)
            )
            return count == 1

    def acquire_lease(self, task_id: str, *, instance_id: str, lease_seconds: int) -> bool:
        now = int(time.time() * 1000)
        until = now + lease_seconds * 1000
        with self._transaction() as db:
            count = (
                db.query(TaskInfoModel)
                .filter(TaskInfoModel.task_id == task_id)
                .filter((TaskInfoModel.lease_until.is_(None)) | (TaskInfoModel.lease_until < now))
                .update({
                    TaskInfoModel.lease_owner: instance_id,
                    TaskInfoModel.lease_until: until,
                    TaskInfoModel.heartbeat_at: now,
                }, synchronize_session=False)
            )
            return count == 1

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

    def list_bbs_tasks_overview(
        self,
        page: int = 1,
        page_size: int = 20,
        *,
        search_word: str | None = None,
        status: str | None = None,
    ) -> "tuple[list[BbsTaskOverviewRecord], int]":
        """列 BBS 接力任务的一页(1-based):``task_node_run_info`` ⋈ ``task_node``
        (task_id+node_id),再按 distinct task_id 批量补 ``task_info.owner_bot_id``→publisher(缺失→None)。

        BBS 判定(二选一 OR):① ``run_mode='bbs'``;② ``extend_props``(JSON)的 ``actual_run_mode='bbs'``
        (BBS 经理-员工群派发时 scoped 节点 ``run_mode='coop_group'`` 但 ``actual_run_mode='bbs'``)。
        只读投影;返回 ``(records, total)``——``total`` 为**过滤后**行数,``records`` 为当前页
        (按 ``task_node_run_info.id`` 降序(最新优先)稳定切片,LIMIT/OFFSET;页越界 → 空列表,``total`` 仍真实)。

        可选过滤(为 None 即不拼,退化为纯分页):``status``(单值,对 ``task_node.status`` 等值);
        ``search_word``(大小写不敏感模糊匹配 ``task_node.task_spec`` 或 ``task_node_run_info.extend_props``
        两列文本;``%``/``_`` 视作通配符,不做转义)。count 与分页两查询共用同一组 filters,保证 total 与页一致。

        ``task_spec``/``extend_props``/``acceptance_result`` 复用模型 ``to_record()`` 的 JSON 解析;
        title/goal/acceptances/assignee_name 由 adapter translator 二次解析(不在此 record 内)。
        """
        page = max(1, page)
        page_size = max(1, page_size)
        offset = (page - 1) * page_size
        join_clause = and_(
            TaskNodeRunInfoModel.task_id == TaskNodeModel.task_id,
            TaskNodeRunInfoModel.node_id == TaskNodeModel.node_id,
        )
        with self._db.orm_session() as db:
            # BBS 任务判定(二选一):① task_node_run_info.run_mode='bbs';
            # ② extend_props(JSON)的 actual_run_mode='bbs'。后者覆盖 BBS 经理-员工群派发——scoped
            # 节点 run_mode='coop_group' 但 extend_props.actual_run_mode='bbs'(见 bbs_modal_executor.notify
            # 落库),原 run_mode 单判会漏这批。跨方言 JSON 提取:json_valid 护栏(非 JSON/NULL → NULL);
            # 非 sqlite 需 json_unquote(MySQL JSON_EXTRACT 返回带引号标量,SQLite json_extract 已去引号)。
            dialect = db.get_bind().dialect.name
            actual_run_mode_val = case(
                (
                    func.json_valid(TaskNodeRunInfoModel.extend_props) == 1,
                    func.json_extract(TaskNodeRunInfoModel.extend_props, "$.actual_run_mode"),
                ),
                else_=None,
            )
            if dialect != "sqlite":
                actual_run_mode_val = func.json_unquote(actual_run_mode_val)
            filters: list = [
                or_(
                    TaskNodeRunInfoModel.run_mode == "bbs",
                    actual_run_mode_val == "bbs",
                ),
                TaskNodeModel.is_deleted.is_(False),
            ]
            if status is not None:
                filters.append(TaskNodeModel.status == status)
            if search_word is not None:
                pat = f"%{search_word.lower()}%"
                filters.append(
                    or_(
                        func.lower(TaskNodeModel.task_spec).like(pat),
                        func.lower(TaskNodeRunInfoModel.extend_props).like(pat),
                    )
                )
            total = (
                db.query(func.count(TaskNodeRunInfoModel.task_id))
                .join(TaskNodeModel, join_clause)
                .filter(*filters)
                .scalar()
            ) or 0

            joined = (
                db.query(TaskNodeRunInfoModel, TaskNodeModel)
                .join(TaskNodeModel, join_clause)
                .filter(*filters)
                .order_by(TaskNodeRunInfoModel.id.desc())
                .limit(page_size)
                .offset(offset)
                .all()
            )
            if not joined:
                return [], total

            # publisher:按 distinct task_id 一次 in_() 批查 task_info.owner_bot_id + owner_user_id,避免 N+1。
            task_ids = {run.task_id for run, _ in joined}
            publishers: dict[str, str] = {}
            owner_users: dict[str, str] = {}
            if task_ids:
                publisher_rows = (
                    db.query(
                        TaskInfoModel.task_id,
                        TaskInfoModel.owner_bot_id,
                        TaskInfoModel.owner_user_id,
                    )
                    .filter(TaskInfoModel.task_id.in_(task_ids))
                    .all()
                )
                publishers = {tid: oid for tid, oid, _ in publisher_rows if oid}
                owner_users = {tid: uid for tid, _, uid in publisher_rows if uid}

            records: list[BbsTaskOverviewRecord] = []
            for run, node in joined:
                run_rec = run.to_record()  # 已 JSON 解析 extend_props/acceptance_result
                node_rec = node.to_record()  # 已 JSON 解析 task_spec;status → Status
                records.append(
                    BbsTaskOverviewRecord(
                        task_id=run_rec.task_id,
                        node_id=run_rec.node_id,
                        run_mode=run_rec.run_mode,
                        retry=run_rec.retry,
                        assignee_id=run_rec.assignee,
                        status=node_rec.status,
                        acceptance_result=run_rec.acceptance_result,
                        extend_props=run_rec.extend_props,
                        relay_create_time=node_rec.gmt_create,
                        relay_begin_time=run_rec.gmt_create,
                        relay_end_time=run_rec.gmt_modified,
                        task_spec=node_rec.task_spec or {},
                        publisher=publishers.get(run_rec.task_id),
                        owner_user_id=owner_users.get(run_rec.task_id),
                        # publisher_name 由 TaskService._enrich_bbs_publisher_names 批量补(repo 不查 BotService)
                    )
                )
            return records, total
