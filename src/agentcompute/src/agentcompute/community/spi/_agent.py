"""Agent executor SPI — the full-lifecycle contract an executor implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "Agent",
    "AgentContext",
    "AgentSpec",
    "NodeInput",
    "NodeResult",
]


@dataclass
class AgentSpec:
    """A candidate agent supplied at runtime with enough context to run.

    ``name`` is the identifier used to bind DAG nodes to an executor.
    ``role`` and ``instructions`` are the context handed to the LLM-backed
    executor so it can discharge the role.
    """

    name: str
    role: str
    instructions: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class NodeInput:
    """Inputs bound to one node: which agent runs it and with what goal."""

    agent: str
    goal: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class NodeResult:
    node_id: str
    output: Any = None


@dataclass
class AgentContext:
    """Execution context handed to an agent instance for one node."""

    node_id: str
    goal: str
    upstream: dict[str, Any] = field(default_factory=dict)
    on_progress: Callable[[float], None] | None = None

    def report_progress(self, progress: float) -> None:
        if self.on_progress is not None:
            self.on_progress(progress)


class Agent(ABC):
    """Full-lifecycle contract for an agent executor.

    Lifecycle per DAG node: ``setup`` → ``execute`` → ``teardown`` (guaranteed),
    with ``halt`` for cancellation. Executors are created on demand and
    destroyed after their node completes.
    """

    name: str = "agent"

    @abstractmethod
    def setup(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def execute(self, ctx: AgentContext) -> NodeResult:
        raise NotImplementedError

    def teardown(self) -> None:
        pass

    def halt(self) -> None:
        pass
