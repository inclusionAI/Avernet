"""TDD for the 副屏 panel carrier transport (Phase 4.5.3, plan §4.5.3 / FR-OBS-11).

Verifies the format function (mirrors BCS ``format_state_machine_panel_message``),
the publisher enriching the event with the formatted ``<AixUI panel>`` content,
and the carrier subscriber relaying to :class:`PanelDeliveryPort`.
"""
from __future__ import annotations

import re

from agentclaw.community.api.task import PanelMessage
from agentclaw.community.core.events.bus import EventBus
from agentclaw.community.plugins.community.task.panel_carrier import (
    RecordingPanelDelivery,
    TaskPanelCarrier,
)
from agentclaw.community.plugins.community.task.panel_publisher import (
    EventBusPanelPublisher,
    format_task_panel_message,
)


# --- format_task_panel_message ---------------------------------------------


def test_format_task_panel_message_has_component_and_task_id():
    msg = format_task_panel_message("task-abc", "my goal")
    assert 'component="taskPanel.TaskWorkflowView"' in msg
    assert "task-abc" in msg
    assert "my goal" in msg  # title surfaces in the tab title


def test_format_task_panel_message_is_self_closing_aixui_panel():
    msg = format_task_panel_message("task-1")
    # self-closing <AixUI ... /> with type=panel + a component attr — matches
    # the frontend hasAixPanelContent self-closing regex.
    assert re.match(r"<AixUI\b[\s\S]*type=\"panel\"[\s\S]*component=[\s\S]*\/>", msg)
    assert "type=\"panel\"" in msg


def test_format_task_panel_message_escapes_single_quotes_in_attrs():
    msg = format_task_panel_message("task-1", "title with 'quote'")
    # the tab/params attrs are wrapped in single quotes; inner quotes are escaped
    assert "&#39;" in msg


# --- publisher enriches the event ------------------------------------------


def test_publisher_formats_content_into_event():
    bus = EventBus()
    captured: list = []
    from agentclaw.community.plugins.community.task.panel_publisher import (
        TaskPanelEvent,
    )

    bus.subscribe(TaskPanelEvent, lambda e: captured.append(e))
    # point the publisher at our isolated bus
    pub = EventBusPanelPublisher()
    # the publisher uses the module-level get_event_bus(); swap it for isolation
    import agentclaw.community.plugins.community.task.panel_publisher as mod

    orig = mod.get_event_bus
    mod.get_event_bus = lambda: bus  # type: ignore[assignment]
    try:
        pub.publish(PanelMessage(component="taskPanel.TaskWorkflowView", params={"task_id": "task-9"}))
    finally:
        mod.get_event_bus = orig  # type: ignore[assignment]

    assert len(captured) == 1
    ev = captured[0]
    assert ev.component == "taskPanel.TaskWorkflowView"
    assert "task-9" in ev.content
    assert 'component="taskPanel.TaskWorkflowView"' in ev.content


# --- carrier → delivery port -----------------------------------------------


def test_carrier_relays_event_content_to_delivery():
    bus = EventBus()
    delivery = RecordingPanelDelivery()
    carrier = TaskPanelCarrier(delivery, bus=bus)
    carrier.install(bus=bus)

    from agentclaw.community.plugins.community.task.panel_publisher import (
        TaskPanelEvent,
    )

    bus.publish(
        TaskPanelEvent(
            component="taskPanel.TaskWorkflowView",
            params={"task_id": "task-1"},
            content=format_task_panel_message("task-1"),
            session_id="sess-7",
        )
    )
    assert len(delivery.delivered) == 1
    session_id, content = delivery.delivered[0]
    assert session_id == "sess-7"
    assert "task-1" in content


def test_carrier_skips_empty_content():
    bus = EventBus()
    delivery = RecordingPanelDelivery()
    TaskPanelCarrier(delivery, bus=bus).install(bus=bus)
    from agentclaw.community.plugins.community.task.panel_publisher import (
        TaskPanelEvent,
    )

    bus.publish(TaskPanelEvent(component="x", content=""))
    assert delivery.delivered == []


def test_carrier_swallows_delivery_failure():
    bus = EventBus()
    delivery = RecordingPanelDelivery()

    def _boom(*_a, **_k):
        raise RuntimeError("push failed")

    delivery.deliver = _boom  # type: ignore[assignment]
    TaskPanelCarrier(delivery, bus=bus).install(bus=bus)
    from agentclaw.community.plugins.community.task.panel_publisher import (
        TaskPanelEvent,
    )

    # must NOT raise — the create path never blocks on a delivery failure
    bus.publish(
        TaskPanelEvent(
            component="x",
            content=format_task_panel_message("task-1"),
        )
    )


# --- end-to-end: publish → carrier → delivery ------------------------------


def test_end_to_end_publish_to_delivery():
    bus = EventBus()
    delivery = RecordingPanelDelivery()
    TaskPanelCarrier(delivery, bus=bus).install(bus=bus)

    pub = EventBusPanelPublisher()
    import agentclaw.community.plugins.community.task.panel_publisher as mod

    orig = mod.get_event_bus
    mod.get_event_bus = lambda: bus  # type: ignore[assignment]
    try:
        pub.publish(
            PanelMessage(
                component="taskPanel.TaskWorkflowView",
                params={"task_id": "task-e2e", "title": "goal"},
            )
        )
    finally:
        mod.get_event_bus = orig  # type: ignore[assignment]

    assert len(delivery.delivered) == 1
    _sid, content = delivery.delivered[0]
    assert 'component="taskPanel.TaskWorkflowView"' in content
    assert "task-e2e" in content