"""Boundary diagnostics for task execute request payloads."""

from __future__ import annotations

from typing import Any

from agentclaw.community.adapters.http.task.schemas import TaskInfoRequestDTO


def task_info_request_params_to_dict(body: TaskInfoRequestDTO) -> dict[str, Any]:
    """Return every accepted execute request field for diagnostics."""
    params = body.model_dump(mode="json")
    # ``metadata`` is excluded from domain serialization but still appears in the
    # request body, so entry diagnostics must add it back explicitly.
    params["task_spec"]["metadata"] = (
        body.task_spec.metadata.model_dump(mode="json")
        if body.task_spec.metadata is not None
        else None
    )
    return params
