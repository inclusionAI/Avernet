"""Typed HTTP contract for dispatching a Relay successor."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TaskDispatchRequestDTO(BaseModel):
    """Dispatch a persisted Relay successor from its current origin generation."""

    model_config = ConfigDict(extra="ignore")

    task_id: str = Field(min_length=1)
    target_node_id: str = Field(min_length=1)
    origin_node_id: str = Field(min_length=1)
    relay_token: str = Field(min_length=1)

    @field_validator(
        "task_id", "target_node_id", "origin_node_id", "relay_token", mode="after"
    )
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be a non-empty string")
        return value


class RelayHandoffDisplayDTO(BaseModel):
    """User-facing handoff facts derived by the Backend after real delivery."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1)
    collaborator_name: str | None = None
    entry_url: str | None = None


class TaskDispatchResultDTO(BaseModel):
    """Minimal dispatch response; the authoritative ``TaskNode`` stays internal."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    node_id: str = Field(min_length=1)
    handoff_display: RelayHandoffDisplayDTO | None = None

def _handoff_target_fact(delivered, planned):
    """Prefer delivery-time facts; before dispatch only planned facts exist."""
    return delivered if delivered is not None else planned


def _target_session_id(delivered, planned) -> str | None:
    for node in (delivered, planned):
        props = getattr(getattr(node, "run_info", None), "extend_props", None) or {}
        value = str(props.get("session_id") or "").strip()
        if value:
            return value
    return None


def _target_collaborator_name(delivered, planned) -> str | None:
    for node in (delivered, planned):
        props = getattr(getattr(node, "run_info", None), "extend_props", None) or {}
        name = str(props.get("assignee_name") or "").strip()
        if name:
            return name
        formation = props.get("pending_group_formation")
        if isinstance(formation, dict):
            group_name = str(formation.get("group_name") or "").strip()
            if group_name:
                return group_name
    return None


def _handoff_bot_identity(delivered, planned) -> str | None:
    """Return the complete Bot address usable by the Workbench chat route."""
    for node in (delivered, planned):
        run_info = getattr(node, "run_info", None)
        if run_info is None:
            continue
        mode = getattr(run_info, "run_mode", None)
        props = getattr(run_info, "extend_props", None) or {}
        if mode == "coop_group":
            formation = props.get("pending_group_formation")
            if not isinstance(formation, dict):
                continue
            manager = str(
                formation.get("extend_props", {}).get("manager_bot_id") or ""
            ).strip()
            if manager:
                return manager
            bot_ids = formation.get("bot_ids")
            if isinstance(bot_ids, list) and bot_ids:
                return str(bot_ids[0]).strip()
        elif mode == "single_bot":
            assignee = str(getattr(run_info, "assignee", "") or "").strip()
            owner_id = str(props.get("assignee_owner_id") or "").strip()
            if assignee and owner_id:
                product_bot_id = assignee.partition(":")[0]
                return f"{product_bot_id}:{owner_id}"
    return None


def _handoff_entry_url(delivered, planned, frontend_url: str) -> str | None:
    session_id = _target_session_id(delivered, planned)
    bot_identity = _handoff_bot_identity(delivered, planned)
    base = str(frontend_url or "").strip().rstrip("/")
    if not session_id or not bot_identity or not base:
        return None
    target = _handoff_target_fact(delivered, planned)
    mode = getattr(getattr(target, "run_info", None), "run_mode", None)
    section = "team" if mode == "coop_group" else "managed"
    query = urlencode({"section": section, "bot": bot_identity, "session": session_id})
    return f"{base}/workspace/chat?{query}"


def build_dispatch_handoff_display(delivered, planned, frontend_url: str) -> RelayHandoffDisplayDTO:
    """Build the single display projection for a successfully dispatched successor."""
    subject = "Relay handoff"
    for node in (delivered, planned):
        context = getattr(getattr(node, "task_spec", None), "context", None)
        title = str(getattr(context, "title", "") or "").strip()
        if title:
            subject = title
            break
        task_id = str(getattr(node, "task_id", "") or "").strip()
        if task_id:
            subject = task_id
    return RelayHandoffDisplayDTO(
        subject=f"{datetime.now().strftime('%m%d')}:{subject}",
        collaborator_name=_target_collaborator_name(delivered, planned),
        entry_url=_handoff_entry_url(delivered, planned, frontend_url),
    )
