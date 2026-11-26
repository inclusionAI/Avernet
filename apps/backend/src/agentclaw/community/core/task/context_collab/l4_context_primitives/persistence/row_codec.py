"""L4 row codecs; composition JSON remains serialized at this SQL boundary."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.legacy_collet import (
    LegacyCollet,
    ColletState,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet_metadata import (
    ColletMetadata,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.models import (
    ContextCollabColletModel,
    ContextCollabEventModel,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.resource_binding import (
    ResourceBinding,
)
from agentclaw.community.utils.env_utils import get_current_env


def _dump_json(data: dict) -> str:
    """JSON 列编码;与仓储解码成对,保证 round-trip 无损。"""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _collet_from_row(
    row: ContextCollabColletModel,
) -> LegacyCollet:
    """Decode L4 identity/scope/audit; composition stays with the injected codec."""
    if row.actor_ref is None:
        raise ContextCollabValidationError(
            f"collaboration context {row.collab_context_id!r} has a NULL actor_ref column; "
            "the Collet aggregate requires a non-null initiator_ref"
        )
    metadata = None
    if row.collet_metadata is not None:
        try:
            data = json.loads(row.collet_metadata)
            # Retired marker is accepted only at the stored-row boundary.
            if isinstance(data, dict) and "schema_version" in data:
                if data.pop("schema_version") != "1.1":
                    raise ContextCollabValidationError(
                        "unsupported legacy Collet schema_version"
                    )
            metadata = ColletMetadata.from_dict(data)
        except (json.JSONDecodeError, ContextCollabValidationError) as error:
            raise ContextCollabValidationError(
                f"collaboration context {row.collab_context_id!r} has invalid "
                f"collet_metadata: {error}"
            ) from error
    resources = ()
    if row.resources is not None:
        try:
            raw_resources = json.loads(row.resources)
            if not isinstance(raw_resources, list):
                raise ContextCollabValidationError("resources must be a list")
            resources = tuple(
                ResourceBinding.from_dict(resource) for resource in raw_resources
            )
        except (json.JSONDecodeError, ContextCollabValidationError) as error:
            raise ContextCollabValidationError(
                f"collaboration context {row.collab_context_id!r} has invalid "
                f"resources: {error}"
            ) from error
    scope = json.loads(row.scope)
    collet = LegacyCollet(
        collet_id=row.collab_context_id,
        initiator_ref=PrincipalRef.from_dict(json.loads(row.actor_ref)),
        state=ColletState.ACTIVE,
        scope=SpaceRef.from_dict(scope) if scope is not None else None,
        version=int(row.context_version),
        metadata=metadata,
        resources=resources,
    )
    return collet


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
        task_ref=row.task_ref,
        task_version_after=row.task_version_after,
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


def _event_row(event: EventRecord, *, now: datetime) -> ContextCollabEventModel:
    """``EventRecord`` 领域对象 → 落库行;append-only,gmt_modified 与 gmt_create 同值写入。"""
    return ContextCollabEventModel(
        collab_context_id=event.collab_context_id,
        event_id=event.event_id,
        task_ref=event.task_ref,
        task_version_after=event.task_version_after,
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
        gmt_modified=now,
    )
