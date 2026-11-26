"""G3 generic observation values; execution details are schema-owned extensions.

These are telemetry, never L4 Events or authoritative transition commands.
Optional references mean absent/not applicable, not fabricated execution facts.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from typing import Any

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.common import (
    IdentityRef,
    ObjectRef,
)


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(f"{name} must be a non-empty string")


def _timestamp(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ContextCollabValidationError(f"{name} must be a timezone-aware timestamp")


def _json_object(value: dict, name: str) -> None:
    def check(item):
        if isinstance(item, dict):
            return all(isinstance(k, str) and check(v) for k, v in item.items())
        if isinstance(item, list):
            return all(check(v) for v in item)
        return item is None or type(item) in (str, bool, int, float)

    try:
        if not isinstance(value, dict) or not check(value):
            raise ValueError("expected a JSON object")
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as error:
        raise ContextCollabValidationError(f"invalid {name}: {error}") from error


_CONTEXT_REFS = (
    "principal_ref",
    "agent_ref",
    "space_ref",
    "collet_ref",
    "task_ref",
    "activity_ref",
    "method_binding_ref",
    "product_profile_ref",
)


@dataclass(frozen=True)
class TelemetryContext:
    trace_id: str
    span_id: str
    protocol_versions: dict[str, str]
    correlation_id: str | None = None
    causation_id: str | None = None
    principal_ref: ObjectRef | None = None
    agent_ref: ObjectRef | None = None
    space_ref: ObjectRef | None = None
    collet_ref: ObjectRef | None = None
    task_ref: ObjectRef | None = None
    activity_ref: ObjectRef | None = None
    method_binding_ref: ObjectRef | None = None
    product_profile_ref: ObjectRef | None = None

    def __post_init__(self) -> None:
        for name in ("trace_id", "span_id", "correlation_id", "causation_id"):
            value = getattr(self, name)
            if name in ("trace_id", "span_id") or value is not None:
                _text(value, name)
        if not isinstance(self.protocol_versions, dict):
            raise ContextCollabValidationError("protocol_versions must be an object")
        for key, value in self.protocol_versions.items():
            _text(key, "protocol_versions key")
            _text(value, "protocol_versions value")
        for name in _CONTEXT_REFS:
            ref = getattr(self, name)
            if ref is not None:
                if not isinstance(ref, ObjectRef):
                    raise ContextCollabValidationError(f"{name} must be an ObjectRef")
                ObjectRef.from_dict(ref.to_dict())

    def to_dict(self) -> dict:
        return {
            **{
                name: getattr(self, name)
                for name in ("trace_id", "span_id", "correlation_id", "causation_id")
            },
            "protocol_versions": dict(self.protocol_versions),
            **{
                name: getattr(self, name).to_dict() if getattr(self, name) else None
                for name in _CONTEXT_REFS
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> TelemetryContext:
        try:
            values = dict(data)
            for name in _CONTEXT_REFS:
                if values.get(name) is not None:
                    values[name] = ObjectRef.from_dict(values[name])
            return cls(**values)
        except (TypeError, ValueError) as error:
            raise ContextCollabValidationError(
                f"invalid TelemetryContext: {error}"
            ) from error


@dataclass(frozen=True)
class Observation:
    observation_id: str
    observation_type: str
    observed_at: datetime
    recorded_at: datetime
    observer: IdentityRef
    subject_ref: ObjectRef
    context: TelemetryContext
    schema_version: str
    payload_ref: ObjectRef | None = None
    payload_digest: str | None = None
    classification: str | None = None
    confidence: float | None = None
    extensions: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _text(self.observation_id, "observation_id")
        if len(self.observation_id) > 128:
            raise ContextCollabValidationError("observation_id exceeds 128 characters")
        if not isinstance(self.observation_type, str) or self.observation_type not in {
            "EVENT",
            "LOG",
            "TRACE",
            "METRIC",
            "FEEDBACK",
            "DIAGNOSIS",
            "CUSTOM",
        }:
            raise ContextCollabValidationError("unknown observation_type")
        _timestamp(self.observed_at, "observed_at")
        _timestamp(self.recorded_at, "recorded_at")
        _text(self.schema_version, "schema_version")
        for name in ("payload_digest", "classification"):
            if getattr(self, name) is not None:
                _text(getattr(self, name), name)
        if not isinstance(self.observer, IdentityRef):
            raise ContextCollabValidationError("observer must be an IdentityRef")
        IdentityRef.from_dict(self.observer.to_dict())
        for name in ("subject_ref", "payload_ref"):
            ref = getattr(self, name)
            if name == "subject_ref" or ref is not None:
                if not isinstance(ref, ObjectRef):
                    raise ContextCollabValidationError(f"{name} must be an ObjectRef")
                ObjectRef.from_dict(ref.to_dict())
        if not isinstance(self.context, TelemetryContext):
            raise ContextCollabValidationError("context must be a TelemetryContext")
        if self.confidence is not None and (
            type(self.confidence) not in (int, float)
            or not math.isfinite(self.confidence)
            or not 0 <= self.confidence <= 1
        ):
            raise ContextCollabValidationError(
                "confidence must be a finite number in [0, 1]"
            )
        _json_object(self.extensions, "extensions")

    def to_dict(self) -> dict:
        data = {item.name: getattr(self, item.name) for item in fields(self)}
        for name in ("observed_at", "recorded_at"):
            data[name] = data[name].astimezone(timezone.utc).isoformat()
        for name in ("observer", "subject_ref", "context", "payload_ref"):
            data[name] = data[name].to_dict() if data[name] is not None else None
        data["extensions"] = json.loads(json.dumps(self.extensions, allow_nan=False))
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Observation:
        try:
            values = dict(data)
            for name in ("observed_at", "recorded_at"):
                values[name] = datetime.fromisoformat(values[name])
            values["observer"] = IdentityRef.from_dict(values["observer"])
            values["subject_ref"] = ObjectRef.from_dict(values["subject_ref"])
            values["context"] = TelemetryContext.from_dict(values["context"])
            if values.get("payload_ref") is not None:
                values["payload_ref"] = ObjectRef.from_dict(values["payload_ref"])
            return cls(**values)
        except (KeyError, TypeError, ValueError) as error:
            raise ContextCollabValidationError(
                f"invalid Observation: {error}"
            ) from error
