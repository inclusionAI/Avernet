"""Generic G3 acquisition: G1 gate + independent telemetry persistence."""

from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.metrics import (
    MetricDefinition,
    MetricSample,
    Outcome,
    metric_storage_key,
)
from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.service_api import (
    TelemetryAccessProtocol,
    TelemetryRepositoryProtocol,
)
from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.telemetry import (
    Observation,
    TelemetryContext,
    _text,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.common import (
    IdentityRef,
    ObjectRef,
)


def _identity(actor: ActorContext) -> IdentityRef:
    return IdentityRef(
        "AGENT" if actor.actor.principal_type == "bot" else "PRINCIPAL",
        actor.actor.principal_id,
        "avernet",
    )


def _metric_ref(ref: ObjectRef) -> None:
    ObjectRef.from_dict(ref.to_dict())
    if (
        ref.object_type != "MetricDefinition"
        or ref.issuer != "avernet"
        or ref.version is None
    ):
        raise ContextCollabValidationError(
            "metric_ref requires a versioned local MetricDefinition"
        )


class TelemetryService:
    def __init__(
        self, repository: TelemetryRepositoryProtocol, access: TelemetryAccessProtocol
    ) -> None:
        self._repository = repository
        self._access = access

    def record_observation(
        self, observation: Observation, *, actor_context: ActorContext
    ) -> Observation:
        observation = Observation.from_dict(observation.to_dict())
        if observation.observer != _identity(actor_context):
            raise ContextCollabValidationError(
                "observer must match the authenticated local actor"
            )
        self._access.check_record(
            observation.context, observation.subject_ref, actor_context
        )
        recorded = replace(observation, recorded_at=datetime.now(timezone.utc))
        self._append(
            "Observation",
            recorded.observation_id,
            recorded,
            recorded.context,
            actor_context,
        )
        return recorded

    def define_metric(
        self, definition: MetricDefinition, *, version: str, actor_context: ActorContext
    ) -> ObjectRef:
        definition = MetricDefinition.from_dict(definition.to_dict())
        _text(version, "metric definition version")
        if definition.owner != _identity(actor_context):
            raise ContextCollabValidationError(
                "metric owner must match the authenticated local actor"
            )
        ref = ObjectRef("MetricDefinition", definition.metric_id, version, "avernet")
        existing = self._repository.get_metric(ref)
        if existing is not None:
            if existing != definition:
                raise ContextCollabValidationError(
                    "metric definition version is immutable"
                )
            return ref
        # This separate version is identity, NOT the representation's schema_version.
        self._repository.append(
            "MetricDefinition",
            metric_storage_key(ref),
            {"reference": ref.to_dict(), "definition": definition.to_dict()},
            context=None,
            subject_ref=None,
            actor=_identity(actor_context),
        )
        return ref

    def _check_sample(self, sample: MetricSample) -> None:
        _metric_ref(sample.metric_ref)
        definition = self._repository.get_metric(sample.metric_ref)
        if definition is None:
            raise ContextCollabValidationError("unknown metric definition version")
        if definition.scope != sample.subject_ref.object_type:
            raise ContextCollabValidationError(
                "metric definition scope does not match sample subject"
            )

    def record_metric_sample(
        self,
        sample: MetricSample,
        *,
        context: TelemetryContext,
        actor_context: ActorContext,
    ) -> MetricSample:
        sample = MetricSample.from_dict(sample.to_dict())
        context = TelemetryContext.from_dict(context.to_dict())
        self._access.check_record(context, sample.subject_ref, actor_context)
        self._check_sample(sample)
        self._append("MetricSample", str(uuid4()), sample, context, actor_context)
        return sample

    def record_outcome(
        self,
        outcome: Outcome,
        *,
        context: TelemetryContext,
        actor_context: ActorContext,
    ) -> Outcome:
        outcome = Outcome.from_dict(outcome.to_dict())
        context = TelemetryContext.from_dict(context.to_dict())
        self._access.check_record(context, outcome.subject_ref, actor_context)
        if outcome.evaluator_ref is not None:
            expected = ObjectRef(
                "Agent" if actor_context.actor.principal_type == "bot" else "Principal",
                actor_context.actor.principal_id,
                None,
                "avernet",
            )
            if outcome.evaluator_ref != expected:
                raise ContextCollabValidationError(
                    "local Outcome cannot impersonate another evaluator"
                )
        for sample in outcome.metric_samples:
            if sample.subject_ref != outcome.subject_ref:
                raise ContextCollabValidationError(
                    "Outcome metric sample must share its subject"
                )
            self._check_sample(sample)
        self._append("Outcome", outcome.outcome_id, outcome, context, actor_context)
        return outcome

    def _append(self, kind, record_id, value, context, actor):
        self._repository.append(
            kind,
            record_id,
            value.to_dict(),
            context=context,
            subject_ref=value.subject_ref,
            actor=_identity(actor),
        )

    def _query(self, kind, subject_ref, collet_ref, actor):
        self._access.check_query(collet_ref, subject_ref, actor)
        return self._repository.list_records(kind, collet_ref, subject_ref)

    def query_observations(
        self,
        subject_ref: ObjectRef,
        *,
        collet_ref: ObjectRef,
        actor_context: ActorContext,
    ) -> tuple[Observation, ...]:
        return tuple(
            Observation.from_dict(data)
            for data in self._query(
                "Observation",
                subject_ref,
                collet_ref,
                actor_context,
            )
        )

    def query_metric_samples(
        self,
        subject_ref: ObjectRef,
        *,
        collet_ref: ObjectRef,
        actor_context: ActorContext,
    ) -> tuple[MetricSample, ...]:
        return tuple(
            MetricSample.from_dict(data)
            for data in self._query(
                "MetricSample",
                subject_ref,
                collet_ref,
                actor_context,
            )
        )

    def query_outcomes(
        self,
        subject_ref: ObjectRef,
        *,
        collet_ref: ObjectRef,
        actor_context: ActorContext,
    ) -> tuple[Outcome, ...]:
        return tuple(
            Outcome.from_dict(data)
            for data in self._query(
                "Outcome",
                subject_ref,
                collet_ref,
                actor_context,
            )
        )
