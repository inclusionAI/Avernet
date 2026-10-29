"""Closed-set StrEnums and value objects for the agentswarm domain (D18 vocabulary)."""
from __future__ import annotations

from dataclasses import dataclass
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
    BOT = "bot"


class BotState(StrEnum):
    PROVISIONING = "PROVISIONING"
    AWAITING_AUTHORIZATION = "AWAITING_AUTHORIZATION"
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


class DispatchStatus(StrEnum):
    READY = "READY"
    LEASED = "LEASED"
    DONE = "DONE"
    FAILED = "FAILED"


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
    """Which serving substrate backs the run (wire + ac_agent_swarm_run.provider)."""

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

@dataclass(frozen=True)
class RunOptions:
    """Per-run operational parameters — the run's own execution contract.

    Every field is a client-visible request parameter; None falls to
    the deployment config. Lives here (domain models) so guards, handler,
    orchestrator, and pool can import it without service-layer coupling."""

    model_name: str | None = None
    slice_max_seconds: float | None = None
    per_attempt_timeout_seconds: float | None = None
    setup_timeout_seconds: float | None = None
    max_turns: int | None = None
    max_total_agents: int | None = None
    max_width: int | None = None
    max_attempts: int | None = None

    @classmethod
    def from_dto(cls, dto) -> "RunOptions":
        if dto is None:
            return cls()
        return cls(
            model_name=dto.model_name,
            slice_max_seconds=dto.slice_max_seconds,
            per_attempt_timeout_seconds=dto.per_attempt_timeout_seconds,
            setup_timeout_seconds=dto.setup_timeout_seconds,
            max_turns=dto.max_turns,
            max_total_agents=dto.max_total_agents,
            max_width=dto.max_width,
            max_attempts=getattr(dto, "max_attempts", None),
        )

    @classmethod
    def from_metadata(cls, md) -> "RunOptions":
        # Accept either a RequestMetadata object (attribute ``extend``) or the
        # raw decoded dict read back from the run row — the repository hands
        # over whatever it loaded, and both shapes carry the same keys.
        if isinstance(md, dict):
            ext = md.get("extend") or {}
        else:
            ext = (getattr(md, "extend", None) or {}) if md else {}
        ext = ext if isinstance(ext, dict) else {}
        prefix = "agentswarm.options"

        def val(key, cast):
            raw = ext.get(f"{prefix}.{key}")
            return cast(raw) if raw is not None else None

        return cls(
            model_name=ext.get(f"{prefix}.model_name"),
            slice_max_seconds=val("slice_max_seconds", float),
            per_attempt_timeout_seconds=val("per_attempt_timeout_seconds", float),
            setup_timeout_seconds=val("setup_timeout_seconds", float),
            max_turns=val("max_turns", int),
            max_total_agents=val("max_total_agents", int),
            max_width=val("max_width", int),
            max_attempts=val("max_attempts", int),
        )

    def to_metadata(self, md) -> None:
        for key, v in {
            "model_name": self.model_name,
            "slice_max_seconds": self.slice_max_seconds,
            "per_attempt_timeout_seconds": self.per_attempt_timeout_seconds,
            "setup_timeout_seconds": self.setup_timeout_seconds,
            "max_turns": self.max_turns,
            "max_total_agents": self.max_total_agents,
            "max_width": self.max_width,
            "max_attempts": self.max_attempts,
        }.items():
            if v is not None:
                md.extend[f"agentswarm.options.{key}"] = str(v)

    def resolved_setup_timeout(self, config_value: float,
                               ceiling: float) -> float:
        """Effective provisioning budget (spawn + authorization + READY).

        The deployment's ``orchestration.setup_timeout`` is the fallback;
        an explicitly requested value wins up to ``ceiling`` — the slice
        room the deployment configuration leaves (``slice_max_seconds``
        minus ``per_attempt_timeout``), so a caller can lengthen slow-env
        provisioning from the request alone (no redeploy) but can never
        push one agent's setup past what a single slice can hold."""
        if self.setup_timeout_seconds is None:
            return float(config_value)
        return max(1.0, min(float(self.setup_timeout_seconds), float(ceiling)))

    def resolved_per_attempt_timeout(self, config_value: float) -> float:
        """Effective one-attempt task budget: tighten-only. The deployment's
        ``orchestration.per_attempt_timeout`` is both fallback and cap — a
        per-run value can never outgrow the slice room its setup budget
        already occupies."""
        if self.per_attempt_timeout_seconds is None:
            return float(config_value)
        return max(1.0, min(float(self.per_attempt_timeout_seconds),
                           float(config_value)))

    def resolved_slice_max(self, config_value: float) -> float:
        """Effective slice budget: tighten-only. The deployment's
        ``orchestration.slice_max_seconds`` stays the ceiling so one run
        cannot stretch the scheduling quantum past fleet norms."""
        if self.slice_max_seconds is None:
            return float(config_value)
        return max(1.0, min(float(self.slice_max_seconds), float(config_value)))

    # Mirrors RunOptionsDTO's wire caps: submit validates live requests,
    # this re-clamps stale metadata rows that predate a schema change.
    _WIRE_MAX_TURNS_CAP = 999
    _WIRE_MAX_TOTAL_AGENTS_CAP = 256

    def resolved_max_turns(self, config_value: int) -> int:
        """Effective orchestrator turn budget: the request replaces the
        deployment default (clamped). Wait/deferred decisions stay free,
        so a widened budget only matters when real decisions actually
        churn — e.g. spawns blocked behind human-authorization parks."""
        if self.max_turns is None:
            return int(config_value)
        return max(1, min(int(self.max_turns), self._WIRE_MAX_TURNS_CAP))

    def resolved_max_total_agents(self, config_value: int) -> int:
        """Effective spawn ceiling: the request replaces the deployment
        default (clamped)."""
        if self.max_total_agents is None:
            return int(config_value)
        return max(1, min(int(self.max_total_agents),
                          self._WIRE_MAX_TOTAL_AGENTS_CAP))
