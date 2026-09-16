"""Bounded, non-blocking TC resource-ready notification coordination."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable
import logging
import time

from agentclaw.community.plugin_api.tc_resource_ready import (
    TcResourceReadyEvent,
    TcResourceReadyPublisherPlugin,
)
from agentclaw.community.core.session_resources.types import (
    SessionResourceRecord,
    SessionResourceStatus,
)
from agentclaw.community.core.tc_file_upload_integrations.service_protocol import (
    TcResourceReadyObserverProtocol,
)

logger = logging.getLogger("tc_resource_ready.coordinator")


class TcResourceReadyCoordinator(TcResourceReadyObserverProtocol):
    """Schedule at most one best-effort attempt per bounded retention window."""

    def __init__(
        self,
        *,
        publisher: TcResourceReadyPublisherPlugin,
        max_in_flight: int = 8,
        dedupe_ttl_seconds: float = 3600.0,
        dedupe_max_entries: int = 10_000,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_in_flight < 1:
            raise ValueError("max_in_flight_must_be_positive")
        if dedupe_ttl_seconds <= 0:
            raise ValueError("dedupe_ttl_seconds_must_be_positive")
        if dedupe_max_entries < 1:
            raise ValueError("dedupe_max_entries_must_be_positive")
        self._publisher = publisher
        self._max_in_flight = max_in_flight
        self._dedupe_ttl_seconds = dedupe_ttl_seconds
        self._dedupe_max_entries = dedupe_max_entries
        self._monotonic = monotonic
        self._in_flight: set[str] = set()
        self._recent: OrderedDict[str, float] = OrderedDict()
        self._tasks: set[asyncio.Task[None]] = set()

    def notify_in_background(self, resource: SessionResourceRecord) -> None:
        """Reserve and schedule one sidecar attempt without changing TC results."""
        if resource.status is not SessionResourceStatus.READY:
            return

        resource_id = resource.resource_id
        now = self._monotonic()
        self._prune_recent(now)
        if resource_id in self._in_flight or resource_id in self._recent:
            return
        if len(self._in_flight) >= self._max_in_flight:
            logger.warning(
                "tc_resource_ready.overloaded res_id=%s max_in_flight=%s",
                resource_id,
                self._max_in_flight,
            )
            return

        self._in_flight.add(resource_id)
        publish = self._publish(resource_id)
        try:
            task = asyncio.create_task(publish)
        except Exception:
            publish.close()
            self._in_flight.discard(resource_id)
            logger.error(
                "tc_resource_ready.schedule_failed res_id=%s",
                resource_id,
            )
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _publish(self, resource_id: str) -> None:
        event = TcResourceReadyEvent.for_resource(resource_id)
        try:
            await self._publisher.publish(event)
        except asyncio.CancelledError:
            logger.info(
                "tc_resource_ready.cancelled event_id=%s res_id=%s",
                event.event_id,
                event.res_id,
            )
            raise
        except Exception as exc:
            logger.error(
                "tc_resource_ready.publish_failed event_id=%s res_id=%s error_type=%s",
                event.event_id,
                event.res_id,
                type(exc).__name__,
            )
        else:
            logger.info(
                "tc_resource_ready.published event_id=%s res_id=%s",
                event.event_id,
                event.res_id,
            )
            self._remember(resource_id, self._monotonic())
        finally:
            self._in_flight.discard(resource_id)

    def _prune_recent(self, now: float) -> None:
        while self._recent:
            _, expires_at = next(iter(self._recent.items()))
            if expires_at > now:
                break
            self._recent.popitem(last=False)

    def _remember(self, resource_id: str, now: float) -> None:
        self._recent[resource_id] = now + self._dedupe_ttl_seconds
        self._recent.move_to_end(resource_id)
        while len(self._recent) > self._dedupe_max_entries:
            self._recent.popitem(last=False)


__all__ = ["TcResourceReadyCoordinator"]
