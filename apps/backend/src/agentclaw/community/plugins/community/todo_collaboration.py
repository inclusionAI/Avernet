"""In-process adapter from Todo's collaboration contract to ContextCollab."""

from __future__ import annotations

from datetime import datetime

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabParentEventError,
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    ResourceRef,
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ActorContext,
    ContextCollabCreatedResult,
    ContextCollabServiceProtocol,
    ContextCollabStatusProjection,
    ContextCreateCommand,
    EventAppendCommand,
)
from agentclaw.community.plugin_api.context_collab_runtime import (
    ContextCollabRuntimeError,
)
from agentclaw.community.plugin_api.impl_registry import Mode, plugin_impl
from agentclaw.community.plugin_api.todo_collaboration import (
    TodoCollaborationArtifact,
    TodoCollaborationContext,
    TodoCollaborationContextRequest,
    TodoCollaborationError,
    TodoCollaborationEvent,
    TodoCollaborationEventRequest,
    TodoCollaborationPlugin,
)


_CONTEXT_COLLAB_DOMAIN_ERRORS = (
    ContextCollabNotFoundError,
    ContextCollabParentEventError,
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)


@plugin_impl(
    mode=Mode.PROD,
    rationale="adapt Todo directly to the in-process ContextCollab Service API",
)
class InProcessTodoCollaborationPlugin(TodoCollaborationPlugin):
    """Translate Todo requests without routing an internal call through HTTP."""

    def __init__(self, service: ContextCollabServiceProtocol) -> None:
        self._service = service

    def create_context(
        self, request: TodoCollaborationContextRequest
    ) -> TodoCollaborationContext:
        try:
            result = self._service.create_context(
                ContextCreateCommand(
                    space=SpaceRef(request.space_id, request.environment),
                    principal_ref=PrincipalRef("human", request.principal_id),
                    product_kind=request.product_kind,
                    workflow_kind=request.workflow_kind,
                    method_kind=request.method_kind,
                    executor_ref=AgentRef("bot", request.executor_id),
                    objective=request.objective,
                    resource_refs=[
                        ResourceRef(
                            resource_type="todo",
                            resource_id=request.todo_id,
                            revision=None,
                        ),
                    ],
                    executor_owner_id=request.executor_owner_id,
                ),
                _actor_context(request.principal_id, "human"),
            )
        except _CONTEXT_COLLAB_DOMAIN_ERRORS as exc:
            raise TodoCollaborationError(
                "ContextCollab rejected Todo context creation"
            ) from exc
        return _created_context(result)

    def get_context(
        self, *, collab_context_id: str, actor_id: str
    ) -> TodoCollaborationContext:
        projection = self._get_projection(
            collab_context_id,
            _actor_context(actor_id, "human"),
        )
        return _projected_context(projection)

    async def run_context(
        self, *, collab_context_id: str, actor_id: str
    ) -> TodoCollaborationContext:
        actor = _actor_context(actor_id, "human")
        try:
            await self._service.run_context(collab_context_id, actor)
        except ContextCollabRuntimeError as exc:
            raise TodoCollaborationError(
                "ContextCollab runtime could not start the Todo executor"
            ) from exc
        except _CONTEXT_COLLAB_DOMAIN_ERRORS as exc:
            raise TodoCollaborationError(
                "ContextCollab rejected the Todo run request"
            ) from exc
        return _projected_context(self._get_projection(collab_context_id, actor))

    def append_event(
        self, *, collab_context_id: str, request: TodoCollaborationEventRequest
    ) -> TodoCollaborationEvent:
        actor = _actor_context(request.actor_id, request.actor_type)
        try:
            accepted = self._service.append_event(
                collab_context_id,
                EventAppendCommand(
                    expected_version=request.expected_version,
                    event_kind=request.event_kind,
                    primitive_type=request.primitive_type,
                    actor_ref=actor.actor,
                    parent_event=request.parent_event,
                    visibility=request.visibility,
                    payload=request.payload,
                ),
                actor,
            )
        except _CONTEXT_COLLAB_DOMAIN_ERRORS as exc:
            raise TodoCollaborationError(
                "ContextCollab rejected the Todo event"
            ) from exc
        context = _projected_context(self._get_projection(collab_context_id, actor))
        for event in context.events:
            if event.event_id == accepted.event_id:
                return event
        raise TodoCollaborationError(
            f"ContextCollab accepted event {accepted.event_id!r} but did not project it"
        )

    def _get_projection(
        self,
        collab_context_id: str,
        actor: ActorContext,
    ) -> ContextCollabStatusProjection:
        try:
            return self._service.get_status(
                collab_context_id,
                trunk_events=False,
                context=actor,
            )
        except _CONTEXT_COLLAB_DOMAIN_ERRORS as exc:
            raise TodoCollaborationError(
                "ContextCollab could not project the Todo context"
            ) from exc


