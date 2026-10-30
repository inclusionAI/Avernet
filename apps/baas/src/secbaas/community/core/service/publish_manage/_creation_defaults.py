"""Publish creation policy, evaluated without an ORM session."""

from typing import Any


def resolve_publish_creation(
    *,
    publish_type: str,
    creator: str,
    name: str | None = None,
    publisher: str | None = None,
    replica_desired: int | None = None,
    batch_capacity: int | None = None,
    batch_number: int | None = None,
    cooldown_seconds: int | None = None,
) -> dict[str, Any]:
    # Explicit empty values and zero remain meaningful; only None gets a default.
    return {
        "name": name if name is not None else f"Publish-{publish_type}",
        "publisher": publisher if publisher is not None else creator,
        "replica_desired": replica_desired if replica_desired is not None else 1,
        "batch_capacity": batch_capacity if batch_capacity is not None else 5,
        "batch_number": batch_number if batch_number is not None else 1,
        "cooldown_seconds": cooldown_seconds if cooldown_seconds is not None else 0,
    }
