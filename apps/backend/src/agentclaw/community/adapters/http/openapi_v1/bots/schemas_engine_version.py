"""Public read-only release observation and restart advice."""

from typing import Literal
from pydantic import BaseModel, Field


class VersionDetection(BaseModel):
    """Whether the current runtime release was identified."""

    status: Literal["known", "legacy", "read_failed", "not_applicable"] = Field(
        description="Known identifies a release; legacy lacks release metadata; read_failed could not inspect it; not_applicable skips engines outside OpenClaw/Hermes."
    )
    reason: str | None = Field(
        default=None, description="Controlled detection reason, when present."
    )


class CurrentRelease(BaseModel):
    """Observed release identity or a historical unversioned marker."""

    release_id: str | None = Field(
        default=None, description="Complete release branch, when identified."
    )
    raw_version: str | None = Field(
        default=None, description="Historical marker that does not identify a release."
    )
    source: Literal["engine_env"] = Field(
        description="Release marker observed in the selected runtime."
    )


class PairedReleaseTarget(BaseModel):
    """The platform release paired with this version check."""

    status: Literal["available", "unavailable"] = Field(
        description="Available has a valid paired release; unavailable cannot establish one."
    )
    release_id: str | None = Field(
        default=None, description="Complete paired release branch, when available."
    )
    source: Literal["backend_image_env"] | None = Field(
        default=None,
        description="The paired release baked into the serving platform image.",
    )
    reason: str | None = Field(
        default=None,
        description="Controlled reason when the paired release is unavailable.",
    )


class ReleaseCheckContext(BaseModel):
    """The workspace and selected instance this observation describes."""

    bot_id: str = Field(description="The addressed Bot identifier.")
    stage: Literal["draft"] = Field(
        description="Only the workspace draft is inspected."
    )
    checked_at: str = Field(
        description="UTC timestamp of this observation in ISO 8601 form."
    )
    instance_id: str | None = Field(
        default=None, description="The selected runtime instance, when resolved."
    )


class EngineVersionObservation(BaseModel):
    """Release facts and advisory restart eligibility; grants no restart permission."""

    detection: VersionDetection = Field(
        description="Current release detection outcome."
    )
    current: CurrentRelease | None = Field(
        default=None, description="Current release facts; omitted when unidentified."
    )
    release_target: PairedReleaseTarget = Field(
        description="The release paired with this platform build."
    )
    comparison: Literal["older", "same", "newer", "legacy", "unknown"] = Field(
        description="Older/same/newer compares complete branch identities and strict REL calendar dates; legacy lacks identity; unknown cannot order them."
    )
    restart_hint: Literal["recommended", "restricted", "none", "unknown"] = Field(
        description="Recommended suggests a workspace restart for older/legacy releases regardless of provider or image policy; none needs no update or is not applicable; unknown cannot advise. Restricted is retained for compatibility but not emitted. Advisory only."
    )
    restart_reason: str | None = Field(
        default=None,
        description="Controlled restriction or uncertainty reason, when present.",
    )
    context: ReleaseCheckContext = Field(
        description="The exact workspace observation scope."
    )
