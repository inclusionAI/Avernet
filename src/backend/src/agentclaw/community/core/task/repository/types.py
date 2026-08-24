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
    gmt_create: Optional[datetime] = None
    gmt_modified: Optional[datetime] = None


@dataclass(frozen=True)
class TaskNodeRecord:
    id: int
    task_id: str
    node_id: str
    task_spec: dict[str, Any]
    status: Status
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
            acceptances_metric=list(self.acceptance_result.get("acceptances_metric", [])),
            gaps=list(self.acceptance_result.get("gaps", [])),
        )


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
    gmt_create: Optional[datetime] = None
    gmt_modified: Optional[datetime] = None