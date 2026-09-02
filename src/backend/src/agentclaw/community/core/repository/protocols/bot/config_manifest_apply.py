"""Repository contracts for the manifest apply record and its lock (W4, #1472).

Every member is ``@abstractmethod``: an implementation that omits one fails at
construction naming the missing member, instead of raising ``AttributeError`` at
the call site — which, for the apply record, would be mid-apply on a real bot.
Domain imports are ``TYPE_CHECKING``-only; see ``core/repository/README.md`` for
why that direction is load-bearing.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Optional, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.bot_config_manifest.repository.apply_models import (
        BotConfigManifestApplyLockRecord,
        BotConfigManifestApplyRecord,
    )


@runtime_checkable
class BotConfigManifestApplyRepositoryProtocol(Protocol):
    """The apply record: one row per apply, written twice.

    Keyed on ``(env, entity_id, bot_id)`` plus the apply's own ``apply_id``.
    Every read filters on the bot key as well as the id, so an ``apply_id``
    guessed or leaked from another bot resolves to ``None`` — the id is a handle
    the caller polls with, never the thing that authorizes the read.

    The two writes are deliberate and match apply's async shape: ``start``
    records ``RUNNING`` before the work begins, ``finish`` records the terminal
    status and the full report once it ends. Nothing writes a partial report in
    between, so a reader never sees a half-built account of what happened.
    """

    @abstractmethod
    def start(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        apply_id: str,
        trigger: str,
        actor: str,
        report: str,
    ) -> BotConfigManifestApplyRecord:
        """Insert the ``RUNNING`` row for an apply that is about to begin."""
        ...

    @abstractmethod
    def finish(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        apply_id: str,
        status: str,
        report: str,
    ) -> Optional[BotConfigManifestApplyRecord]:
        """Stamp the terminal status, the finished timestamp and the report.

        Returns ``None`` when the row is gone — which the caller treats as a
        lost race rather than an error, because the alternative is a background
        thread raising into nothing.
        """
        ...

    @abstractmethod
    def get(
        self, *, env: str, entity_id: str, bot_id: str, apply_id: str
    ) -> Optional[BotConfigManifestApplyRecord]:
        """One apply's row, or ``None``.

        Filters on the bot key **and** the id: an id belonging to another bot is
        not found here.
        """
        ...

    @abstractmethod
    def latest(
        self, *, env: str, entity_id: str, bot_id: str
    ) -> Optional[BotConfigManifestApplyRecord]:
        """The newest row for this bot, or ``None`` when it has never applied.

        ``None``, never an error — the same "absent is not an error" rule the
        manifest's own read follows.
        """
        ...

    @abstractmethod
    def recent(
        self, *, env: str, entity_id: str, bot_id: str, limit: int
    ) -> list[BotConfigManifestApplyRecord]:
        """The newest ``limit`` rows for this bot, newest first.

        Strict mode's baselines are read back through report *history*: the
        newest row may be an apply that failed to resolve a source (its
        report carries no resolution for it), and the record that did resolve
        the ref lives further back. ``limit`` bounds the walk, newest first.
        """
        ...


@runtime_checkable
class BotConfigManifestApplyLockRepositoryProtocol(Protocol):
    """Serialises applies against one bot.

    The UNIQUE constraint on ``(avernet_tenant, env, entity_id, bot_id)`` **is**
    the lock: ``acquire`` inserts a row and lets the database arbitrate, so
    exactly one concurrent caller wins and the rest see the integrity violation
    as "held". The shape is ``BotRestartLockRepository``'s, reused rather than
    reinvented (work-items §5); the table is separate, because applying a
    manifest and restarting a bot are different operations and sharing a row
    would make one block the other by accident.
    """

    @abstractmethod
    def acquire(
        self, *, env: str, entity_id: str, bot_id: str, holder_user_id: str
    ) -> Optional[BotConfigManifestApplyLockRecord]:
        """Take the lock. ``None`` means another apply holds it.

        Stamps a random ``lock_token`` on the row and returns the record
        carrying it. The caller keeps that token and passes it to
        :meth:`release`, so a delete only ever removes the row it acquired —
        never one a later caller took after this one's lock was reaped.
        """
        ...

    @abstractmethod
    def release(
        self, *, env: str, entity_id: str, bot_id: str, lock_token: str
    ) -> bool:
        """Release the lock, comparing the token first. Idempotent."""
        ...

    @abstractmethod
    def get(
        self, *, env: str, entity_id: str, bot_id: str
    ) -> Optional[BotConfigManifestApplyLockRecord]:
        """The lock row for this bot, if one is held."""
        ...

    @abstractmethod
    def get_if_stale(
        self, *, env: str, entity_id: str, bot_id: str, ttl_seconds: int
    ) -> Optional[BotConfigManifestApplyLockRecord]:
        """The lock row, but only once it is older than ``ttl_seconds``.

        Judged on the **database clock** on both sides — the row's
        ``gmt_create`` and "now" both come from the database — so application
        and database clock skew cannot mis-judge the TTL.

        This is what bounds a report stranded at ``RUNNING`` by a process that
        died mid-apply: such a report reads as ``FAILED`` once its lock has gone
        stale, derived at read time rather than by a sweeper nobody maintains.
        """
        ...
