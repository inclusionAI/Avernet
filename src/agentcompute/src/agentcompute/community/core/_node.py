"""A single DAG node: id, agent name, input, execution status, timing, result."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

__all__ = ["DAGNode", "NodeStatus"]


class NodeStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class DAGNode:
    id: str
    agent: str
    input: dict[str, Any] = field(default_factory=dict)
    status: NodeStatus = NodeStatus.PENDING
    result: Any = None
    error: str | None = None
    progress: float = 0.0
    started_at: str | None = None
    finished_at: str | None = None
    wave: int | None = None
    plan_round: int = 0

    def mark_started(self) -> None:
        self.started_at = _now_iso()

    def mark_finished(self) -> None:
        self.finished_at = _now_iso()

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "agent": self.agent, "input": self.input}

    def to_full_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent": self.agent,
            "input": self.input,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "progress": self.progress,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "wave": self.wave,
            "plan_round": self.plan_round,
        }

    @classmethod
    def from_dict(cls, node_id: str, data: dict[str, Any]) -> DAGNode:
        return cls(id=node_id, agent=data["agent"], input=data.get("input", {}))
