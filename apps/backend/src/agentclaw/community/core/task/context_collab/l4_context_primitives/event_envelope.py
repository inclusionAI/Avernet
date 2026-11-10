"""L4 事件记录与 ACCS EventEnvelope 适配(spec §3.4.1)。

``EventRecord`` 是本地 append-only 事件持久化记录(对应
``ac_context_collab_event`` 表),不是 ACCS 正式概念对象,更不得命名为
``ColletEvent``;``EventEnvelope`` 是 ACCS 公共合同的公共信封,字段名取
自 spec §3.4.1。两个对象只做字段适配,不做任何 IO。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)

EVENT_ENVELOPE_VERSION = 1
"""ACCS EventEnvelope 当前协议版本;非 1 的版本视为不兼容。"""


@dataclass
class EventRecord:
    """本地事件持久化记录(不得命名 ``ColletEvent``,spec §3.4.1)。

    ``parent_event`` 是事件 DAG 的本地因果关系字段,主事件为 ``None``;
    ``is_trunk`` 在接纳时计算;``occurred_at`` 是事件发生时间。行一旦
    追加即不再改写,因此本记录没有生命周期方法。
    """

    collab_context_id: str
    event_id: str
    request_id: str
    sequence_no: int
    parent_event: str | None
    is_trunk: bool
    event_kind: str
    primitive_type: str  # Message | Task | Activity
    actor_ref: PrincipalRef
    visibility: str  # public | workspace | private
    payload: dict
    context_version_after: int
    occurred_at: datetime

    def to_envelope(self) -> "EventEnvelope":
        """适配为 ACCS 公共合同信封;字段映射见 spec §3.4.1。"""
        return EventEnvelope(
            event_id=self.event_id,
            event_type=self.event_kind,
            event_version=EVENT_ENVELOPE_VERSION,
            aggregate_ref=self.collab_context_id,
            aggregate_revision=self.context_version_after,
            causation_id=self.parent_event,
            payload=self.payload,
        )

    @classmethod
    def from_envelope(
        cls,
        envelope: "EventEnvelope",
        *,
        request_id: str,
        sequence_no: int,
        is_trunk: bool,
        primitive_type: str,
        actor_ref: PrincipalRef,
        visibility: str,
        occurred_at: datetime,
    ) -> "EventRecord":
        """从 ACCS 信封反向构造本地记录。

        信封只携带公共合同字段,``request_id`` / ``sequence_no`` /
        ``is_trunk`` / ``primitive_type`` / ``actor_ref`` / ``visibility`` /
        ``occurred_at`` 这些本地字段必须由调用方补充;不支持的信封版本
        直接拒绝。
        """
        if envelope.event_version != EVENT_ENVELOPE_VERSION:
            raise ContextCollabValidationError(
                f"unsupported event_version: {envelope.event_version!r}, "
                f"expected: {EVENT_ENVELOPE_VERSION}"
            )
        return cls(
            collab_context_id=envelope.aggregate_ref,
            event_id=envelope.event_id,
            request_id=request_id,
            sequence_no=sequence_no,
            parent_event=envelope.causation_id,
            is_trunk=is_trunk,
            event_kind=envelope.event_type,
            primitive_type=primitive_type,
            actor_ref=actor_ref,
            visibility=visibility,
            payload=envelope.payload,
            context_version_after=envelope.aggregate_revision,
            occurred_at=occurred_at,
        )


@dataclass
class EventEnvelope:
    """ACCS 公共合同事件信封(spec §3.4.1)。

    ``event_type`` 对应本地 ``event_kind``;``aggregate_ref`` 对应
    ``collab_context_id``;``aggregate_revision`` 对应
    ``context_version_after``;``causation_id`` 对应 ``parent_event``。
    本地 API 与数据库仍保留原始字段名,不做改名。
    """

    event_id: str
    event_type: str
    event_version: int
    aggregate_ref: str
    aggregate_revision: int
    causation_id: str | None
    payload: dict
