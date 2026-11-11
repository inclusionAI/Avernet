"""Context collab 仓储契约与 ORM 实现(spec §9.3-9.4)。

协议部分(``ContextCollabRepositoryProtocol`` 与出入参 dataclass)保持纯
类型声明;``ContextCollabRepository`` 是基于 ``DatabasePlugin`` 的 ORM 实现,
行 → 领域对象一律显式构造,JSON 列(context_spec / current_snapshot /
actor_ref / payload)用 L4 ``to_dict`` / ``from_dict`` 编解码。读查询过滤
``env == get_current_env()``,tenant 由 ORM guard 自动追加,不在此手写。
``append_event`` 在持锁事务内完成版本检查 → parent 校验 → 事件落库 →
Collet CAS 更新;追加不设幂等键,顺序即自增 id,主干在投影层推导。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable, Protocol, runtime_checkable
from uuid import uuid4

from injector import inject

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabParentEventError,
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


@dataclass
class InitialEventDraft:
    """创建时固定的初始事件草稿(service 提供):task.created / Task。"""

    event_kind: str       # "task.created"
    primitive_type: str   # "Task"
    actor_ref: PrincipalRef  # 创建人
    payload: dict         # {"objective": <objective>}


@dataclass
class CreateContextRecordRequest:
    """仓储创建请求;创建不设幂等键(spec 作者裁决:调用方无需防重)。"""

    context_spec: ContextSpec
    initial_event: InitialEventDraft


@dataclass
class ContextCreatedRecord:
    """仓储创建返回:新建的 Collet 与其初始事件。"""

    collet: Collet
    initial_event: EventRecord


@dataclass
class ContextStatusRecord:
    """``get_current_status`` 返回;events 按自增 id 升序即接纳顺序,
    不做可见性过滤(visibility 过滤与 trunk 过滤在 service/Query 层)。"""

    collet: Collet
    events: list[EventRecord]


@dataclass
class AppendEventRecordRequest:
    """``append_event`` 请求;追加不设幂等键,重复提交即重复事件。

    ``snapshot_updater`` 由 Service(Task 10)构建,是 G4 求解出的
    "当前快照 → 新快照" 纯函数;仓储在持锁事务内执行它,保证其输入
    不会在计算与应用之间变陈旧。
    """

    expected_version: int
    event_kind: str
    primitive_type: str
    actor_ref: PrincipalRef
    parent_event: str | None
    visibility: str
    payload: dict
    snapshot_updater: Callable[[CurrentSnapshot], CurrentSnapshot]


@dataclass
class EventAppendedRecord:
    """``append_event`` 返回;``collet`` 是追加后落库行的解码。"""

    event: EventRecord
    collet: Collet


@runtime_checkable
class ContextCollabRepositoryProtocol(Protocol):
    """Collet 聚合的两张表(ac_context_collab_collet/event)存取协议。

    ``create_context`` 插 collet(initial snapshot/context_version=1)
    + 初始事件(parent=None);创建不设幂等键,重复调用即重复创建。
    """

    def create_context(self, request: CreateContextRecordRequest) -> ContextCreatedRecord: ...
    def append_event(self, collab_context_id: str, request: "AppendEventRecordRequest") -> "EventAppendedRecord": ...
    def get_current_status(self, collab_context_id: str) -> "ContextStatusRecord | None": ...


def _dump_json(data: dict) -> str:
    """JSON 列编码;与仓储解码成对,保证 round-trip 无损。"""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _collet_from_row(row: ContextCollabColletModel) -> Collet:
    """落库行 → ``Collet`` 领域对象;显式逐字段构造。

    ``current_snapshot`` 可空:外部可写入只有 spec 的聚合行。
    """
    return Collet(
        collab_context_id=row.collab_context_id,
        actor_ref=(
            PrincipalRef.from_dict(json.loads(row.actor_ref))
            if row.actor_ref is not None
            else None
        ),
        context_spec=ContextSpec.from_dict(json.loads(row.context_spec)),
        current_snapshot=(
            CurrentSnapshot.from_dict(json.loads(row.current_snapshot))
            if row.current_snapshot is not None
            else None
        ),
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
        parent_event=row.parent_event,
        event_kind=row.event_kind,
        primitive_type=row.primitive_type,
        actor_ref=(
            PrincipalRef.from_dict(json.loads(row.actor_ref))
            if row.actor_ref is not None
            else None
        ),
        visibility=row.visibility,
        payload=json.loads(row.payload) if row.payload is not None else None,
        context_version_after=(
            int(row.context_version_after)
            if row.context_version_after is not None
            else None
        ),
        occurred_at=occurred_at,
    )


def _parent_event_exists(db, collab_context_id: str, parent_event: str) -> bool:
    """parent 必须命中同一上下文内已存在事件(spec §9.4 规则 4)。"""
    return (
        db.query(ContextCollabEventModel.id)
        .filter(
            ContextCollabEventModel.collab_context_id == collab_context_id,
            ContextCollabEventModel.event_id == parent_event,
            ContextCollabEventModel.env == get_current_env(),
        )
        .one_or_none()
        is not None
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

        创建不设幂等键(spec 作者裁决):重复调用即重复创建,自然防重
        由调用方保证。
        """
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
        event_id = "evt_" + uuid4().hex
        initial_event = EventRecord(
            collab_context_id=collab_context_id,
            event_id=event_id,
            parent_event=None,
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
            # 创建主体与初始事件的 actor_ref 同源(创建人)。
            actor_ref=request.initial_event.actor_ref,
            context_spec=spec,
            current_snapshot=snapshot,
            current_event_id=initial_event.event_id,
            context_version=1,
        )
        with self._transaction() as db:
            db.add(self._collet_row(collet, now=now))
            db.add(self._event_row(initial_event, now=now))
            db.flush()
            return ContextCreatedRecord(collet=collet, initial_event=initial_event)

    def append_event(
        self, collab_context_id: str, request: "AppendEventRecordRequest"
    ) -> "EventAppendedRecord":
        """追加事件并推进 Collet 快照(spec §9.4 规则 2/4/5)。

        持锁事务内的固定顺序:行锁读 collet → ``expected_version`` 检查 →
        ``parent_event`` 存在性检查 → 插事件 → CAS 更新 collet。
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
            context_version = request.expected_version + 1
            now = datetime.now(timezone.utc)
            event = EventRecord(
                collab_context_id=collab_context_id,
                event_id="evt_" + uuid4().hex,
                parent_event=request.parent_event,
                event_kind=request.event_kind,
                primitive_type=request.primitive_type,
                actor_ref=request.actor_ref,
                visibility=request.visibility,
                payload=dict(request.payload),
                context_version_after=context_version,
                occurred_at=now,
            )
            # snapshot_updater 在行锁内对持锁读出的快照执行,其视图不可能变陈旧;
            # current_snapshot 为 NULL 的外部部分行保持 NULL(Ruling R10):
            # 只推进 version 与事件指针,不为它虚构快照。
            if previous.current_snapshot is None:
                new_snapshot = None
                snapshot_write: str | None = None
            else:
                new_snapshot = request.snapshot_updater(previous.current_snapshot)
                snapshot_write = _dump_json(new_snapshot.to_dict())
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
                        "current_snapshot": snapshot_write,
                        "current_event_id": event.event_id,
                        "context_version": context_version,
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
            return EventAppendedRecord(
                event=event,
                collet=replace(
                    previous,
                    current_snapshot=new_snapshot,
                    current_event_id=event.event_id,
                    context_version=context_version,
                ),
            )

    def get_current_status(
        self, collab_context_id: str
    ) -> ContextStatusRecord | None:
        """读取 Collet + 全量事件列表(spec §9.4 规则 3:只读,不写库)。

        事件按自增 id 升序返回(即接纳顺序);主干(trunk)过滤在查询投影
        层由活动链推导,仓储_trunk/sequence 概念;不存在返回 ``None``。
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
            return ContextStatusRecord(
                collet=_collet_from_row(row),
                events=[_event_from_row(event_row) for event_row in event_rows],
            )

    @staticmethod
    def _collet_row(collet: Collet, *, now: datetime) -> ContextCollabColletModel:
        """``Collet`` 领域对象 → 落库行;tenant 由 ORM guard 盖章。"""
        return ContextCollabColletModel(
            collab_context_id=collet.collab_context_id,
            actor_ref=(
                _dump_json(collet.actor_ref.to_dict())
                if collet.actor_ref is not None
                else None
            ),
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
            parent_event=event.parent_event,
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
