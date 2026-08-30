"""Service API for running the Installation flush deliberately — the backfill.

The Protocol lives here, in the owning core module, so the concrete service can
inherit it without a ``core -> api`` waiver; adapters import it from
``api/installation_backfill_service.py``.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class BotBackfillOutcome:
    """What the backfill did to one Bot.

    ``changed`` is the flush's own answer: ``True`` only when it wrote rows.
    ``error`` carries the failure for a Bot a page-scoped run could not
    converge — the run continues past it rather than abandoning the rest of
    the page, and the report counts it as failed.
    """

    bot_id: str
    owner_id: str
    changed: bool
    error: str | None = None


@dataclass(frozen=True)
class BackfillReport:
    """One page of a scoped backfill run.

    ``total`` is how many Bots the scope holds, not how many this page
    reached, so the caller knows whether more pages remain.
    """

    total: int
    page: int
    page_size: int
    scanned: int
    changed: int
    failed: int
    outcomes: tuple[BotBackfillOutcome, ...]

    @property
    def has_more(self) -> bool:
        return self.page * self.page_size < self.total


@runtime_checkable
class InstallationBackfillServiceProtocol(Protocol):
    """Converge Installation with SkillSet configuration, on demand.

    DB-side only, exactly like the flush it runs: no device is touched and no
    runtime projection is triggered. A Bot converged here still needs a
    projection before its engine sees the change.
    """

    @abstractmethod
    def backfill_bot(self, *, bot_id: str, owner_id: str) -> BotBackfillOutcome:
        """Flush one exact Bot; raise if it does not exist for this owner."""
        ...

    @abstractmethod
    def backfill_page(
        self,
        *,
        owner_id: str | None = None,
        engine_type: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> BackfillReport:
        """Flush one page of the Bots this env holds.

        ``owner_id`` and ``engine_type`` are optional filters; ``None`` means
        "do not filter by it", so an unfiltered call reaches every live Bot in
        the env one page at a time.

        Pages are cut from a newest-first ordering, so a Bot created while a
        sweep is in flight shifts the window and one Bot can fall between two
        pages. The flush is convergent and idempotent, so the fix is to run
        the sweep again rather than to hold a snapshot open across it.
        """
        ...
