"""HTTP implementation of Todo's B-side collaboration Plugin API."""

from __future__ import annotations

from datetime import datetime

import httpx

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

_CONTEXT_PATH = "/api/v1/collaboration/task/context_collab"


@plugin_impl(
    mode=Mode.PROD,
    rationale="direct HTTP adapter for the collaboration Context service",
)
class HttpTodoCollaborationPlugin(TodoCollaborationPlugin):
    """Synchronous B adapter with no retries or local event cache."""

    def __init__(self, *, base_url: str, timeout_seconds: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def create_context(
        self, request: TodoCollaborationContextRequest
    ) -> TodoCollaborationContext:
        data = self._request(
            "POST",
            _CONTEXT_PATH,
            {
                "space": {
                    "space_id": request.space_id,
                    "environment": request.environment,
                },
                "principal_ref": {
                    "principal_type": "human",
                    "principal_id": request.principal_id,
                },
                "product_kind": request.product_kind,
                "workflow_kind": request.workflow_kind,
                "method_kind": request.method_kind,
                "executor_ref": {
                    "agent_type": "bot",
                    "agent_id": request.executor_id,
                },
                "objective": request.objective,
                "resource_refs": [
                    {
                        "resource_type": "todo",
                        "resource_id": request.todo_id,
                        "revision": None,
                    }
                ],
            },
        )
        return _parse_context(_dict(data))

    def get_context(self, *, collab_context_id: str) -> TodoCollaborationContext:
        data = self._request("GET", f"{_CONTEXT_PATH}/{collab_context_id}")
        return _parse_context(_dict(data))

    def run_context(self, *, collab_context_id: str) -> TodoCollaborationContext:
        self._request("POST", f"{_CONTEXT_PATH}/{collab_context_id}/run")
        return self.get_context(collab_context_id=collab_context_id)

    def append_event(
        self, *, collab_context_id: str, request: TodoCollaborationEventRequest
    ) -> TodoCollaborationEvent:
        accepted = _dict(
            self._request(
                "POST",
                f"{_CONTEXT_PATH}/{collab_context_id}/events",
                {
                    "expected_version": request.expected_version,
                    "event_kind": request.event_kind,
                    "primitive_type": request.primitive_type,
                    "actor_ref": {
                        "principal_type": request.actor_type,
                        "principal_id": request.actor_id,
                    },
                    "parent_event": request.parent_event,
                    "visibility": request.visibility,
                    "payload": request.payload,
                },
            )
        )
        event_id = _required_string(accepted.get("event_id"), "event_id")
        context = self.get_context(collab_context_id=collab_context_id)
        for event in context.events:
            if event.event_id == event_id:
                return event
        raise TodoCollaborationError(
            f"B accepted event {event_id!r} but did not project it"
        )

    def _request(self, method: str, path: str, json: dict | None = None) -> object:
        if not self._base_url:
            raise TodoCollaborationError("Todo collaboration service is not configured")
        try:
            with httpx.Client(
                base_url=self._base_url, timeout=self._timeout_seconds
            ) as client:
                response = (
                    client.post(path, json=json)
                    if method == "POST"
                    else client.get(path)
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise TodoCollaborationError("B collaboration request failed") from exc
        except ValueError as exc:
            raise TodoCollaborationError(
                "B collaboration response is not JSON"
            ) from exc
        data = payload.get("data") if isinstance(payload, dict) else payload
        if data is None:
            raise TodoCollaborationError("B collaboration response is malformed")
        return data


def _parse_context(record: dict) -> TodoCollaborationContext:
    snapshot = _dict(record.get("current_snapshot"))
    progress = _dict(snapshot.get("progress"))
    current_event = _dict(record.get("current_event"))
    task_progress = _dict(record.get("task_progress"))
    raw_events = task_progress.get("events", [])
    if not isinstance(raw_events, list):
        raise TodoCollaborationError(
            "B collaboration response has invalid task_progress.events"
        )
    current_event_id = progress.get("current_event_id")
    if current_event_id is None:
        current_event_id = current_event.get("event_id")
    return TodoCollaborationContext(
        collab_context_id=_required_string(
            record.get("collab_context_id"), "collab_context_id"
        ),
        status=_required_string(snapshot.get("status"), "current_snapshot.status"),
        context_version=_required_int(record.get("context_version"), "context_version"),
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
        raise TodoCollaborationError(f"B collaboration response has invalid {field}")
    return normalized


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return _string(value)


def _required_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise TodoCollaborationError(f"B collaboration response has invalid {field}")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise TodoCollaborationError(
            f"B collaboration response has invalid {field}"
        ) from exc


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return _required_int(value, "context_version")
