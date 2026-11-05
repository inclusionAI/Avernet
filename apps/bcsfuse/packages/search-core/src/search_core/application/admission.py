"""Immediate, shared HTTP/TR admission on the search application's event loop."""

from contextlib import contextmanager

from search_core.api.errors import SearchOverloaded
from search_core.application.emergency import EmergencyControl
from search_core.core.observation import events, search_transport


class SearchAdmission:
    def __init__(self, max_inflight: int, emergency: EmergencyControl | None = None):
        self.max_inflight = max_inflight
        self.emergency = emergency
        self._active = 0

    @contextmanager
    def admit(self, transport: str):
        snapshot = self.emergency.current if self.emergency is not None else None
        limit = snapshot.policy.max_inflight_requests if snapshot else self.max_inflight
        if limit and self._active >= limit:
            events.record(
                "search_admission",
                transport,
                "capacity_exceeded",
                active_requests=self._active,
                max_inflight_requests=limit,
            )
            raise SearchOverloaded("search capacity exceeded")
        self._active += 1
        token = search_transport.set(transport)
        try:
            if snapshot is not None:
                with self.emergency.bind(snapshot):
                    yield
            else:
                yield
        finally:
            search_transport.reset(token)
            self._active -= 1
