"""Admission modes shared by the public OpenAPI route inventory and edge seams."""

from __future__ import annotations

from collections.abc import Callable, Set
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class AdmissionMode(StrEnum):
    """How an operation admits an application caller with no human on the wire.

    ENGINE takes per-operation engines and an authorized Bot dependency through
    ENGINE(...). It does not replace the operation's Owner or application checks.
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

    @property
    def mode(self) -> AdmissionMode:
        return self

    def __call__(
        self, *, engines: Set[str], bot_dependency: Callable[[], Callable[..., Any]],
    ) -> _EngineAdmission:
        if self is not AdmissionMode.ENGINE:
            raise ValueError('Engine parameters require AdmissionMode.ENGINE')
        allowed_engines = frozenset(engines)
        if not allowed_engines:
            raise ValueError('AdmissionMode.ENGINE requires a non-empty engine allowlist')
        if not callable(bot_dependency):
            raise ValueError('AdmissionMode.ENGINE requires an authorized Bot dependency')
        return _EngineAdmission(engines=allowed_engines, bot_dependency=bot_dependency)


@dataclass(frozen=True)
class _EngineAdmission:
    """Immutable route parameters; never mutate the shared ENGINE enum member."""

    engines: frozenset[str]
    bot_dependency: Callable[[], Callable[..., Any]]

    @property
    def mode(self) -> AdmissionMode:
        return AdmissionMode.ENGINE
