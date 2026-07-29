"""Secondary-panel (副屏) event publishers + carrier (Phase 2.1 / 4.5.3, plan
§1.4b / FR-OBS-11).

The carrier-transport chain (mirrors BCS ``publish_state_machine_panel_event``):

1. ``TaskService.create`` publishes a :class:`PanelMessage` via the injected
   :class:`PanelEventPublisher`.
2. ``EventBusPanelPublisher`` formats the ``<AixUI panel>`` message content
   (:func:`format_task_panel_message`) and publishes a :class:`TaskPanelEvent`
   (content + params + session_id) on the in-process :class:`EventBus`.
3. A carrier subscriber (:class:`TaskPanelCarrier`, wired in the community
   bootstrap) relays the content to the :class:`PanelDeliveryPort` seam. The
   community default is a Noop/recording delivery (no chat-session push bus in
   the open-source profile); the corp/transport-bridge wires a real chat-WS
   ``<AixUI panel>`` push (TODO Phase 6).
4. On the frontend, the ``<AixUI panel>`` message — once it arrives in a chat
   message stream — is detected by ``@aix-chat/ui``'s ``hasAixPanelContent`` /
   ``AixPanelPreviewCard``; and the community create-flow calls
   ``openTaskPanel(taskId)`` directly on the create response (FR-OBS-11 popup
   without needing a chat push).

A recording publisher/carrier is provided for unit tests so they never touch the
global bus.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Optional

from agentclaw.community.core.events.bus import get_event_bus
from agentclaw.community.core.task.protocols import PanelMessage
from agentclaw.community.log import get_logger

logger = get_logger()


def _single_quoted_json_attr(value: str) -> str:
    """Escape single quotes for an HTML attribute wrapped in single quotes,
    matching BCS ``single_quoted_json_attr``."""
    return value.replace("'", "&#39;")


def format_task_panel_message(
    task_id: str,
    title: Optional[str] = None,
) -> str:
    """Format a ``<AixUI panel>`` message for the task-entry dynamic-DAG canvas
    (FR-OBS-11). Mirrors BCS ``format_state_machine_panel_message``:
    ``component=taskPanel.TaskWorkflowView``, ``tab`` carries an id + closable
    title, ``params`` carries the ``task_id`` bind key. The frontend
    ``hasAixPanelContent`` regex matches self-closing ``<AixUI .../>`` with a
    ``component`` + panel type.
    """
    tab_title = f"任务执行 - {title}" if title else "任务执行流程"
    tab_json = _single_quoted_json_attr(
        json.dumps(
            {"id": f"task-run-{task_id}", "title": tab_title, "closable": True},
            ensure_ascii=False,
        )
    )
    params_json = _single_quoted_json_attr(
        json.dumps({"task_id": task_id}, ensure_ascii=False)
    )
    return (
        "<AixUI\n"
        '  type="panel"\n'
        '  component="taskPanel.TaskWorkflowView"\n'
        f"  tab='{tab_json}'\n"
        f"  params='{params_json}'\n"
        "/>"
    )


@dataclass
class TaskPanelEvent:
    """Bus payload for a panel popup directive (carrier envelope).

    ``content`` is the ready-to-relay ``<AixUI panel>`` string; ``session_id``
    is the optional chat session to deliver to (None when the create has no chat
    context — the frontend create-flow handles the popup in that case).
    """

    component: str
    params: dict = field(default_factory=dict)
    content: str = ""
    session_id: Optional[str] = None
    kind: str = "open_panel"


class EventBusPanelPublisher:
    """Publishes a :class:`TaskPanelEvent` on the in-process EventBus.

    Formats the ``<AixUI panel>`` content upfront so a downstream carrier
    relays it verbatim to the chat channel (corp/transport-bridge).
    """

    def publish(self, message: PanelMessage) -> None:
        task_id = str(message.params.get("task_id") or "")
        title = message.params.get("title")
        content = format_task_panel_message(task_id, title) if task_id else ""
        event = TaskPanelEvent(
            component=message.component,
            params=dict(message.params),
            content=content,
            session_id=str(message.params.get("session_id") or "") or None,
            kind=message.kind,
        )
        logger.info(
            "[PanelPublisher] open_panel component=%s task=%s",
            event.component,
            task_id,
        )
        get_event_bus().publish(event)


class RecordingPanelPublisher:
    """Test double — records published messages in order, never touches the bus."""

    def __init__(self) -> None:
        self.published: List[PanelMessage] = []

    def publish(self, message: PanelMessage) -> None:
        self.published.append(message)


__all__ = [
    "EventBusPanelPublisher",
    "RecordingPanelPublisher",
    "TaskPanelEvent",
    "format_task_panel_message",
]