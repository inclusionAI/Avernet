"""Injection-only replay seam for Group Context density."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

GROUP_ID = "group-context-density-1"
SESSION_ID = "session-context-density-1"
DRIVER_BOT_ID = "bot-pm-context-density-1"
CONTEXT_NAME = "cli_e2e_context_density"
GROUP_CONTENT = "initial group context from cli e2e"
UPDATED_CONTENT = "updated group context from cli e2e"
SESSION_CONTENT = "session group context from cli e2e"
ROUND_CONTENT = "round group context from cli e2e"


@dataclass(frozen=True)
class Observation:
    status: int
    body: Any


class DensityTransport(Protocol):
    def request(self, operation: str, obligation_id: str) -> Observation:
        """Return one deterministic observation without opening a transport."""


class ReplayTransport:
    """Offline operation-keyed replay with call recording."""

    def __init__(self, observations: dict[str, Observation]):
        self.observations = observations
        self.calls: list[tuple[str, str]] = []

    def request(self, operation: str, obligation_id: str) -> Observation:
        self.calls.append((operation, obligation_id))
        if operation not in self.observations:
            raise KeyError(operation)
        return self.observations[operation]


def canonical_replay_observations() -> dict[str, Observation]:
    contexts = [
        {"name": CONTEXT_NAME, "content": GROUP_CONTENT, "scope_level": "group"},
        {"name": CONTEXT_NAME, "content": SESSION_CONTENT, "scope_level": "session"},
        {"name": CONTEXT_NAME, "content": ROUND_CONTENT, "scope_level": "round"},
    ]
    return {
        "group_context.workspace": Observation(200, {
            "group_id": GROUP_ID, "session_id": SESSION_ID, "driver_bot": DRIVER_BOT_ID}),
        "group_context.add.group": Observation(200, {"status": "ok"}),
        "group_context.add.session": Observation(200, {"status": "ok"}),
        "group_context.add.round": Observation(200, {"status": "ok"}),
        "group_context.get.initial": Observation(200, {
            "items": [{"name": CONTEXT_NAME, "content": GROUP_CONTENT}]}),
        "group_context.update.group": Observation(200, {"status": "ok"}),
        "group_context.list.entries": Observation(200, {
            "contexts": [
                {"name": CONTEXT_NAME, "permission": "WRO", "scope_level": "group"},
                {"name": CONTEXT_NAME, "permission": "WRO", "scope_level": "session"},
                {"name": CONTEXT_NAME, "permission": "WRO", "scope_level": "round"},
            ]}),
        "group_context.get.updated": Observation(200, {
            "items": [{"name": CONTEXT_NAME, "content": UPDATED_CONTENT}]}),
        "group_context.cleanup": Observation(200, {"deleted": True}),
    }


def canonical_replay_transport() -> ReplayTransport:
    return ReplayTransport(canonical_replay_observations())
