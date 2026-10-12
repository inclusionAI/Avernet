"""agentcompute observability facade — the D39 metric surface (task 12.4).

Every durable event the swarm produces that D39 says an operator must be
able to see — slice latency, agent births and deaths, LLM call outcomes,
tokens per tenant, shard persistence, STUCK breaker trips, queue depth —
is counted through one small object owned by this module. The facade does
not know any registry: it translates typed domain values (``AgentKind``,
``AgentStatus``, ``TokenUsage``) into fixed metric names + label maps on
an injected ``MetricsPlugin`` (D35: metrics are platform operational
infrastructure, reached through the seam, never a vendor SDK import).

Canonical metric names (the complete list; adding one is a deliberate D39
surface change that tests pin):

==========================  ===========================  ===============
Facade operation            Metric                       Operation
==========================  ===========================  ===============
``slice_duration(run)``     ``agentcompute_slice_duration_seconds``  time_block
``run_claim(kind)``         ``agentcompute_run_claim_total``          increment
``agent_spawn(kind)``      ``agentcompute_agent_spawn_total``        increment
``llm_call(outcome)``      ``agentcompute_llm_call_total``           increment
``token_usage(...)``       ``agentcompute_llm_tokens_total``         observe
``shard_persisted(...)``   ``agentcompute_shard_persisted_total``   increment
``agent_terminal(status)`` ``agentcompute_agent_terminal_total``     increment
``stuck_transition()``     ``agentcompute_stuck_transition_total``   increment
``queue_depth(depth)``     ``agentcompute_queue_depth``              set_gauge
==========================  ===========================  ===============

Callers never wrap these operations defensively: the Protocol's contract
is that a metrics impl never raises (diagnostic-first, like the tracer),
so a broken impl is a bug in the impl, and domain exceptions propagate
unwrapped either way. Construction sites without a configured plugin
default to :data:`NULL_OBSERVABILITY` — a singleton facade whose metrics
backend is inert — so ``observability: AgentComputeObservability = NULL_OBSERVABILITY``
parameters stay non-optional in type while costing nothing.
"""
from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from typing import Literal

from agentclaw.community.core.agentcompute.domain.models import (
    AgentKind, AgentStatus, LlmCallKind, Run, TenantId, TokenUsage,
)
from agentclaw.community.plugin_api.metrics import MetricLabels, MetricsPlugin

#: Closed outcome vocabulary for one LLM/gateway attempt (D39: "LLM error
#: rate" counted by class, not by message).
LlmCallOutcome = Literal["ok", "transient", "non_retryable", "exhausted", "timeout"]

#: Claim-kind vocabulary: a job enters the worker either freshly claimed
#: or reclaimed after a lease expiry (D28 fencing terms).
ClaimKind = Literal["claim", "reclaim"]

#: Tenant label for token observations made where the billing tenant is not
#: on the call path — the shared LLM client is constructed once per process
#: and ``CompletionRequest`` carries no tenant (D37 admission state does).
#: Threading the run's tenant down to the client is deferred; the string is
#: deliberately distinct from every real tenant id.
UNATTRIBUTED_TENANT = TenantId("unattributed")


class AgentComputeObservability:
    """Named D39 operations over an injected ``MetricsPlugin``."""

    def __init__(self, metrics: MetricsPlugin) -> None:
        self._metrics = metrics

    def slice_duration(self, run: Run) -> AbstractContextManager[None]:
        """One bounded slice's wall-clock duration (D34 slices, seconds)."""
        return self._metrics.time_block(
            "agentcompute_slice_duration_seconds",
            labels={"tenant": run.tenant},
        )

    def run_claim(self, kind: ClaimKind) -> None:
        """A job was claimed or reclaimed off the queue (D28)."""
        self._metrics.increment("agentcompute_run_claim_total", labels={"kind": kind})

    def agent_spawn(self, kind: AgentKind) -> None:
        """One subagent was spawned into the run."""
        self._metrics.increment(
            "agentcompute_agent_spawn_total", labels={"kind": kind.value},
        )

    def llm_call(self, outcome: LlmCallOutcome) -> None:
        """One LLM/gateway attempt resolved as ``outcome``."""
        self._metrics.increment(
            "agentcompute_llm_call_total", labels={"outcome": outcome},
        )

    def token_usage(self, tenant: TenantId, kind: LlmCallKind, usage: TokenUsage) -> None:
        """Total tokens of one completed exchange, attributed to ``tenant``."""
        self._metrics.observe(
            "agentcompute_llm_tokens_total",
            float(usage.total_tokens),
            labels={"tenant": tenant, "kind": kind.value},
        )

    def shard_persisted(self, artifact: bool) -> None:
        """A result shard was durably routed (D31; payload XOR artifact)."""
        self._metrics.increment(
            "agentcompute_shard_persisted_total",
            labels={"artifact": str(artifact).lower()},
        )

    def agent_terminal(self, status: AgentStatus) -> None:
        """One subagent reached a terminal status."""
        self._metrics.increment(
            "agentcompute_agent_terminal_total", labels={"status": status.value},
        )

    def stuck_transition(self) -> None:
        """The D40 reclaim breaker moved a poison run to terminal STUCK."""
        self._metrics.increment("agentcompute_stuck_transition_total")

    def queue_depth(self, depth: int) -> None:
        """A point-in-time sample of pending/eligible jobs."""
        self._metrics.set_gauge("agentcompute_queue_depth", float(depth))


class _NullMetrics(MetricsPlugin):
    """Inert ``MetricsPlugin`` backing :data:`NULL_OBSERVABILITY`.

    Kept private to this module rather than imported from
    ``plugins.local.metrics``: core must not import concrete plugins, and
    this null is a facade-internal default, not a deployable impl.
    """

    def increment(self, name: str, labels: MetricLabels | None = None) -> None:
        return None

    def observe(self, name: str, value: float, labels: MetricLabels | None = None) -> None:
        return None

    def set_gauge(self, name: str, value: float, labels: MetricLabels | None = None) -> None:
        return None

    def time_block(
        self, name: str, labels: MetricLabels | None = None
    ) -> AbstractContextManager[None]:
        return nullcontext()


#: The no-metrics-configured facade: valid to call everywhere, records
#: nothing. Default parameter value for hook sites whose owners (tests,
#: direct construction) have no plugin configured.
NULL_OBSERVABILITY = AgentComputeObservability(_NullMetrics())