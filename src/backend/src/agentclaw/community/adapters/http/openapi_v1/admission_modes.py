"""Admission modes shared by the public OpenAPI route inventory and edge seams."""

from __future__ import annotations

from enum import StrEnum


class AdmissionMode(StrEnum):
    """How an operation admits an application caller with no human on the wire."""

    GRANT_CHECKED_OWN_BOT = "grant-checked"
    GRANT_CHECKED_ADDRESSED_BOT = "grant-checked-owner-addressed"
    GRANT_FILTERED = "grant-filtered"
    USER_GATED = "user-gated"
    OPEN = "open"
    REFUSED = "refused"
