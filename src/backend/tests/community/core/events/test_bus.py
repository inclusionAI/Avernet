"""Tests for agentclaw.community.core.events.bus.EventBus."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.events.bus import (
    EventBus,
    RequiredEventDeliveryError,
    get_event_bus,
    reset_event_bus,
)


@dataclass
class _DummyEvent:
    value: int


@pytest.fixture(autouse=True)
def _reset_bus():
    reset_event_bus()
    yield
    reset_event_bus()


class TestEventBus:
    def test_publish_calls_subscribed_handler(self):
        bus = EventBus()
        handler = MagicMock()
        bus.subscribe(_DummyEvent, handler)

        event = _DummyEvent(value=42)
        bus.publish(event)

        handler.assert_called_once_with(event)

    def test_publish_with_no_subscribers_is_noop(self):
        bus = EventBus()
        # Should not raise
        bus.publish(_DummyEvent(value=1))

    def test_multiple_handlers_called_in_registration_order(self):
        bus = EventBus()
        calls: list[str] = []
        bus.subscribe(_DummyEvent, lambda e: calls.append("a"))
        bus.subscribe(_DummyEvent, lambda e: calls.append("b"))
        bus.subscribe(_DummyEvent, lambda e: calls.append("c"))

        bus.publish(_DummyEvent(value=0))

        assert calls == ["a", "b", "c"]

    def test_handler_exception_does_not_block_other_handlers(self):
        bus = EventBus()
        good_before = MagicMock()
        good_after = MagicMock()

        def bad(_event):
            raise RuntimeError("boom")

        bus.subscribe(_DummyEvent, good_before)
        bus.subscribe(_DummyEvent, bad)
        bus.subscribe(_DummyEvent, good_after)

        bus.publish(_DummyEvent(value=0))

        good_before.assert_called_once()
        good_after.assert_called_once()

    def test_required_handler_exception_is_propagated_after_siblings_run(self):
        bus = EventBus()
        good_after = MagicMock()

        def bad(_event):
            raise RuntimeError("queue unavailable")

        bus.subscribe(_DummyEvent, bad, required=True)
        bus.subscribe(_DummyEvent, good_after)

        with pytest.raises(RequiredEventDeliveryError):
            bus.publish(_DummyEvent(value=0))

        good_after.assert_called_once()

    def test_handlers_only_receive_their_event_type(self):
        @dataclass
        class OtherEvent:
            x: str

        bus = EventBus()
        dummy_handler = MagicMock()
        other_handler = MagicMock()
        bus.subscribe(_DummyEvent, dummy_handler)
        bus.subscribe(OtherEvent, other_handler)

        bus.publish(_DummyEvent(value=1))

        dummy_handler.assert_called_once()
        other_handler.assert_not_called()

    def test_get_event_bus_returns_singleton(self):
        bus1 = get_event_bus()
        bus2 = get_event_bus()
        assert bus1 is bus2

    def test_reset_event_bus_clears_subscribers(self):
        bus = get_event_bus()
        handler = MagicMock()
        bus.subscribe(_DummyEvent, handler)

        reset_event_bus()

        new_bus = get_event_bus()
        new_bus.publish(_DummyEvent(value=1))
        handler.assert_not_called()
