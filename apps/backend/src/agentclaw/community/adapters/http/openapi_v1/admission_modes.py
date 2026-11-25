"""Admission modes shared by the public OpenAPI route inventory and edge seams."""

from __future__ import annotations

from enum import StrEnum


class AdmissionMode(StrEnum):
    """How an operation admits an application caller with no human on the wire.

    Engine-scoped members carry their shared allowlist. They do not replace the
    operation's Owner or application checks.
    """

    engines: frozenset[str]

    def __new__(cls, value: str, engines: tuple[str, ...] = ()) -> AdmissionMode:
        member = str.__new__(cls, value)
        member._value_ = value
        member.engines = frozenset(engines)
        return member

    GRANT_CHECKED_OWN_BOT = "grant-checked"
    GRANT_CHECKED_ADDRESSED_BOT = "grant-checked-owner-addressed"
    GRANT_FILTERED = "grant-filtered"
    USER_GATED = "user-gated"
    #: Acts for the named user and addresses **no bot** — a creation, above
    #: all. Admitted on a live user-level delegation from that user, checked
    #: by ``require_delegated_user`` before the handler runs; a bot grant
    #: cannot serve here because there is no bot for it to name yet.
    USER_DELEGATED = "user-delegated"
    ENGINE_CODING = ("engine-coding", ("aicoding", "claude_code"))
    OPEN = "open"
    REFUSED = "refused"
