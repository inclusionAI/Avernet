"""Resolved Installation read mode; deployment parsing belongs to DI."""

from dataclasses import dataclass


@dataclass(frozen=True)
class InstallationReadConfig:
    """False retains full repair until this environment's backfill is accepted."""

    default_sync_only: bool = False
