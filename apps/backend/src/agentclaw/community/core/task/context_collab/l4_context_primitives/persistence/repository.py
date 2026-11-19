"""L4 SQL repository: actual creation, Task/Event CAS and read implementation.

No catalog, ProductInstance, Profile or Binding is imported here. Read codecs
validate optional deployment metadata at the storage boundary; creation is
self-contained and writes only Collet. Work.start can atomically create the
root Task, both creation/start Events and their Collet revisions. Explicit
Task creation also supports already-bound Collets without starting Work.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from typing import Generic, Sequence, TypeVar
from uuid import uuid4

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabParentEventError,
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.g2_keep_reliable_live.reliability import (
    ensure_expected_version,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    AppendEventRecordRequest,
    ColletCreateCommand,
    TaskCreateCommand,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participation import (
    initial_executor_refs,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.creation import (
    build_created_collet,
    build_created_work,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.models import (
    ContextCollabColletModel,
    ContextCollabEventModel,
    ContextCollabTaskModel,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.records import (
    CollaborationRecordDecoder,
    CreatedColletRecord,
    CreatedWorkRecord,
    InitialEventDraft,
    PrimitiveReadState,
    StoredEventAppend,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.row_codec import (
    _collet_from_row,
    _dump_json,
    _event_from_row,
    _event_row,
    _parent_event_exists,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.task_storage import (
    legacy_progress,
    load_tasks,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.service_ports import (
    CollaborationReadState,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import Task
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env
from sqlalchemy import func

StateT = TypeVar("StateT", bound=CollaborationReadState)


class ContextCollabRepository(Generic[StateT]):
    def __init__(
        self, db: DatabasePlugin, record_decoder: CollaborationRecordDecoder[StateT]
    ) -> None:
        self._db = db
        self._record_decoder = record_decoder

    def _transaction(self):
        transactional = getattr(self._db, "transactional_orm_session", None)
        return transactional() if transactional is not None else self._db.orm_session()

    def create_l4_collet(
        self, command: ColletCreateCommand, actor_ref: PrincipalRef
    ) -> CreatedColletRecord:
        record = build_created_collet(command, actor_ref)
        assert record.collet.metadata is not None
        with self._transaction() as db:
            db.add(
                self._collet_row(
                    record.collet, "{}", None, now=record.collet.metadata.created_at
                )
            )
            db.flush()
        return record

    def create_l4_task(
        self,
        collab_context_id: str,
        command: TaskCreateCommand,
        actor_ref: PrincipalRef,
    ) -> CreatedWorkRecord:
        """Create Work/evidence only on explicit request; all writes share one CAS."""
        with self._transaction() as db:
            row = (
                db.query(ContextCollabColletModel)
                .filter(
                    ContextCollabColletModel.collab_context_id == collab_context_id,
                    ContextCollabColletModel.env == get_current_env(),
                )
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                raise ContextCollabNotFoundError(
                    f"context {collab_context_id!r} not found"
                )
            ensure_expected_version(command.expected_version, int(row.context_version))
            collet = _collet_from_row(row)
            if actor_ref != collet.initiator_ref:
                raise ContextCollabValidationError(
                    "only the Collet initiator may create its Task"
                )
            existing, _ = load_tasks(db, row)
            if existing is not None:
                raise ContextCollabValidationError("root Task already exists")
            binding_rows = (
                db.query(ContextCollabEventModel)
                .filter(
                    ContextCollabEventModel.collab_context_id == collab_context_id,
                    ContextCollabEventModel.env == get_current_env(),
                )
                .order_by(ContextCollabEventModel.id.asc())
                .all()
            )
            executors = initial_executor_refs(
                [_event_from_row(event) for event in binding_rows]
            )
            facts = {
                "objective": command.objective,
                "executor_refs": [ref.to_dict() for ref in executors],
            }
            now = datetime.now(timezone.utc)
            updated_collet = replace(
                collet,
                version=collet.version + 1,
                metadata=replace(collet.metadata, updated_at=now, updated_by=actor_ref)
                if collet.metadata
                else None,
            )
            record = build_created_work(
                updated_collet,
                command.principal_ref,
                command.objective,
                InitialEventDraft("task.created", "Task", actor_ref, facts),
                command.completion_conditions,
                occurred_at=now,
            )
            record.task = replace(record.task, executor_refs=executors)
            db.add(self._task_row(record.task, now=now))
            db.add(self._event_row(record.initial_event, now=now))
            count = (
                db.query(ContextCollabColletModel)
                .filter(
                    ContextCollabColletModel.id == row.id,
                    ContextCollabColletModel.context_version
                    == command.expected_version,
                    ContextCollabColletModel.root_task_ref.is_(None),
                )
                .update(
                    {
                        "root_task_ref": record.task.task_id,
                        # Selection metadata is opaque to L4 and must survive
                        # explicit Task creation unchanged after instantiation.
                        "creation_facts": row.creation_facts
                        if json.loads(row.creation_facts)
                        else _dump_json(facts),
                        "context_version": updated_collet.version,
                        "gmt_modified": now,
                        "collet_metadata": _dump_json(updated_collet.metadata.to_dict())
                        if updated_collet.metadata
                        else None,
                    },
                    synchronize_session=False,
                )
            )
            if count != 1:
                raise ContextCollabVersionConflict("Task creation Collet CAS failed")
            db.flush()
            return record

    def persist_created(
        self, record: CreatedWorkRecord, creation_facts_json: str
    ) -> None:
        """Store one creation atomically; opaque JSON is an SQL column, not policy.

        Upper-layer creation can supply its own serialized metadata without
        changing L4 command/model contracts. This method never decodes it.
        """
        now = record.initial_event.occurred_at
        with self._transaction() as db:
            db.add(
                self._collet_row(
                    record.collet, creation_facts_json, record.task, now=now
                )
            )
            db.add(self._task_row(record.task, now=now))
            db.add(self._event_row(record.initial_event, now=now))
            db.flush()

    def append_event(
        self, collab_context_id: str, request: "AppendEventRecordRequest"
    ) -> StoredEventAppend:
        """Append an Event and fence Collet plus any resolved Work update.

        持锁事务:读 Collet → 校验其版本/parent → 加载 Task → Task CAS
        (Work 才推进)→ 插事件 → Collet CAS。Legacy Task 在同一事务物化。
        追加不设幂等键,事件顺序即插入顺序(自增 id),主干在查询时由
        活动链推导(spec 作者裁决)。``with_for_update()`` 在
        MySQL/OceanBase 上是行锁,SQLite 测试驱动上优雅降级为普通读,
        由 CAS WHERE 把关。任何写失败原样上抛,异常路径靠事务回滚
        保证不落半态。
        """
        with self._transaction() as db:
            row = (
                db.query(ContextCollabColletModel)
                .filter(
                    ContextCollabColletModel.collab_context_id == collab_context_id,
                    ContextCollabColletModel.env == get_current_env(),
                )
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                raise ContextCollabNotFoundError(
                    f"collaboration context {collab_context_id!r} not found"
                )
            ensure_expected_version(request.expected_version, int(row.context_version))
            if request.parent_event is not None and not _parent_event_exists(
                db, collab_context_id, request.parent_event
            ):
                raise ContextCollabParentEventError(
                    f"parent_event {request.parent_event!r} does not exist in "
                    f"collaboration context {collab_context_id!r}"
                )

            previous = _collet_from_row(row)
            self._record_decoder.validate(row.creation_facts)
            previous_task, _tasks = load_tasks(db, row)
            root_task = previous_task
            if previous_task is not None and row.root_task_ref is None:
                old_rows = (
                    db.query(ContextCollabEventModel)
                    .filter(
                        ContextCollabEventModel.collab_context_id == collab_context_id,
                        ContextCollabEventModel.env == get_current_env(),
                    )
                    .order_by(ContextCollabEventModel.id.asc())
                    .all()
                )
                previous_task = legacy_progress(
                    previous_task, [_event_from_row(e) for e in old_rows]
                )
            context_version = request.expected_version + 1
            now = datetime.now(timezone.utc)
            creating = request.task_creation
            if creating is not None:
                child = creating.parent_task_ref is not None
                parent = next(
                    (t for t in _tasks if t.task_id == creating.parent_task_ref), None
                )
                if (
                    (previous_task is not None and not child)
                    or (
                        child
                        and (
                            parent is None
                            or creating.principal_ref != parent.principal_ref
                        )
                    )
                    or any(t.task_id == creating.task_id for t in _tasks)
                    or request.event_kind != "task.started"
                    or request.primitive_type != "Task"
                    or request.task_update is None
                    or creating.collet_ref != collab_context_id
                    or creating.status != "created"
                    or creating.version != 1
                    or (not child and creating.principal_ref != previous.initiator_ref)
                    or creating.created_event_ref != creating.last_event_ref
                    or creating.legacy_context_version is not None
                ):
                    raise ContextCollabValidationError("invalid root Task start intent")
                binding_rows = (
                    db.query(ContextCollabEventModel)
                    .filter(
                        ContextCollabEventModel.collab_context_id == collab_context_id,
                        ContextCollabEventModel.env == get_current_env(),
                    )
                    .order_by(ContextCollabEventModel.id.asc())
                    .all()
                )
                if not child and creating.executor_refs != initial_executor_refs(
                    [_event_from_row(e) for e in binding_rows]
                ):
                    raise ContextCollabValidationError(
                        "Task does not match initial executor bindings"
                    )
                previous_task = creating
                creation_event = EventRecord(
                    collab_context_id=collab_context_id,
                    event_id=creating.created_event_ref,
                    parent_event=None,
                    event_kind="task.created",
                    primitive_type="Task",
                    actor_ref=request.actor_ref,
                    visibility="public",
                    payload={
                        "objective": creating.objective,
                        "executor_refs": [r.to_dict() for r in creating.executor_refs],
                        "completion_conditions": list(creating.completion_conditions),
                    },
                    context_version_after=context_version,
                    occurred_at=now,
                    task_ref=creating.task_id,
                    task_version_after=1,
                )
                db.add(self._event_row(creation_event, now=now))
                context_version += 1
            event = EventRecord(
                collab_context_id=collab_context_id,
                event_id="evt_" + uuid4().hex,
                parent_event=creating.created_event_ref
                if creating is not None
                else request.parent_event,
                event_kind=request.event_kind,
                primitive_type=request.primitive_type,
                actor_ref=request.actor_ref,
                visibility=request.visibility,
                payload=dict(request.payload),
                context_version_after=context_version,
                occurred_at=now,
            )
            is_work = request.primitive_type in {
                "Task",
                "Activity",
            } and request.event_kind.startswith("task.")
            if is_work and request.task_update is None:
                raise ContextCollabValidationError(
                    "Work append requires a versioned TaskUpdate"
                )
            if request.task_update is not None and creating is None:
                previous_task = next(
                    (t for t in _tasks if t.task_id == request.task_update.task_ref),
                    previous_task
                    if previous_task is not None
                    and previous_task.task_id == request.task_update.task_ref
                    else None,
                )
            new_task = previous_task
            if request.task_update is not None:
                if previous_task is None:
                    raise ContextCollabValidationError("cannot update a missing Task")
                if request.primitive_type not in {
                    "Task",
                    "Activity",
                } or not request.event_kind.startswith("task."):
                    raise ContextCollabValidationError(
                        "only Work events may update a Task"
                    )
                new_task = request.task_update.apply(
                    previous_task,
                    event_id=event.event_id,
                    event_kind=request.event_kind,
                    payload=request.payload,
                )
                event = replace(
                    event,
                    task_ref=new_task.task_id,
                    task_version_after=new_task.version,
                )
            if new_task is not None:
                if row.root_task_ref is None or creating is not None:
                    db.add(self._task_row(new_task, now=now))
                elif request.task_update is not None:
                    updated = (
                        db.query(ContextCollabTaskModel)
                        .filter(
                            ContextCollabTaskModel.task_id == new_task.task_id,
                            ContextCollabTaskModel.collab_context_id
                            == collab_context_id,
                            ContextCollabTaskModel.env == get_current_env(),
                            ContextCollabTaskModel.task_version
                            == previous_task.version,
                        )
                        .update(
                            {
                                "task_data": _dump_json(new_task.to_dict()),
                                "task_version": new_task.version,
                            },
                            synchronize_session=False,
                        )
                    )
                    if updated != 1:
                        raise ContextCollabVersionConflict("Task CAS failed")
            updated_collet = replace(
                previous,
                version=context_version,
                metadata=(
                    replace(
                        previous.metadata, updated_at=now, updated_by=request.actor_ref
                    )
                    if previous.metadata is not None
                    else None
                ),
            )
            db.add(self._event_row(event, now=now))
            updated_rows = (
                db.query(ContextCollabColletModel)
                .filter(
                    ContextCollabColletModel.id == row.id,
                    ContextCollabColletModel.context_version
                    == request.expected_version,
                )
                .update(
                    {
                        "current_snapshot": None,
                        "root_task_ref": root_task.task_id
                        if root_task
                        else (new_task.task_id if new_task else None),
                        "context_version": context_version,
                        "collet_metadata": (
                            _dump_json(updated_collet.metadata.to_dict())
                            if updated_collet.metadata is not None
                            else None
                        ),
                    },
                    synchronize_session=False,
                )
            )
            if updated_rows != 1:
                raise ContextCollabVersionConflict(
                    f"cas update on collet {collab_context_id!r} affected "
                    f"{updated_rows} rows for expected_version "
                    f"{request.expected_version}"
                )
            db.flush()
            return StoredEventAppend(
                event=event,
                collet=updated_collet,
                creation_facts_json=row.creation_facts,
                task=new_task,
            )

    def get_current_status(self, collab_context_id: str) -> StateT | None:
        """读取 Collet + 全量事件列表(spec §9.4 规则 3:只读,不写库)。

        事件按自增 id 升序返回(即接纳顺序);主干(trunk)过滤在查询投影
        层由活动链推导,仓储不持有 is_trunk/sequence 概念;不存在返回 ``None``。
        """
        with self._db.orm_session() as db:
            row = (
                db.query(ContextCollabColletModel)
                .filter(
                    ContextCollabColletModel.collab_context_id == collab_context_id,
                    ContextCollabColletModel.env == get_current_env(),
                )
                .one_or_none()
            )
            if row is None:
                return None
            event_rows = (
                db.query(ContextCollabEventModel)
                .filter(
                    ContextCollabEventModel.collab_context_id == collab_context_id,
                    ContextCollabEventModel.env == get_current_env(),
                )
                .order_by(ContextCollabEventModel.id.asc())
                .all()
            )
            collet = _collet_from_row(row)
            task, tasks = load_tasks(db, row)
            events = [_event_from_row(event_row) for event_row in event_rows]
            if row.root_task_ref is None:
                task = legacy_progress(task, events)
            if task is not None and row.root_task_ref is None:
                tasks = tuple(task if t.task_id == task.task_id else t for t in tasks)
            return self._record_decoder.decode(
                PrimitiveReadState(collet, task, tasks, events), row.creation_facts
            )

    def count_message_events(self, collab_context_ids: Sequence[str]) -> dict[str, int]:
        """按 Collet 计 Message 事件数(GROUP BY 单查询,禁 N+1)。

        键=collab_context_id,值=``primitive_type='Message'`` 事件总数
        (含首帖正文);请求中没有事件的 id 不出现在结果键里。Task 类
        控制事件不参与计数。
        """
        if not collab_context_ids:
            return {}
        with self._db.orm_session() as db:
            rows = (
                db.query(
                    ContextCollabEventModel.collab_context_id,
                    func.count(ContextCollabEventModel.id),
                )
                .filter(
                    ContextCollabEventModel.collab_context_id.in_(collab_context_ids),
                    ContextCollabEventModel.primitive_type == "Message",
                    ContextCollabEventModel.env == get_current_env(),
                )
                .group_by(ContextCollabEventModel.collab_context_id)
                .all()
            )
            return {collab_context_id: int(count) for collab_context_id, count in rows}

    @staticmethod
    def _collet_row(
        collet: Collet,
        creation_facts_json: str,
        task: Task | None,
        *,
        now: datetime,
    ) -> ContextCollabColletModel:
        """Persist L4 scope/audit plus plain creation facts and root Task reference."""
        return ContextCollabColletModel(
            collab_context_id=collet.collet_id,
            actor_ref=_dump_json(collet.initiator_ref.to_dict()),
            scope=_dump_json(collet.scope.to_dict())
            if collet.scope is not None
            else "null",
            collet_metadata=(
                _dump_json(collet.metadata.to_dict())
                if collet.metadata is not None
                else None
            ),
            creation_facts=creation_facts_json,
            current_snapshot=None,
            root_task_ref=task.task_id if task else None,
            context_version=collet.version,
            env=get_current_env(),
            gmt_create=now,
            gmt_modified=now,
        )

    @staticmethod
    def _event_row(event: EventRecord, *, now: datetime) -> ContextCollabEventModel:
        return _event_row(event, now=now)

    @staticmethod
    def _task_row(task: Task, *, now: datetime) -> ContextCollabTaskModel:
        return ContextCollabTaskModel(
            task_id=task.task_id,
            collab_context_id=task.collet_ref,
            parent_task_ref=task.parent_task_ref,
            task_version=task.version,
            task_data=_dump_json(task.to_dict()),
            env=get_current_env(),
            gmt_create=now,
            gmt_modified=now,
        )
