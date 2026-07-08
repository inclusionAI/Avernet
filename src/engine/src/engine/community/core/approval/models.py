"""Approval-plugin request / result models.

Mirrors the OpenClaw `exec.approvals.get` / `exec.approvals.set` wire shape but
exposes typed dataclasses so callers (e.g. `api/approvals.py`) don't speak raw
dict/JSON to the engine. Engines that don't implement the OpenClaw shape can
still satisfy the plugin Protocol by translating these dataclasses to / from
their own upstream wire format.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# The three modes the OpenClaw / Moltis approval policy understands.
# `always` = ask on every action, `on-miss` = ask only when the policy can't
# auto-decide, `never` = full auto. Engines may declare their own equivalent
# values; this Literal is here to document the canonical vocabulary the
# frontend selector uses today (see ApprovalModeSelector.tsx).
ApprovalMode = Literal["always", "approve", "on-miss", "on_miss", "never", "off"]


@dataclass
class ApprovalModeGetRequest:
    """Inputs for `ApprovalService.get_mode`.

    `session_key` is optional — when omitted the engine should return the
    user-level / global default mode rather than a per-session override.
    """

    session_key: str | None = None


@dataclass
class ApprovalModeGetResult:
    """Result of `ApprovalService.get_mode`.

    `payload` carries the raw upstream payload so callers preserving the legacy
    response shape (the HTTP route does this) don't lose engine-specific fields
    (`globalDefault`, `isOverride`, etc.). New callers should prefer the typed
    `mode` field.
    """

    mode: ApprovalMode | str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ApprovalModeSetRequest:
    """Inputs for `ApprovalService.set_mode`."""

    session_key: str
    mode: ApprovalMode | str


@dataclass
class ApprovalModeSetResult:
    """Result of `ApprovalService.set_mode`.

    `ok` mirrors the upstream's `payload.ok` flag (defaults to True when the
    upstream returned a successful response without an explicit ok marker).
    `mode` echoes the value the engine actually committed (engines may
    canonicalise hyphen / underscore variants).
    """

    ok: bool = True
    mode: ApprovalMode | str | None = None
    session_key: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "ApprovalMode",
    "ApprovalModeGetRequest",
    "ApprovalModeGetResult",
    "ApprovalModeSetRequest",
    "ApprovalModeSetResult",
]