def _actor_context(actor_id: str, actor_type: str) -> ActorContext:
    return ActorContext(
        actor=PrincipalRef(actor_type, actor_id),
        source="todo",
    )


def _created_context(result: ContextCollabCreatedResult) -> TodoCollaborationContext:
    snapshot = _dict(result.current_snapshot)
    progress = _dict(snapshot.get("progress"))
    current_event = _dict(result.current_event)
    current_event_id = progress.get("current_event_id") or current_event.get("event_id")
    return TodoCollaborationContext(
        collab_context_id=_required_string(
            result.collab_context_id,
            "collab_context_id",
        ),
        status=_required_string(snapshot.get("status"), "current_snapshot.status"),
        context_version=_required_int(result.context_version, "context_version"),
        current_event_id=_optional_string(current_event_id),
    )


def _projected_context(
    projection: ContextCollabStatusProjection,
) -> TodoCollaborationContext:
    snapshot = _dict(projection.current_snapshot)
    progress = _dict(snapshot.get("progress"))
    current_event = _dict(projection.current_event)
    current_event_id = progress.get("current_event_id") or current_event.get("event_id")
    raw_events = projection.task_progress.events
    if not isinstance(raw_events, list):
        raise TodoCollaborationError(
            "ContextCollab projection has invalid task_progress.events"
        )
    return TodoCollaborationContext(
        collab_context_id=_required_string(
            projection.collab_context_id,
            "collab_context_id",
        ),
        status=_required_string(snapshot.get("status"), "current_snapshot.status"),
        context_version=_required_int(
            projection.context_version,
            "context_version",
        ),
        current_event_id=_optional_string(current_event_id),
        events=tuple(_parse_event(_dict(item)) for item in raw_events),
    )


def _parse_event(record: dict) -> TodoCollaborationEvent:
    artifacts_raw = record.get("artifacts")
    artifacts: tuple[TodoCollaborationArtifact, ...] = ()
    if isinstance(artifacts_raw, list):
        artifacts = tuple(
            artifact
            for artifact in (_parse_artifact(_dict(item)) for item in artifacts_raw)
            if artifact is not None
        )
    created_at = record.get("created_at")
    if isinstance(created_at, datetime):
        created_at = created_at.isoformat()
    return TodoCollaborationEvent(
        event_id=_required_string(record.get("event_id"), "event_id"),
        event_seq=_required_int(record.get("event_seq"), "event_seq"),
        event_type=_required_string(record.get("event_kind"), "event_kind"),
        actor_id=_string(record.get("actor_id")),
        actor_type=_string(record.get("actor_type")),
        content=_string(record.get("content") or record.get("summary")),
        execution_id=_string(record.get("execution_id")),
        context_version=_optional_int(record.get("context_version")),
        status=_optional_string(record.get("status")),
        artifacts=artifacts,
        created_at=_required_string(created_at, "created_at"),
    )


def _parse_artifact(record: dict) -> TodoCollaborationArtifact | None:
    url = _string(record.get("url") or record.get("address"))
    if not url:
        return None
    return TodoCollaborationArtifact(
        artifact_id=_string(record.get("artifact_id") or record.get("artifactId")),
        name=_string(record.get("name") or record.get("artifact_name"), "未命名产物"),
        url=url,
    )


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _string(value: object, fallback: str = "") -> str:
    if isinstance(value, str) and value.strip():
        return value
    if value is None:
        return fallback
    return str(value)


def _required_string(value: object, field: str) -> str:
    normalized = _string(value)
    if not normalized:
        raise TodoCollaborationError(f"ContextCollab projection has invalid {field}")
    return normalized


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return _string(value)


def _required_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise TodoCollaborationError(f"ContextCollab projection has invalid {field}")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise TodoCollaborationError(
            f"ContextCollab projection has invalid {field}"
        ) from exc


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return _required_int(value, "context_version")
