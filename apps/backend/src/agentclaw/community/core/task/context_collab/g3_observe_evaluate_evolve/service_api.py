"""Local G3 Service API and storage/access ports, independent of transport.

Local Task/Collet self-reports only. Records never advance Work or revisions.
Metric definitions are immutable and explicitly versioned separately from their
schema_version. Queries return acceptance order, without aggregation. Failures
propagate; producers handle non-mandatory telemetry failure without false success.
"""

from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.metrics import (
    MetricDefinition,
    MetricSample,
    Outcome,
)
from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.telemetry import (
    Observation,
    TelemetryContext,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.common import (
    IdentityRef,
    ObjectRef,
)


@runtime_checkable
class TelemetryServiceProtocol(Protocol):
    def record_observation(
        self, observation: Observation, *, actor_context: ActorContext
    ) -> Observation:
        """Authorize, stamp recorded_at, persist; no success-shaped fallback."""
        ...

    def define_metric(
        self, definition: MetricDefinition, *, version: str, actor_context: ActorContext
    ) -> ObjectRef:
        """Register immutable definition. Exact repeated registration is a no-op."""
        ...

    def record_metric_sample(
        self,
        sample: MetricSample,
        *,
        context: TelemetryContext,
        actor_context: ActorContext,
    ) -> MetricSample:
        """Require a known versioned definition with matching subject scope."""
        ...

    def record_outcome(
        self,
        outcome: Outcome,
        *,
        context: TelemetryContext,
        actor_context: ActorContext,
    ) -> Outcome:
        """Record a self-reported result, not an owner acceptance or Task transition."""
        ...

    def query_observations(
        self,
        subject_ref: ObjectRef,
        *,
        collet_ref: ObjectRef,
        actor_context: ActorContext,
    ) -> tuple[Observation, ...]: ...

    def query_metric_samples(
        self,
        subject_ref: ObjectRef,
        *,
        collet_ref: ObjectRef,
        actor_context: ActorContext,
    ) -> tuple[MetricSample, ...]: ...

    def query_outcomes(
        self,
        subject_ref: ObjectRef,
        *,
        collet_ref: ObjectRef,
        actor_context: ActorContext,
    ) -> tuple[Outcome, ...]: ...


class TelemetryRepositoryProtocol(Protocol):
    def append(
        self,
        kind: str,
        record_id: str,
        data: dict,
        *,
        context: TelemetryContext | None,
        subject_ref: ObjectRef | None,
        actor: IdentityRef,
    ) -> None: ...

    def get_metric(self, metric_ref: ObjectRef) -> MetricDefinition | None: ...

    def list_records(
        self, kind: str, collet_ref: ObjectRef, subject_ref: ObjectRef
    ) -> tuple[dict, ...]: ...


class TelemetryAccessProtocol(Protocol):
    def check_record(
        self, context: TelemetryContext, subject_ref: ObjectRef, actor: ActorContext
    ) -> None: ...

    def check_query(
        self, collet_ref: ObjectRef, subject_ref: ObjectRef, actor: ActorContext
    ) -> None: ...
