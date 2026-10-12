"""Closed-set StrEnums for the agentcompute domain (D18 vocabulary)."""
from __future__ import annotations

from enum import StrEnum


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    GUARD_KILLED = "GUARD_KILLED"
    STUCK = "STUCK"


class AgentStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    KILLED = "KILLED"


class AgentKind(StrEnum):
    LLM = "llm"
    AVENET = "avernet"


class BotState(StrEnum):
    PROVISIONING = "PROVISIONING"
    READY = "READY"
    AUTHORIZATION_REJECTED = "AUTHORIZATION_REJECTED"
    AUTHORIZATION_EXPIRED = "AUTHORIZATION_EXPIRED"
    CREATE_FAILED = "CREATE_FAILED"
    APPLY_FAILED = "APPLY_FAILED"


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    DECISION = "decision"


class GuardKind(StrEnum):
    SERIAL_COLLAPSE = "serial_collapse"
    SPURIOUS_PARALLELISM = "spurious_parallelism"
    DEDUPE_REJECT = "dedupe_reject"
    DEPTH_EXCEEDED = "depth_exceeded"
    WIDTH_EXCEEDED = "width_exceeded"
    TIMEOUT = "timeout"
    KILL = "kill"
    STUCK = "stuck"
    PROMPT_INJECTION = "prompt_injection"


class TerminationReason(StrEnum):
    FINISH = "finish"
    NO_DECISION = "no_decision"
    NO_PROGRESS = "no_progress"
    TURN_BUDGET = "turn_budget"
    MAX_AGENTS = "max_agents"
    WALL_CLOCK = "wall_clock"
    CANCELLED = "cancelled"
    STUCK = "stuck"
    ERROR = "error"
    GUARD = "guard"


class DecisionKind(StrEnum):
    SPAWN_AGENT = "spawn_agent"
    WAIT = "wait"
    HANDOFF = "handoff"
    FINISH = "finish"


class WaitMode(StrEnum):
    ALL = "all"
    ANY = "any"


class LlmCallKind(StrEnum):
    ORCHESTRATOR = "orchestrator"
    SUBAGENT = "subagent"


class WorkerOutcomeKind(StrEnum):
    COMPLETE = "complete"
    RESCHEDULE = "reschedule"
    RETRY = "retry"
    FAIL = "fail"


class TaskQueueStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"


class FinishReason(StrEnum):
    STOP = "stop"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"


class StreamEventKind(StrEnum):
    AGENT_SPAWN = "agent_spawn"
    AGENT_STATE = "agent_state"
    ROUND = "round"
    TERMINAL = "terminal"

class ProviderKind(StrEnum):
    """Which serving substrate backs the run (wire + ac_agent_compute_run.provider)."""

    DEFAULT = "default"
    STUB = "stub"
    AVENET = "avernet"

    @classmethod
    def coerce(cls, value: str | None) -> "ProviderKind":
        """Boundary coercion for wire/row strings, failing safe to DEFAULT."""
        try:
            return cls(value or "")
        except ValueError:
            return cls.DEFAULT
