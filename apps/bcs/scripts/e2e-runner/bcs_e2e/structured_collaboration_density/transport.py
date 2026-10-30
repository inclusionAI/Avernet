"""Offline transport seam and canonical structured-collaboration replay."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

@dataclass(frozen=True)
class Observation:
    status: int
    body: Any

class DensityTransport(Protocol):
    def request(self, operation: str, obligation_id: str) -> Observation:
        """Return one local observation; implementations must not hide errors."""

class ReplayTransport:
    def __init__(self, observations: dict[str, Observation]):
        self.observations = observations
        self.calls: list[tuple[str, str]] = []

    def request(self, operation: str, obligation_id: str) -> Observation:
        self.calls.append((operation, obligation_id))
        if operation not in self.observations:
            raise KeyError(operation)
        return self.observations[operation]

GROUP_ID = "group-structured-collaboration-density-1"
DEFINITION_ID = "definition-structured-collaboration-density-1"
RUN_ID = "run-structured-collaboration-density-1"


def canonical_replay_observations() -> dict[str, Observation]:
    return {
        "templates.list": Observation(200, {"templates": [{"id": "single-bot-guided-answer"}]}),
        "templates.detail": Observation(200, {"id": "single-bot-guided-answer", "lang": "zh-CN", "yaml": "name: E2E Guided Answer"}),
        "group.create": Observation(200, {"id": GROUP_ID}),
        "group.read": Observation(200, {"group_strategy": "state_machine"}),
        "definition.read": Observation(200, {"default_definition": {"id": DEFINITION_ID, "version": 7}, "yaml_source": "original"}),
        "definition.patch": Observation(200, {"default_definition": {"id": DEFINITION_ID, "version": 8}, "body": "Draft and verify a concise release recommendation."}),
        "definition.upgrade": Observation(404, {"error": f"{DEFINITION_ID} @107 not found"}),
        "run.start": Observation(202, {"run": {"run_id": RUN_ID, "group_id": GROUP_ID}, "nodes": [{"node_id": "answer"}]}),
        "run.read": Observation(200, {"run": {"run_id": RUN_ID, "status": "running"}}),
        "run.graph": Observation(200, {"run": {"run_id": RUN_ID}, "nodes": [{"node_id": "answer"}]}),
        "run.node": Observation(200, {"node": {"node_id": "answer", "run_id": RUN_ID}}),
        "run.pending-human": Observation(200, []),
        "run.human-response": Observation(400, {"error": {"message": "not a human_input node"}}),
        "run.cancel": Observation(200, {"run": {"status": "aborted"}}),
        "run.terminal": Observation(200, {"run": {"status": "aborted"}}),
        "run.rerun": Observation(409, {"error": {"message": "must be failed before rerun"}}),
        "group.delete": Observation(200, {"id": GROUP_ID}),
    }


def canonical_replay_transport() -> ReplayTransport:
    return ReplayTransport(canonical_replay_observations())
