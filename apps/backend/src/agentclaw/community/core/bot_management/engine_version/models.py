"""Transport-independent release version result and deployment configuration."""

from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict


@dataclass(frozen=True)
class EngineVersionConfig:
    target_release: str


class Detection(TypedDict):
    status: Literal["known", "legacy", "read_failed", "not_applicable"]
    reason: NotRequired[str]


class CurrentVersion(TypedDict, total=False):
    release_id: str
    raw_version: str
    source: Literal["engine_env"]


class ReleaseTarget(TypedDict):
    status: Literal["available", "unavailable"]
    release_id: NotRequired[str]
    source: NotRequired[Literal["backend_image_env"]]
    reason: NotRequired[str]


class VersionContext(TypedDict):
    bot_id: str
    stage: str
    checked_at: str
    instance_id: NotRequired[str]


Comparison = Literal["older", "same", "newer", "legacy", "unknown"]


class EngineVersionResult(TypedDict):
    detection: Detection
    current: NotRequired[CurrentVersion]
    release_target: ReleaseTarget
    comparison: Comparison
    restart_hint: Literal["recommended", "restricted", "none", "unknown"]
    restart_reason: NotRequired[str]
    context: VersionContext
