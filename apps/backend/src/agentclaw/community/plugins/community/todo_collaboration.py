"""HTTP adapter for the B-side Todo collaboration service.

This is the Plugin implementation, not domain policy: it owns paths, payloads,
response parsing, and transport failures only. Event aggregation and Todo state
decisions remain in Backend's Todo core service.
"""

from __future__ import annotations

import httpx

from agentclaw.community.plugin_api.impl_registry import Mode, plugin_impl
from agentclaw.community.plugin_api.todo_collaboration import (
    TodoCollaborationArtifact,
    TodoCollaborationContext,
    TodoCollaborationContextRequest,
    TodoCollaborationEvent,
    TodoCollaborationEventRequest,
    TodoCollaborationError,
    TodoCollaborationPlugin,
)


_CONTEXT_PATH = "/api/v1/collaboration/task/context_collab"


@plugin_impl(
    mode=Mode.PROD,
    rationale="direct HTTP adapter for the collaboration context/event service",
)
class HttpTodoCollaborationPlugin(TodoCollaborationPlugin):
    """Synchronous B adapter with no retries or local event cache."""

    def __init__(self, *, base_url: str, timeout_seconds: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def create_context(
        self, request: TodoCollaborationContextRequest
    ) -> TodoCollaborationContext:
        payload = {
            "task_id": request.task_id,
            "task_title": request.task_title,
            "collaboration_goal": request.collaboration_goal,
            "collaborators": list(request.collaborators),
            "owner_id": request.owner_id,
            "bot_id": request.bot_id,
        }
        data = self._request("POST", _CONTEXT_PATH, payload)
        collab_context_id = _string(_dict(data).get("collab_context_id"))
        if not collab_context_id:
            raise TodoCollaborationError("B returned no collab_context_id")
        return TodoCollaborationContext(collab_context_id=collab_context_id)

    def append_event(
        self, *, collab_context_id: str, request: TodoCollaborationEventRequest
    ) -> TodoCollaborationEvent:
        payload = {
            "event_type": request.event_type,
            "actor_id": request.actor_id,
            "content": request.content,
            "boundary_event_seq": request.boundary_event_seq,
            **request.payload,
        }
        data = self._request(
            "POST", f"{_CONTEXT_PATH}/{collab_context_id}/events", payload
        )
        return _parse_event(_dict(data))

    def list_events(
        self, *, collab_context_id: str
    ) -> tuple[TodoCollaborationEvent, ...]:
        data = self._request("GET", f"{_CONTEXT_PATH}/{collab_context_id}")
        if isinstance(data, list):
            raw_events = data
        else:
            wrapper = _dict(data)
            raw_events = wrapper.get("events")
            if not isinstance(raw_events, list):
                raw_events = wrapper.get("event_list")
            if not isinstance(raw_events, list):
                raise TodoCollaborationError(
                    "B collaboration response has no event list"
                )
        return tuple(_parse_event(_dict(item)) for item in raw_events)

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
        if isinstance(payload, dict) and "data" in payload:
            data = payload["data"]
        else:
            data = payload
        if data is None:
            raise TodoCollaborationError("B collaboration response is malformed")
        return data


def _parse_event(record: dict) -> TodoCollaborationEvent:
    artifacts_raw = record.get("artifacts")
    artifacts = ()
    if isinstance(artifacts_raw, list):
        artifacts = tuple(
            artifact
            for artifact in (_parse_artifact(_dict(item)) for item in artifacts_raw)
            if artifact is not None
        )
    return TodoCollaborationEvent(
        event_id=_string(record.get("event_id")),
        event_seq=_int(record.get("event_seq")),
        event_type=_string(record.get("event_type"), "event"),
        actor_id=_string(record.get("actor_id")),
        actor_type=_string(record.get("actor_type"), "human"),
        content=_string(record.get("content") or record.get("message")),
        execution_id=_string(record.get("execution_id")),
        context_version=_string(record.get("context_version")),
        status=_optional_string(record.get("status")),
        artifacts=artifacts,
        created_at=_string(record.get("created_at") or record.get("created_time")),
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


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return _string(value)


def _int(value: object, field: str = "event_seq") -> int:
    if isinstance(value, bool):
        raise TodoCollaborationError("B collaboration response has invalid event_seq")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise TodoCollaborationError(
            "B collaboration response has invalid event_seq"
        ) from exc
