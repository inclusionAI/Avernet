"""Project-owned model types isomorphic to Arca SDK model types.

These types replace direct imports from arca SDK in the API and core layers.
They are pure data models with no SDK dependencies, following the same
pattern as api/device_manage/_outbound_rule.py.

ArcaPaasService converts between these project-owned types and SDK types
at the plugin boundary (in _arca_paas_service.py).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MountPermission(StrEnum):
    """Mount permission for Arca sandbox mount points."""

    READ_ONLY = "ro"
    READ_WRITE = "rw"


class MountPoint(BaseModel):
    """Sandbox mount point."""

    model_config = ConfigDict(extra="allow")

    id: str
    remote_dir: str
    local_dir: str
    permission: MountPermission = MountPermission.READ_WRITE


class ResourceSpecification(BaseModel):
    """Sandbox resource specification."""

    model_config = ConfigDict(extra="allow")

    cpu: int
    memory: int
    disk: float | None = None


class Storage(BaseModel):
    """Sandbox storage."""

    model_config = ConfigDict(extra="allow")

    type: str | None = None
    path: str | None = None
    storage_id: str | None = None
    quota: str | None = None
    permission: str | None = None


class OutBoundOperationRuleUpdatedMode(StrEnum):
    """Sandbox outbound operation rule update mode."""

    REPLACE = "replace"
    APPEND = "append"


class VolumeMountSpec(BaseModel):
    """One UPFS subpath mount; converted to SDK types only at the plugin boundary."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    volume_id: str = Field(min_length=1, alias="volumeId")
    subpath: str = Field(min_length=1)
    subpath_size_bytes: int = Field(default=1073741824, gt=0, strict=True)
    mount_path: str = Field(min_length=1, alias="mountPath")
    read_only: bool = Field(default=False, alias="readOnly")

    @field_validator("subpath")
    @classmethod
    def validate_subpath(cls, value: str) -> str:
        # Never normalize or trim: that would silently mount a different path.
        if (
            not value.strip()
            or value.startswith("/")
            or value.endswith("/")
            or "\\" in value
            or "\x00" in value
            or "{" in value
            or "}" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
        ):
            raise ValueError(
                "UPFS subpath must be a non-empty relative path without traversal"
            )
        return value

    @field_validator("volume_id")
    @classmethod
    def validate_volume(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError(
                "UPFS volume_id must be non-empty without surrounding whitespace"
            )
        return value

    @field_validator("mount_path")
    @classmethod
    def validate_mount_path(cls, value: str) -> str:
        if not value.startswith("/") or "\x00" in value:
            raise ValueError("UPFS mount path must be absolute")
        return value
