"""Access control data models (records / value objects)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class AccessControlPolicyRecord:
    id: int
    entity_id: str
    entity_type: str
    policy: str | None
    gmt_create: str | None = None


@dataclass(slots=True)
class ConfigItemRecord:
    config_key: str
    config_value: str


@dataclass(slots=True)
class UserInfoRecord:
    id: int
    user_id: str
    user_type: str  # NORMAL, COMPETE
    status: str  # ACCESS, REFUSE
    gmt_create: str | None = None
    gmt_modified: str | None = None
