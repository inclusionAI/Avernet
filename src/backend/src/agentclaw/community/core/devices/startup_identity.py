"""Resolve the persisted identity of one device startup attempt."""

from __future__ import annotations

from collections.abc import Mapping


STARTUP_IDENTITY_KEYS = (
    "startup_identity",
    "restart_publish_id",
    "restart_request_id",
    "publish_id",
    "sandbox_id",
)


def resolve_startup_identity(props: Mapping[str, object] | None) -> str | None:
    """Return the highest-priority non-empty persisted startup identity."""

    if props is None:
        return None
    return next(
        (
            str(props[key])
            for key in STARTUP_IDENTITY_KEYS
            if props.get(key) is not None and str(props[key])
        ),
        None,
    )


__all__ = ["resolve_startup_identity"]
