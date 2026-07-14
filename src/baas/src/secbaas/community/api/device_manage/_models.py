"""Project-owned model types isomorphic to Arca SDK model types.

These types replace direct imports from arca SDK in the API and core layers.
They are pure data models with no SDK dependencies, following the same
pattern as api/device_manage/_outbound_rule.py.

ArcaPaasService converts between these project-owned types and SDK types
at the plugin boundary (in _arca_paas_service.py).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


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

    type: str
    path: str
    storage_id: str | None = None
    quota: str
    permission: str | None = None


class OutBoundOperationRuleUpdatedMode(StrEnum):
    """Sandbox outbound operation rule update mode."""

    REPLACE = "replace"
    APPEND = "append"
