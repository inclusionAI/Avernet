"""G3 MetricDefinition, MetricSample and Outcome protocol values.

A definition describes a metric, a sample carries its measured value, and an
Outcome records a result. None of them is an L4 completion/acceptance command.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.telemetry import (
    _text,
    _timestamp,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.common import (
    IdentityRef,
    ObjectRef,
)


def _ref(ref: ObjectRef) -> None:
    if not isinstance(ref, ObjectRef):
        raise ContextCollabValidationError("expected ObjectRef")
    ObjectRef.from_dict(ref.to_dict())


def _refs(refs: tuple[ObjectRef, ...]) -> None:
    if not isinstance(refs, tuple):
        raise ContextCollabValidationError("reference collection must be a tuple")
    for ref in refs:
        _ref(ref)


def _strings(values: dict[str, str]) -> None:
    if not isinstance(values, dict):
        raise ContextCollabValidationError("expected a string map")
    for key, value in values.items():
        _text(key, "map key")
        _text(value, "map value")


@dataclass(frozen=True)
class MetricDefinition:
    metric_id: str
    name: str
    direction: str
    unit: str
    aggregation: str
    scope: str
    owner: IdentityRef
    schema_version: str

    def __post_init__(self) -> None:
        for name in (
            "metric_id",
            "name",
            "unit",
            "aggregation",
            "scope",
            "schema_version",
        ):
            _text(getattr(self, name), name)
        if not isinstance(self.direction, str) or self.direction not in {
            "HIGHER_IS_BETTER",
            "LOWER_IS_BETTER",
            "TARGET_RANGE",
            "BOOLEAN_GATE",
        }:
            raise ContextCollabValidationError("unknown metric direction")
        if not isinstance(self.owner, IdentityRef):
            raise ContextCollabValidationError("metric owner must be IdentityRef")
        IdentityRef.from_dict(self.owner.to_dict())

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> MetricDefinition:
        try:
            return cls(**{**data, "owner": IdentityRef.from_dict(data["owner"])})
        except (KeyError, TypeError, ValueError) as error:
            raise ContextCollabValidationError(
                f"invalid MetricDefinition: {error}"
            ) from error


@dataclass(frozen=True)
class MetricSample:
    metric_ref: ObjectRef
    subject_ref: ObjectRef
    value: int | float | bool | str
    measured_at: datetime
    window: str | None
    evidence_refs: tuple[ObjectRef, ...]
    dimensions: dict[str, str]

    def __post_init__(self) -> None:
        _ref(self.metric_ref)
        _ref(self.subject_ref)
        if type(self.value) not in (int, float, bool, str) or (
            type(self.value) in (int, float) and not math.isfinite(self.value)
        ):
            raise ContextCollabValidationError(
                "metric value must be a finite number, boolean or string"
            )
        _timestamp(self.measured_at, "measured_at")
        if self.window is not None:
            _text(self.window, "window")
        _refs(self.evidence_refs)
        _strings(self.dimensions)

    def to_dict(self) -> dict:
        return {
            "metric_ref": self.metric_ref.to_dict(),
            "subject_ref": self.subject_ref.to_dict(),
            "value": self.value,
            "measured_at": self.measured_at.astimezone(timezone.utc).isoformat(),
            "window": self.window,
            "evidence_refs": [ref.to_dict() for ref in self.evidence_refs],
            "dimensions": dict(self.dimensions),
        }

    @classmethod
    def from_dict(cls, data: dict) -> MetricSample:
        try:
            return cls(
                **{
                    **data,
                    "metric_ref": ObjectRef.from_dict(data["metric_ref"]),
                    "subject_ref": ObjectRef.from_dict(data["subject_ref"]),
                    "measured_at": datetime.fromisoformat(data["measured_at"]),
                    "evidence_refs": tuple(
                        ObjectRef.from_dict(ref) for ref in data["evidence_refs"]
                    ),
                }
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ContextCollabValidationError(
                f"invalid MetricSample: {error}"
            ) from error


@dataclass(frozen=True)
class Outcome:
    outcome_id: str
    subject_ref: ObjectRef
    improvement_objective_refs: tuple[ObjectRef, ...]
    status: str
    produced_at: datetime
    result_refs: tuple[ObjectRef, ...]
    evidence_refs: tuple[ObjectRef, ...]
    metric_samples: tuple[MetricSample, ...]
    evaluator_ref: ObjectRef | None
    protocol_versions: dict[str, str]

    def __post_init__(self) -> None:
        _text(self.outcome_id, "outcome_id")
        if len(self.outcome_id) > 128:
            raise ContextCollabValidationError("outcome_id exceeds 128 characters")
        _ref(self.subject_ref)
        if not isinstance(self.status, str) or self.status not in {
            "SUCCEEDED",
            "PARTIALLY_SUCCEEDED",
            "FAILED",
            "CANCELLED",
            "INCONCLUSIVE",
        }:
            raise ContextCollabValidationError("unknown outcome status")
        _timestamp(self.produced_at, "produced_at")
        for refs in (
            self.improvement_objective_refs,
            self.result_refs,
            self.evidence_refs,
        ):
            _refs(refs)
        if not isinstance(self.metric_samples, tuple) or not all(
            isinstance(sample, MetricSample) for sample in self.metric_samples
        ):
            raise ContextCollabValidationError(
                "metric_samples must be a tuple of MetricSample"
            )
        if self.evaluator_ref is not None:
            _ref(self.evaluator_ref)
        _strings(self.protocol_versions)

    def to_dict(self) -> dict:
        return {
            "outcome_id": self.outcome_id,
            "subject_ref": self.subject_ref.to_dict(),
            "improvement_objective_refs": [
                ref.to_dict() for ref in self.improvement_objective_refs
            ],
            "status": self.status,
            "produced_at": self.produced_at.astimezone(timezone.utc).isoformat(),
            "result_refs": [ref.to_dict() for ref in self.result_refs],
            "evidence_refs": [ref.to_dict() for ref in self.evidence_refs],
            "metric_samples": [sample.to_dict() for sample in self.metric_samples],
            "evaluator_ref": self.evaluator_ref.to_dict()
            if self.evaluator_ref
            else None,
            "protocol_versions": dict(self.protocol_versions),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Outcome:
        try:
            values = dict(data)
            values["subject_ref"] = ObjectRef.from_dict(values["subject_ref"])
            values["produced_at"] = datetime.fromisoformat(values["produced_at"])
            for name in ("result_refs", "evidence_refs", "improvement_objective_refs"):
                values[name] = tuple(ObjectRef.from_dict(ref) for ref in values[name])
            values["metric_samples"] = tuple(
                MetricSample.from_dict(s) for s in values["metric_samples"]
            )
            if values.get("evaluator_ref") is not None:
                values["evaluator_ref"] = ObjectRef.from_dict(values["evaluator_ref"])
            return cls(**values)
        except (KeyError, TypeError, ValueError) as error:
            raise ContextCollabValidationError(f"invalid Outcome: {error}") from error


def metric_storage_key(ref: ObjectRef) -> str:
    """Stable bounded identity for an immutable (metric_id, version) pair."""
    import hashlib
    import json

    return hashlib.sha256(json.dumps([ref.object_id, ref.version]).encode()).hexdigest()
