"""Collet description and audit facts, independent of Task authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)

COLLET_METADATA_KEYS = frozenset(
    {
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "title",
        "description",
        "objective",
        "acceptance_criteria",
    }
)
COLLET_AUDIT_KEYS = COLLET_METADATA_KEYS - {
    "title",
    "description",
    "objective",
    "acceptance_criteria",
}


def validate_collet_text(value: str | None, field: str) -> None:
    """Description fields may be absent, but not blank or non-text values."""
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ContextCollabValidationError(
            f"Collet.{field} must be a non-empty string or null, got: {value!r}"
        )


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ContextCollabValidationError(f"Collet.{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ContextCollabValidationError(
            f"Collet.{field} must be an ISO timestamp"
        ) from error
    return parsed


@dataclass(frozen=True)
class ColletMetadata:
    """Complete audit metadata for new Collets; never synthesize old history.

    Actors come from authenticated operations, not caller-supplied audit fields.
    objective/acceptance_criteria describe the overall collaboration intent;
    they do not replace Task objectives or completion conditions.
    """

    created_at: datetime
    created_by: PrincipalRef
    updated_at: datetime
    updated_by: PrincipalRef
    title: str | None = None
    description: str | None = None
    objective: str = ""
    acceptance_criteria: str = ""

    def __post_init__(self) -> None:
        for field in ("created_at", "updated_at"):
            value = getattr(self, field)
            if not isinstance(value, datetime) or value.utcoffset() is None:
                raise ContextCollabValidationError(
                    f"Collet.{field} must be a timezone-aware timestamp"
                )
            object.__setattr__(self, field, value.astimezone(timezone.utc))
        if self.updated_at < self.created_at:
            raise ContextCollabValidationError(
                "Collet.updated_at must not precede created_at"
            )
        for field in ("created_by", "updated_by"):
            actor = getattr(self, field)
            if not isinstance(actor, PrincipalRef):
                raise ContextCollabValidationError(
                    f"Collet.{field} requires a typed PrincipalRef"
                )
            PrincipalRef.from_dict(actor.to_dict())
        for field in ("title", "description"):
            validate_collet_text(getattr(self, field), field)
        for field in ("objective", "acceptance_criteria"):
            if not isinstance(getattr(self, field), str):
                raise ContextCollabValidationError(f"Collet.{field} must be a string")

    @classmethod
    def from_dict(cls, data: dict) -> ColletMetadata:
        if (
            not isinstance(data, dict)
            or set(data) - COLLET_METADATA_KEYS
            or not COLLET_AUDIT_KEYS <= set(data)
        ):
            raise ContextCollabValidationError("invalid or incomplete Collet metadata")
        for field in ("created_by", "updated_by"):
            if not isinstance(data[field], dict):
                raise ContextCollabValidationError(f"Collet.{field} must be an object")
        return cls(
            created_at=_timestamp(data["created_at"], "created_at"),
            created_by=PrincipalRef.from_dict(data["created_by"]),
            updated_at=_timestamp(data["updated_at"], "updated_at"),
            updated_by=PrincipalRef.from_dict(data["updated_by"]),
            title=data.get("title"),
            description=data.get("description"),
            objective=data.get("objective", ""),
            acceptance_criteria=data.get("acceptance_criteria", ""),
        )

    def to_dict(self) -> dict:
        return {
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by.to_dict(),
            "updated_at": self.updated_at.isoformat(),
            "updated_by": self.updated_by.to_dict(),
            "title": self.title,
            "description": self.description,
            "objective": self.objective,
            "acceptance_criteria": self.acceptance_criteria,
        }
