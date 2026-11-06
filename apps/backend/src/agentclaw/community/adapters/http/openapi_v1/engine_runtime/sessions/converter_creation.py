"""Convert the authoritative OpenClaw create result without runtime readbacks."""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.engine_runtime.errors import EngineUpstreamError


def convert_created_session(
    *, engine_type: str, created_item: dict[str, Any], requested_title: str | None,
) -> dict[str, Any]:
    """Use Adapter creation metadata while preserving the public Session shape.

    OpenClaw Adapter POST /api/sessions adds ext_info.openclaw_creation with
    session_id and model from the successful sessions.patch response. Its
    legacy id/model remain unchanged for direct clients. An updated Adapter
    is required; missing metadata never triggers a list or history lookup.
    """
    if engine_type.lower() != "openclaw":
        return created_item

    ext_info = created_item.get("ext_info")
    creation = ext_info.get("openclaw_creation") if isinstance(ext_info, dict) else None
    if not isinstance(creation, dict):
        raise EngineUpstreamError("OpenClaw create response is missing creation metadata")
    session_id = creation.get("session_id")
    model = creation.get("model")
    if not isinstance(session_id, str) or not session_id.strip():
        raise EngineUpstreamError("OpenClaw create response is missing its canonical session id")
    if not isinstance(model, str) or not model.strip():
        raise EngineUpstreamError("OpenClaw create response is missing its resolved model")

    item = {**created_item, "id": session_id, "model": model}
    if requested_title is not None:
        item["title"] = requested_title
    return item
