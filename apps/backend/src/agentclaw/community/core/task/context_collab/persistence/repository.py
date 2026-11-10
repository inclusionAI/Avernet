"""Context collab 仓储契约与 ORM 实现(spec §9.3-9.4)。

协议部分(``ContextCollabRepositoryProtocol`` 与出入参 dataclass)保持纯
类型声明;``ContextCollabRepository`` 是基于 ``DatabasePlugin`` 的 ORM 实现,
行 → 领域对象一律显式构造,JSON 列(context_spec / current_snapshot /
actor_ref / payload)用 L4 ``to_dict`` / ``from_dict`` 编解码。读查询过滤
``env == get_current_env()``,tenant 由 ORM guard 自动追加,不在此手写。
``AppendEventRecordRequest`` / ``EventAppendedRecord`` 在追加任务补充定义,
``append_event`` 本文件先留桩(Task 9 实现并测试)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable
from uuid import uuid4

from injector import inject
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
    ContextSpec,
    CurrentSnapshot,
    ProgressSnapshot,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    TaskStatus,
)
from agentclaw.community.core.task.context_collab.persistence.models import (
    ContextCollabColletModel,
    ContextCollabEventModel,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env

# 初始事件对进入 Collet 的主体可见(G4 visibility 白名单内,spec §6.3)。
_CREATION_EVENT_VISIBILITY = "public"
_COLLET_REQUEST_INDEX = "uk_context_collab_collet_request"


@dataclass
class InitialEventDraft:
    """创建时固定的初始事件草稿(service 提供):task.created / Task。"""

    event_kind: str       # "task.created"
    primitive_type: str   # "Task"
    actor_ref: PrincipalRef  # 创建人
    payload: dict         # {"objective": <objective>}


@dataclass
class CreateContextRecordRequest:
    """仓储创建请求;``create_request_id`` 是 G2 幂等键。"""

    create_request_id: str
    context_spec: ContextSpec
    initial_event: InitialEventDraft


@dataclass
class ContextCreatedRecord:
    """仓储创建返回;``created=False`` 表示幂等命中返回原上下文。"""

    collet: Collet
    initial_event: EventRecord
    created: bool


@dataclass
class ContextStatusRecord:
    """``get_current_status`` 返回;events 按 sequence_no 升序不做可见性过滤
    (trunk 过滤在仓储完成,visibility 过滤在 service/Query 层)。"""

    collet: Collet
    events: list[EventRecord]


@runtime_checkable
class ContextCollabRepositoryProtocol(Protocol):
    """Collet 聚合的两张表(ac_context_collab_collet/event)存取协议。

    ``create_context`` 首次创建插 collet(initial
    snapshot/context_version=1)+ 初始事件(sequence_no=1、
    is_trunk=True、parent=None),重复 ``create_request_id`` 返回
    原行且 ``created=False``。
    """

    def create_context(self, request: CreateContextRecordRequest) -> ContextCreatedRecord: ...
    def append_event(self, collab_context_id: str, request: "AppendEventRecordRequest") -> "EventAppendedRecord": ...
    def get_current_status(self, collab_context_id: str, *, trunk_events: bool) -> "ContextStatusRecord | None": ...


def _dump_json(data: dict) -> str:
    """JSON 列编码;与仓储解码成对,保证 round-trip 无损。"""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _collet_from_row(row: ContextCollabColletModel) -> Collet:
    """落库行 → ``Collet`` 领域对象;显式逐字段构造。"""
    return Collet(
        collab_context_id=row.collab_context_id,
        create_request_id=row.create_request_id,
        context_spec=ContextSpec.from_dict(json.loads(row.context_spec)),
        current_snapshot=CurrentSnapshot.from_dict(json.loads(row.current_snapshot)),
        current_event_id=row.current_event_id,
        context_version=int(row.context_version),
    )


def _event_from_row(row: ContextCollabEventModel) -> EventRecord:
    """落库行 → ``EventRecord`` 领域对象;显式逐字段构造。"""
    occurred_at = row.occurred_at
    if occurred_at.tzinfo is None:
        # 本仓储只写 UTC;SQLite / MySQL DATETIME 读回是 naive,读路径统一
        # 补回 UTC,否则与创建返回值(tz-aware)比较 / 排序会静默出错。
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    return EventRecord(
        collab_context_id=row.collab_context_id,
        event_id=row.event_id,
        request_id=row.request_id,
        sequence_no=int(row.sequence_no),
        parent_event=row.parent_event,
        is_trunk=bool(row.is_trunk),
        event_kind=row.event_kind,
        primitive_type=row.primitive_type,
        actor_ref=PrincipalRef.from_dict(json.loads(row.actor_ref)),
        visibility=row.visibility,
        payload=json.loads(row.payload),
        context_version_after=int(row.context_version_after),
        occurred_at=occurred_at,
    )


def _is_unique_conflict(
    exc: IntegrityError,
    *,
    index_name: str,
    table_name: str,
    required_columns: tuple[str, ...],
) -> bool:
    """Recognize one named MySQL/OceanBase or column-based SQLite conflict."""
    message = str(getattr(exc, "orig", None) or exc)
    if index_name in message:
        return True
    return (
        "UNIQUE constraint failed" in message
        and table_name in message
        and all(column in message for column in required_columns)
    )


def _is_collet_request_idempotency_conflict(exc: IntegrityError) -> bool:
    return _is_unique_conflict(
        exc,
        index_name=_COLLET_REQUEST_INDEX,
        table_name="ac_context_collab_collet",
        required_columns=("avernet_tenant", "env", "create_request_id"),
    )


class ContextCollabRepository(ContextCollabRepositoryProtocol):
    """两行一事务的 Collet/Event ORM 存取(``@inject db: DatabasePlugin``)。"""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def _transaction(self):
        transactional = getattr(self._db, "transactional_orm_session", None)
        return transactional() if transactional is not None else self._db.orm_session()

    def create_context(self, request: CreateContextRecordRequest) -> ContextCreatedRecord:
        """创建 Collet + 初始事件(spec §9.4 规则 1:同一事务两行)。

        幂等(spec §9.4 规则 1/G2):事务前先按 ``create_request_id`` 查
        重放,命中返回原 collet + 原初始事件且 ``created=False``;并发下
        由 ``uk_context_collab_collet_request`` 唯一键兜底,IntegrityError
        分类命中后转为重放,其余如实上抛。
        """
        replay = self._find_request_replay(request.create_request_id)
        if replay is not None:
            return replay

        spec = request.context_spec
        # 初始快照由创建参数派生:status=created、progress 全 0;快照内的
        # progress.current_event_id 由 service 组装响应时回填(spec §6.2)。
        snapshot = CurrentSnapshot(
            objective=spec.objective,
            status=TaskStatus.CREATED.value,
            principal_ref=spec.principal_ref,
            executor_ref=spec.executor_ref,
            method_kind=spec.method_kind,
            progress=ProgressSnapshot(),
        )
        now = datetime.now(timezone.utc)
        collab_context_id = "collet_" + uuid4().hex
        initial_event = EventRecord(
            collab_context_id=collab_context_id,
            event_id="evt_" + uuid4().hex,
            request_id=request.create_request_id,
            sequence_no=1,
            parent_event=None,
            is_trunk=True,
            event_kind=request.initial_event.event_kind,
            primitive_type=request.initial_event.primitive_type,
            actor_ref=request.initial_event.actor_ref,
            visibility=_CREATION_EVENT_VISIBILITY,
            payload=dict(request.initial_event.payload),
            context_version_after=1,
            occurred_at=now,
        )
        collet = Collet(
            collab_context_id=collab_context_id,
            create_request_id=request.create_request_id,
            context_spec=spec,
            current_snapshot=snapshot,
            current_event_id=initial_event.event_id,
            context_version=1,
        )
        try:
            with self._transaction() as db:
                # 事务内复查:窗口内并发的重复创建到此转为重放。
                replay = self._replay_record(db, request.create_request_id)
                if replay is not None:
                    return replay
                db.add(self._collet_row(collet, now=now))
                db.add(self._event_row(initial_event, now=now))
                db.flush()
                return ContextCreatedRecord(
                    collet=collet, initial_event=initial_event, created=True
                )
        except IntegrityError as exc:
            if not _is_collet_request_idempotency_conflict(exc):
                raise
            replay = self._find_request_replay(request.create_request_id)
            if replay is None:
                raise
            return replay

    def append_event(
        self, collab_context_id: str, request: "AppendEventRecordRequest"
    ) -> "EventAppendedRecord":
        raise NotImplementedError("append_event lands in the next repository task")

    def get_current_status(
        self, collab_context_id: str, *, trunk_events: bool
    ) -> ContextStatusRecord | None:
        """读取 Collet + 事件列表(spec §9.4 规则 3:只读,不写库)。

        事件全量返回或仅主事件(参数过滤,顺序始终 sequence_no 升序);
        ``trunk_events`` 只过滤不重排;不存在返回 ``None``。
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
            events_query = db.query(ContextCollabEventModel).filter(
                ContextCollabEventModel.collab_context_id == collab_context_id,
                ContextCollabEventModel.env == get_current_env(),
            )
            if trunk_events:
                events_query = events_query.filter(
                    ContextCollabEventModel.is_trunk.is_(True)
                )
            event_rows = events_query.order_by(
                ContextCollabEventModel.sequence_no.asc()
            ).all()
            return ContextStatusRecord(
                collet=_collet_from_row(row),
                events=[_event_from_row(event_row) for event_row in event_rows],
            )

    def _find_request_replay(
        self, create_request_id: str
    ) -> ContextCreatedRecord | None:
        """事务外按 G2 幂等键查重放;未命中返回 ``None``。"""
        with self._db.orm_session() as db:
            return self._replay_record(db, create_request_id)

    def _replay_record(
        self, db, create_request_id: str
    ) -> ContextCreatedRecord | None:
        """collet + 其初始事件一起读出并组装 ``created=False`` 记录。"""
        row = (
            db.query(ContextCollabColletModel)
            .filter(
                ContextCollabColletModel.create_request_id == create_request_id,
                ContextCollabColletModel.env == get_current_env(),
            )
            .one_or_none()
        )
        if row is None:
            return None
        event_row = (
            db.query(ContextCollabEventModel)
            .filter(
                ContextCollabEventModel.collab_context_id == row.collab_context_id,
                ContextCollabEventModel.request_id == create_request_id,
                ContextCollabEventModel.env == get_current_env(),
            )
            .one_or_none()
        )
        if event_row is None:
            # collet 与初始事件同一事务写入,缺行即存储被外部破坏;如实上抛。
            raise RuntimeError(
                f"collet {row.collab_context_id!r} has no initial event for "
                f"create request {create_request_id!r}"
            )
        return ContextCreatedRecord(
            collet=_collet_from_row(row),
            initial_event=_event_from_row(event_row),
            created=False,
        )

    @staticmethod
    def _collet_row(collet: Collet, *, now: datetime) -> ContextCollabColletModel:
        """``Collet`` 领域对象 → 落库行;tenant 由 ORM guard 盖章。"""
        return ContextCollabColletModel(
            collab_context_id=collet.collab_context_id,
            create_request_id=collet.create_request_id,
            context_spec=_dump_json(collet.context_spec.to_dict()),
            current_snapshot=_dump_json(collet.current_snapshot.to_dict()),
            current_event_id=collet.current_event_id,
            context_version=collet.context_version,
            env=get_current_env(),
            gmt_create=now,
            gmt_modified=now,
        )

    @staticmethod
    def _event_row(event: EventRecord, *, now: datetime) -> ContextCollabEventModel:
        """``EventRecord`` 领域对象 → 落库行;append-only,不写 gmt_modified。"""
        return ContextCollabEventModel(
            collab_context_id=event.collab_context_id,
            event_id=event.event_id,
            request_id=event.request_id,
            sequence_no=event.sequence_no,
            parent_event=event.parent_event,
            is_trunk=event.is_trunk,
            event_kind=event.event_kind,
            primitive_type=event.primitive_type,
            actor_ref=_dump_json(event.actor_ref.to_dict()),
            visibility=event.visibility,
            payload=_dump_json(event.payload),
            context_version_after=event.context_version_after,
            occurred_at=event.occurred_at,
            env=get_current_env(),
            gmt_create=now,
        )
