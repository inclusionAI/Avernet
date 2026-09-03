"""Table-faithful record dataclasses + projection helpers for the 5 task tables.

Mirrors ``core/task_queue/types.py``: frozen dataclasses returned by repositories
(never ORM objects). Structured TEXT columns hold parsed JSON (``dict``) or raw
``str``; enum columns hold the domain enum. Projections onto domain dataclasses
are provided where the mapping needs no nested (de)serialization (``Relation``,
``AcceptanceResult``). ``TaskSpec``/``TaskInfo``/``RuntimeInfo`` projections are
deferred — the domain dataclasses have no (de)serialization and the full graph
state has no persistence home yet (spec §3); the records hold parsed dicts so the
projection can be added later without a schema change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from agentclaw.community.core.task.domain.models import (
    AcceptanceResult,
    AcceptanceVerdict,
    NodeAction,
    NodeActionEvent,
    Relation,
    RelationType,
    Status,
)


@dataclass(frozen=True)
class TaskInfoRecord:
    id: int
    task_id: str
    source_type: str
    owner_user_id: str
    owner_bot_id: str
    execution_config: Optional[dict[str, Any]]
    task_spec: dict[str, Any]
    status: Status
    # Read-side owner display fields. They are enriched from authoritative
    # Bot/staff services and are not persisted in task_info.
    owner_bot_name: Optional[str] = None
    owner_user_name: Optional[str] = None
    graph_run_id: Optional[str] = None
    graph_loop_round: int = 0
    graph_output: Optional[dict[str, Any]] = None
    graph_extend_props: Optional[dict[str, Any]] = None
    graph_version: int = 0
    lease_owner: Optional[str] = None
    lease_until: Optional[int] = None
    heartbeat_at: Optional[int] = None
    gmt_create: Optional[datetime] = None
    gmt_modified: Optional[datetime] = None


@dataclass(frozen=True)
class TaskNodeRecord:
    id: int
    task_id: str
    node_id: str
    task_spec: dict[str, Any]
    status: Status
    is_deleted: bool = False
    gmt_create: Optional[datetime] = None
    gmt_modified: Optional[datetime] = None


@dataclass(frozen=True)
class TaskNodeRunInfoRecord:
    id: int
    node_id: str
    task_id: str
    run_mode: Optional[str]
    assignee: Optional[str]
    output: Optional[dict[str, Any]]
    acceptance_result: Optional[dict[str, Any]]
    retry: int
    session_id: Optional[str]
    extend_props: Optional[dict[str, Any]]
    start_time: Optional[int]
    update_time: Optional[int]
    end_time: Optional[int]
    gmt_create: Optional[datetime] = None
    gmt_modified: Optional[datetime] = None

    def to_acceptance_result(self) -> Optional[AcceptanceResult]:
        """Project the ``acceptance_result`` JSON dict onto the domain type."""
        if self.acceptance_result is None:
            return None
        return AcceptanceResult(
            verdict=AcceptanceVerdict(self.acceptance_result["verdict"]),
            acceptances_metric=list(
                self.acceptance_result.get("acceptances_metric", [])
            ),
            gaps=list(self.acceptance_result.get("gaps", [])),
        )


@dataclass(frozen=True)
class BbsTaskOverviewRecord:
    """``GET /bbs/list`` 后端联合投影:`task_node_run_info` (run_mode='bbs') ⋈ `task_node`,
    再按 task_id 补 `task_info.owner_bot_id`(publisher)。忠实映射给定 SQL 的列别名。

    `task_spec`/`extend_props`/`acceptance_result` 为已 JSON 解析的原始 dict;title/goal/
    acceptances/assignee_name 由 adapter translator 二次解析(不在此 record 内)。
    """

    task_id: str
    node_id: str
    run_mode: Optional[str]
    retry: int
    assignee_id: Optional[str]  # task_node_run_info.assignee(原 SQL 别名 assignee_id)
    status: Status  # task_node.status(领域枚举)
    acceptance_result: Optional[dict[str, Any]]
    extend_props: Optional[dict[str, Any]]
    relay_create_time: Optional[datetime]  # task_node.gmt_create
    relay_begin_time: Optional[datetime]  # task_node_run_info.gmt_create
    relay_end_time: Optional[datetime]  # task_node_run_info.gmt_modified
    task_spec: dict[str, Any]
    publisher: Optional[str]  # task_info.owner_bot_id(缺失 → None)
    owner_user_id: Optional[str] = None  # task_info.owner_user_id;供 service 批量查 name(不进 DTO)
    publisher_name: Optional[str] = None  # 发布方 bot 名称(service enrich;缺失/降级 → None)


@dataclass
class TaskNodeRunInfoUpdate:
    """Partial update for ``task_node_run_info``. ``None`` means leave the row
    unchanged (mirrors the domain ``TaskNodePatch``/``TaskGraphPatch`` idiom)."""

    run_mode: Optional[str] = None
    assignee: Optional[str] = None
    output: Optional[dict[str, Any]] = None
    acceptance_result: Optional[dict[str, Any]] = None
    session_id: Optional[str] = None
    extend_props: Optional[dict[str, Any]] = None
    start_time: Optional[int] = None
    update_time: Optional[int] = None
    end_time: Optional[int] = None


@dataclass(frozen=True)
class TaskNodeRelationRecord:
    id: int
    task_id: str
    src_node_id: str
    dst_node_id: str
    relation_type: RelationType
    extend_props: Optional[dict[str, Any]]
    gmt_create: Optional[datetime] = None
    gmt_modified: Optional[datetime] = None

    def to_relation(self) -> Relation:
        """Clean 1:1 projection onto domain ``Relation``."""
        return Relation(
            src_id=self.src_node_id,
            dst_id=self.dst_node_id,
            type=self.relation_type,
            extend_props=dict(self.extend_props) if self.extend_props else {},
        )


@dataclass(frozen=True)
class TaskCallbackRecord:
    id: int
    invoker: str
    run_id: str
    node_id: str
    main_session_id: str
    status: Optional[str]
    orig_callback_data: str
    execution_graph: Optional[dict[str, Any]]
    result: Optional[dict[str, Any]]
    result_success: Optional[bool]
    exec_error: Optional[str]
    extend_props: Optional[dict[str, Any]]
    event_id: Optional[str] = None
    process_status: Optional[str] = None
    processed_at: Optional[datetime] = None
    gmt_create: Optional[datetime] = None
    gmt_modified: Optional[datetime] = None


@dataclass(frozen=True)
class TaskActionLogRecord:
    """Table-faithful record for one append-only node action."""

    id: int
    event_id: str
    task_id: str
    node_id: str
    seq: int
    action: NodeAction
    loop_round: Optional[int]
    attempt: int
    status_from: Optional[Status]
    status_to: Optional[Status]
    payload: dict[str, Any]
    instance_id: Optional[str]
    gmt_create: Optional[datetime] = None

    def to_event(self) -> NodeActionEvent:
        return NodeActionEvent(
            seq=self.seq,
            ts=int(self.gmt_create.timestamp() * 1000) if self.gmt_create else 0,
            action=self.action,
            loop_round=self.loop_round or 0,
            attempt=self.attempt,
            status_from=self.status_from,
            status_to=self.status_to,
            payload=dict(self.payload),
        )
