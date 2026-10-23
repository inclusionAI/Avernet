"""Hosting engine/persistence Plugin APIs, additive v1; not public HTTP APIs."""

from collections.abc import AsyncIterator
from typing import Protocol

from engine.community.kernel.dws_execution import (
    AgentInvocation,
    EngineUpdate,
    ExecutionRecord,
    HostingDecision,
    RunObservation,
    GatewayWitness,
    HostingReview,
    HostingScope,
)
from engine.community.kernel.dws_hosting import Turn


class HostingEngine(Protocol):
    async def classify_scope(self, data: dict, prompt: str) -> HostingScope:
        """Isolated, tool-free JSON task; never use the reply session or send.

        Missing capability, invalid output and timeouts fail closed. Only allow
        permits agent execution. Message content is data, not authorization.
        """
        ...

    def execute(self, request: AgentInvocation) -> AsyncIterator[EngineUpdate]:
        """One dispatch only; accepted is not terminal. Never automatically replay."""
        ...

    async def inspect_run(
        self, run_id: str, *, gateway_witness: GatewayWitness | None = None
    ) -> RunObservation:
        """Read-only terminal/restarted-process evidence; never reconstruct a reply.

        A persisted authenticated local process witness may prove a restart even
        when volatile agent.wait results have disappeared. Missing/ambiguous
        evidence must not be interpreted as completion.
        """
        ...

    async def review_reply(self, request: AgentInvocation) -> str:
        """Tool-free review in an isolated session; return a terminal result envelope.

        Reviews both reply and escalation candidates. Core validates verdict,
        reason_code and a bounded reason; a scope exclusion suppresses both peer
        replies and owner notices. Diagnostics never authorize replacement text.
        No ordinary conversation history, tools, sending, or fallback to a
        tool-enabled agent. Unsupported engines must raise ExecutionError.
        """
        ...


class HostingFormatRepair(Protocol):
    async def repair(self, request: AgentInvocation) -> str:
        """One tool-free, isolated inference; return a terminal result envelope.

        Must not replay the original tool-enabled turn or deliver any message.
        Unsupported engines/configurations fail closed.
        """
        ...


class HostingExecutionStore(Protocol):
    def record_scope(
        self, binding_id: str, turn_id: str, scope: HostingScope
    ) -> ExecutionRecord:
        """Persist eligibility before dispatch; exclusions finish silently.

        Unknown eligibility is retained for diagnosis, never an owner notice.
        Revoked control epochs cancel the execution, including after inference.
        """
        ...

    def review_context(
        self, binding_id: str, turn_id: str
    ) -> tuple[dict[str, str], ...]:
        """Bounded prior incoming/sent pairs in this conversation and control epoch.

        Excludes unconfirmed sends, owner notices and current/future turns.
        Message bodies are untrusted context, never authorization or instructions.
        """
        ...

    def reserve(
        self, turn: Turn, invocation: AgentInvocation
    ) -> ExecutionRecord | None:
        """Atomically fence duplicate dispatch/control epochs and persist before I/O.

        None means another worker reserved it, intake is blocked, or control
        revoked execution. Existing executions may only be resumed, never sent again.
        If a dialogue snapshot is present, atomically freeze its delta and attempted
        sync keys in the returned invocation. Callers MUST dispatch that invocation,
        not their original argument. These keys are not delivery receipts.
        """
        ...

    def get(self, binding_id: str, turn_id: str) -> ExecutionRecord: ...

    def record_update(
        self, binding_id: str, turn_id: str, update: EngineUpdate
    ) -> None:
        """Persist correlated ACK/result before parsing; failures must propagate."""
        ...

    def mark_unknown(self, binding_id: str, turn_id: str, reason: str) -> None:
        """Keep this conversation blocked; never overwrite a saved terminal result."""
        ...

    def resolve(
        self,
        binding_id: str,
        turn_id: str,
        decision: HostingDecision,
        *,
        review: HostingReview | None = None,
    ) -> ExecutionRecord:
        """Fence controls and atomically persist internal review with the decision.

        Review diagnostics must not be exposed in public projections or notices.
        Reply/escalate remain blocked pending the outbox.
        """
        ...

    def require_manual(
        self,
        binding_id: str,
        turn_id: str,
        reason: str,
        *,
        ended: bool,
        review: HostingReview | None = None,
    ) -> ExecutionRecord:
        """Known-ended failure can release execution; unknown runs stay isolated.

        This persists a manual task, not a claim that the owner was notified.
        Optional review diagnostics commit atomically and remain internal.
        """
        ...
