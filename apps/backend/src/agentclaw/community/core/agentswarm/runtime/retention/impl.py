"""Artifact TTL sweeper for the agentswarm artifact store (task 12.9, D38).

Retention is store-scoped on purpose: the sweeper never reads the database,
so it inherits no tenancy rules — artifact keys already embed the run identity
(``{run_id}/{agent_id}/result``, the only format
``SubagentRuntime._mk_result`` writes today), and an agent result older than
the TTL can no longer be resumed by any slice (D34 rehydration reads results
while runs are live, so the TTL must cover the active-run horizon generously
relative to a run's deadline). Age comes from the store's own listing and mtime
seam (:class:`RetentionScanner`), deletion from the store's own guard-raised
``delete``; a key that escapes the root raises there and is recorded, never
deleted, never propagated (single-key failures are logged and the sweep
continues).

Sweeps are **synchronous and bounded**: an injected caller (a future wave's
worker/scheduler) invokes periodically per
``AgentSwarmRetentionConfig.sweep_interval_seconds``; the sweeper itself
keeps no timers, no asyncio, and ``max_batch`` caps one invocation's work.

DI wiring (wave-2, deliberately deferred): ``di/modules/agentswarm/``
(off-limits this wave) will eventually publish the sweeper built from
``AgentSwarmConfig.retention`` + ``artifact_store_root`` onto
``FilesystemArtifactStore.retention_scanner()`` and a worker/scheduler will
call ``sweep()`` at the configured interval until then.
"""
from __future__ import annotations

from agentclaw.community.log import get_logger
import re
import time
from dataclasses import dataclass
from typing import Callable

from agentclaw.community.core.agentswarm.config.settings import (
    AgentSwarmRetentionConfig,
)
from agentclaw.community.plugin_api.agentswarm.artifact import (
    ArtifactStore,
    RetentionScanner,
)

logger = get_logger("agentswarm")

_DAY_SECONDS = 86400.0

# The only conformant artifact-key format today: SubagentRuntime writes
# ``{run_id}/{agent_id}/result`` for spilled results. Anything else
# in the root is foreign state (temp/inflight files, operator residue) and
# must be skipped, not guessed at.
_KEY_FORMAT = re.compile(r"^[^/]+/[^/]+/result$")


@dataclass(frozen=True)
class SweepOutcome:
    """One sweep's ledger.

    ``would_delete`` carries every key the TTL matched; in a real run
    ``deleted`` and ``errors`` partition it, in a dry run ``deleted`` is
    empty (nothing was touched). ``skipped`` records non-conformant keys
    examined and misses (delete returned False); ``errors`` carries
    ``key: reason`` strings for per-key failures.
    """

    would_delete: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


class AgentSwarmRetentionSweeper:

    def __init__(
        self,
        store: ArtifactStore,
        scanner: RetentionScanner,
        config: AgentSwarmRetentionConfig,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._scanner = scanner
        self._config = config
        self._clock = clock

    def sweep(
        self, *, dry_run: bool = False, max_batch: int | None = None
    ) -> SweepOutcome:
        cutoff = self._clock() - self._config.artifact_ttl_days * _DAY_SECONDS
        outcome = _OutcomeBuilder(max_batch=max_batch)
        for key in self._scanner.list_keys():
            if not _KEY_FORMAT.match(key):
                outcome.skip(key)
                continue
            if outcome.at_capacity():
                break
            try:
                mtime = self._scanner.key_mtime(key)
            except (OSError, ValueError) as exc:
                outcome.error(key, exc)
                logger.warning(
                    "[agentswarm][sweep] key=%s mtime lookup failed: %s",
                    key, exc,
                )
                continue
            if mtime >= cutoff:
                continue
            outcome.would_delete(key)
            if dry_run:
                continue
            try:
                deleted = self._store.delete(key)
            except (OSError, ValueError) as exc:
                outcome.error(key, exc)
                logger.warning(
                    "[agentswarm][sweep] key=%s delete failed: %s", key, exc
                )
                continue
            if deleted:
                outcome.deleted(key)
            else:
                outcome.skip(key)
        result = outcome.build()
        logger.info(
            "[agentswarm][sweep] matched=%s deleted=%s skipped=%s errors=%s "
            "dry_run=%s",
            len(result.would_delete), len(result.deleted), len(result.skipped),
            len(result.errors), dry_run,
        )
        return result


class _OutcomeBuilder:
    """Mutable accumulator; ``max_batch`` caps how many TTL-matched keys one
    invocation processes, so a large root cannot block the caller's loop —
    leftovers wait for the next sweep. Skips and errors do not count toward
    the cap; matched keys do.
    """

    def __init__(self, *, max_batch: int | None) -> None:
        self._max_batch = max_batch
        self._would_delete: list[str] = []
        self._deleted: list[str] = []
        self._skipped: list[str] = []
        self._errors: list[str] = []

    def at_capacity(self) -> bool:
        return (
            self._max_batch is not None
            and len(self._would_delete) >= self._max_batch
        )

    def skip(self, key: str) -> None:
        self._skipped.append(key)

    def would_delete(self, key: str) -> None:
        self._would_delete.append(key)

    def deleted(self, key: str) -> None:
        self._deleted.append(key)

    def error(self, key: str, exc: Exception) -> None:
        self._errors.append(f"{key}: {exc}")

    def build(self) -> SweepOutcome:
        return SweepOutcome(
            would_delete=tuple(self._would_delete),
            deleted=tuple(self._deleted),
            skipped=tuple(self._skipped),
            errors=tuple(self._errors),
        )