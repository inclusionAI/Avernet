"""Admission modes shared by the public OpenAPI route inventory and edge seams."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class AdmissionMode(StrEnum):
    """How an operation admits an application caller with no human on the wire.

    ENGINE requires an AdmissionRule declaring its allowed engines and authorized
    Bot dependency. It does not replace the operation's Owner or application checks.
    """

    GRANT_CHECKED_OWN_BOT = "grant-checked"
    GRANT_CHECKED_ADDRESSED_BOT = "grant-checked-owner-addressed"
    GRANT_FILTERED = "grant-filtered"
    USER_GATED = "user-gated"
    #: Acts for the named user and addresses **no bot** — a creation, above
    #: all. Admitted on a live user-level delegation from that user, checked
    #: by ``require_delegated_user`` before the handler runs; a bot grant
    #: cannot serve here because there is no bot for it to name yet.
    USER_DELEGATED = "user-delegated"
    ENGINE = "engine"
    OPEN = "open"
    REFUSED = "refused"


@dataclass(frozen=True)
class AdmissionRule:
    """A mode and its parameters, declared together in the admission inventory.

    ENGINE requires engines and a provider resolving the authorized Bot dependency
    at route construction, after the inventory and its HTTP consumers are loaded.
    Other modes do not take engine parameters.
    """

    mode: AdmissionMode
    engines: frozenset[str] = frozenset()
    bot_dependency: Callable[[], Callable[..., Any]] | None = None
