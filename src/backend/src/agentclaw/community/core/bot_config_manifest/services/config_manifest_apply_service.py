"""Applying a bot's configuration manifest (issue #1472).

Owns the lifecycle around the orchestrator: the lock, the re-validation, the
record's two writes, and the background thread the work actually runs on.

**Apply is started, not awaited.** :meth:`start_apply` answers as soon as it can
answer — with an ``apply_id`` — and the orchestrator runs on a daemon thread.
Applying is device I/O today and network fetching from W5, so a caller holding
an HTTP connection across it would be a caller timing out. It is also the shape
W13's ``APPLYING`` poll state needs: a state you can observe only exists if the
work is something you start and then ask about.
"""
from __future__ import annotations

import asyncio
import json
import threading
import uuid
from datetime import datetime
from typing import Any, Callable, Optional

from injector import inject

from agentclaw.community.core.bot_config_manifest.apply.context import ApplyContext
from agentclaw.community.core.bot_config_manifest.apply.order import (
    ApplyPhase,
)
from agentclaw.community.core.bot_config_manifest.apply.orchestrator import (
    ApplyOrchestrator,
)
from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    ApplyReport,
    ApplyStatus,
    CategoryResult,
    EntryOutcome,
    EntryResult,
    SourceResolution,
)
from agentclaw.community.core.bot_config_manifest.apply.registry import (
    build_materialisers,
)
from agentclaw.community.core.bot_config_manifest.bot_config_manifest_apply_service_protocol import (
    ApplyAccepted,
    BotConfigManifestApplyServiceProtocol,
    ManifestApplyInProgressError,
)
from agentclaw.community.core.bot_config_manifest.bot_config_manifest_service_protocol import (
    BotConfigManifestServiceProtocol,
)
from agentclaw.community.core.bot_startup_script.bot_startup_script_service_protocol import (
    BotStartupScriptServiceProtocol,
)
from agentclaw.community.core.bot_config_manifest.capabilities import (
    ManifestCategory,
    ManifestSection,
    parse_category,
)
from agentclaw.community.core.mcp.mcp_auth_service_protocol import (
    MCPAuthServiceProtocol,
)
from agentclaw.community.core.repository.protocols.bot.config_manifest_apply import (
    BotConfigManifestApplyLockRepositoryProtocol,
    BotConfigManifestApplyRepositoryProtocol,
)
from agentclaw.community.core.skill_center.direct_activation_service_protocol import (
    DirectActivationServiceProtocol,
)
from agentclaw.community.log import get_logger
from agentclaw.community.utils.avernet_tenant import (
    bind_current_avernet_tenant,
    get_current_avernet_tenant,
)
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()

#: How long a lock may be held before another apply may take it.
#:
#: Also what bounds a report stranded at ``RUNNING``: a process killed mid-apply
#: never runs its ``finally``, so the row would poll forever. Past this age the
#: read derives ``FAILED`` instead. Derived at read time rather than swept, so
#: there is no second mechanism to keep alive.
#:
#: Generous, because it is a safety net rather than a timeout: an apply that
#: legitimately takes minutes (W5 fetching several sources) must not have its
#: lock stolen mid-write.
APPLY_LOCK_TTL_SECONDS = 30 * 60


class BotConfigManifestApplyService(BotConfigManifestApplyServiceProtocol):
    """Start applies, and read what they did."""

    @inject
    def __init__(
        self,
        manifest_service: BotConfigManifestServiceProtocol,
        apply_repository: BotConfigManifestApplyRepositoryProtocol,
        lock_repository: BotConfigManifestApplyLockRepositoryProtocol,
        script_service_provider: Callable[[], BotStartupScriptServiceProtocol],
        activation_service_provider: Callable[[], DirectActivationServiceProtocol],
        mcp_auth_service_provider: Callable[[], MCPAuthServiceProtocol],
    ) -> None:
        self._manifests = manifest_service
        self._applies = apply_repository
        self._locks = lock_repository
        # Lazy providers rather than the services themselves, the way the
        # sibling manifest service holds its teclaw test: the bot-configuration
        # graph reaches back into this module's package, and holding concrete
        # instances would close an import cycle at construction.
        self._script_service_provider = script_service_provider
        self._activation_service_provider = activation_service_provider
        self._mcp_auth_service_provider = mcp_auth_service_provider

    # ── starting ────────────────────────────────────────────────────────────

    def start_apply(
        self,
        *,
        entity_id: str,
        bot_id: str,
        bot: dict,
        owner_id: str,
        actor_id: str,
        trigger: str = "explicit",
        phases: Optional[frozenset[ApplyPhase]] = None,
    ) -> ApplyAccepted:
        """Lock, validate, record ``RUNNING``, start the thread, return the id.

        The order is the contract. Both refusals happen **before** an id exists,
        so a caller never holds a handle to an apply that never ran.
        """
        env = get_current_env()
        lock = self._locks.acquire(
            env=env, entity_id=entity_id, bot_id=bot_id, holder_user_id=actor_id
        )
        if lock is None:
            if not self._reap_stale_lock(env=env, entity_id=entity_id, bot_id=bot_id):
                raise ManifestApplyInProgressError(bot_id)
            lock = self._locks.acquire(
                env=env, entity_id=entity_id, bot_id=bot_id, holder_user_id=actor_id
            )
            if lock is None:
                raise ManifestApplyInProgressError(bot_id)

        try:
            # Raises ManifestValidationError, before an id is minted, if the
            # stored document no longer validates for this bot.
            parsed = self._parsed_or_empty(
                entity_id=entity_id, bot_id=bot_id, bot=bot
            )
            apply_id = uuid.uuid4().hex
            started_at = datetime.now()
            self._applies.start(
                env=env,
                entity_id=entity_id,
                bot_id=bot_id,
                apply_id=apply_id,
                trigger=trigger,
                actor=actor_id,
                report=json.dumps(
                    ApplyReport(
                        apply_id=apply_id,
                        bot_id=bot_id,
                        trigger=trigger,
                        status=ApplyStatus.RUNNING,
                        started_at=started_at,
                    ).as_payload()
                ),
            )
        except Exception:
            # Nothing started, so the lock must not be left behind.
            self._locks.release(
                env=env,
                entity_id=entity_id,
                bot_id=bot_id,
                lock_token=lock.lock_token,
            )
            raise

        ctx = self._context(
            bot_id=bot_id,
            bot=bot,
            owner_id=owner_id,
            actor_id=actor_id,
            entity_id=entity_id,
            env=env,
        )

        # ``bind_current_avernet_tenant`` captures the tenant AT WRAP TIME —
        # here, inside the request thread — and re-establishes it inside the new
        # one. Wrapped inline at the construction site, never as an @decorator:
        # it looks like one (it uses functools.wraps) but a decorator on a
        # module-level function would capture at *import*, when there is no
        # request, and bind the default tenant forever.
        #
        # This is an isolation control, not a nicety. A wrong tenant here
        # substitutes the wrong ${BOT_TENANT} *and* reads and writes the
        # manifest tables under the wrong tenant.
        threading.Thread(
            target=bind_current_avernet_tenant(self._run),
            kwargs={
                "ctx": ctx,
                "parsed": parsed,
                "apply_id": apply_id,
                "trigger": trigger,
                "started_at": started_at,
                "phases": phases,
                "lock_token": lock.lock_token,
            },
            daemon=True,
            name=f"manifest-apply-{bot_id}",
        ).start()

        return ApplyAccepted(apply_id=apply_id, status=ApplyStatus.RUNNING)

    def _run(
        self,
        *,
        ctx: ApplyContext,
        parsed: dict,
        apply_id: str,
        trigger: str,
        started_at: datetime,
        phases: Optional[frozenset[ApplyPhase]],
        lock_token: str,
    ) -> None:
        """The background half. Always terminates the report and the lock.

        Both the terminal write and the lock release are in ``finally`` blocks:
        a raising orchestrator must not leave a report polling forever, and must
        not leave the bot locked against every future apply.
        """
        report: ApplyReport | None = None
        try:
            report = asyncio.run(
                self._orchestrator().apply(
                    ctx,
                    parsed,
                    apply_id=apply_id,
                    trigger=trigger,
                    started_at=started_at,
                    phases=phases,
                )
            )
        except Exception as exc:  # noqa: BLE001 - a daemon thread has no caller
            logger.exception(
                "[manifest_apply] apply raised, apply_id=%s, bot_id=%s",
                apply_id,
                ctx.bot_id,
            )
            report = ApplyReport(
                apply_id=apply_id,
                bot_id=ctx.bot_id,
                trigger=trigger,
                status=ApplyStatus.FAILED,
                started_at=started_at,
                finished_at=datetime.now(),
                categories=(),
            )
            self._record_engine_failure(report, exc)
        finally:
            try:
                if report is not None:
                    self._applies.finish(
                        env=ctx.env,
                        entity_id=ctx.entity_id,
                        bot_id=ctx.bot_id,
                        apply_id=apply_id,
                        status=report.status.value,
                        report=json.dumps(report.as_payload()),
                    )
            finally:
                self._locks.release(
                    env=ctx.env,
                    entity_id=ctx.entity_id,
                    bot_id=ctx.bot_id,
                    lock_token=lock_token,
                )

    def _record_engine_failure(self, report: ApplyReport, exc: Exception) -> None:
        """Log the cause; the stored report says FAILED with no entries.

        Deliberately not putting ``str(exc)`` in the report: an exception from
        the engine itself is a bug rather than something a caller can act on,
        and its text is the one place raw internals could reach a response body.
        """
        logger.error(
            "[manifest_apply] engine failure recorded, apply_id=%s: %s",
            report.apply_id,
            exc,
        )

    # ── dry run ─────────────────────────────────────────────────────────────

    async def dry_run(
        self,
        *,
        entity_id: str,
        bot_id: str,
        bot: dict,
        owner_id: str,
        actor_id: str,
    ) -> ApplyReport:
        """Compute the plan. Writes nothing, and mints no id."""
        env = get_current_env()
        parsed = self._parsed_or_empty(entity_id=entity_id, bot_id=bot_id, bot=bot)
        ctx = self._context(
            bot_id=bot_id,
            bot=bot,
            owner_id=owner_id,
            actor_id=actor_id,
            entity_id=entity_id,
            env=env,
        )
        return await self._orchestrator().apply(
            ctx,
            parsed,
            # No id: a dry run appears in no history, so there is nothing to
            # address it by. Naming it explicitly rather than minting one keeps
            # "writes nothing" true of the record as well as of the bot.
            apply_id="",
            trigger="dry_run",
            started_at=datetime.now(),
            dry_run=True,
        )

    # ── reading ─────────────────────────────────────────────────────────────

    def get_apply(
        self, *, entity_id: str, bot_id: str, apply_id: str
    ) -> Optional[ApplyReport]:
        """One apply's report. Scoped to the bot, so a foreign id is not found."""
        record = self._applies.get(
            env=get_current_env(),
            entity_id=entity_id,
            bot_id=bot_id,
            apply_id=apply_id,
        )
        return self._to_report(record, entity_id=entity_id, bot_id=bot_id)

    def last_apply(
        self, *, entity_id: str, bot_id: str
    ) -> Optional[ApplyReport]:
        """The newest report, or ``None`` when this bot has never applied."""
        record = self._applies.latest(
            env=get_current_env(), entity_id=entity_id, bot_id=bot_id
        )
        return self._to_report(record, entity_id=entity_id, bot_id=bot_id)

    # ── internals ───────────────────────────────────────────────────────────

    def _orchestrator(self) -> ApplyOrchestrator:
        """A fresh orchestrator over the registry. Holds no state between calls."""
        return ApplyOrchestrator(
            build_materialisers(
                script_service=self._script_service_provider(),
                activation_service=self._activation_service_provider(),
                mcp_auth_service=self._mcp_auth_service_provider(),
            )
        )

    def _context(
        self,
        *,
        bot_id: str,
        bot: dict,
        owner_id: str,
        actor_id: str,
        entity_id: str,
        env: str,
    ) -> ApplyContext:
        return ApplyContext(
            bot_id=bot_id,
            owner_id=owner_id,
            actor_id=actor_id,
            entity_id=entity_id,
            env=env,
            tenant=get_current_avernet_tenant(),
            engine_type=str(bot.get("active_engine") or ""),
            bot_type=str(bot.get("bot_type") or ""),
            bot=bot,
            capabilities=self._manifests.capabilities_for_bot(bot),
        )

    def _parsed_or_empty(
        self, *, entity_id: str, bot_id: str, bot: dict
    ) -> dict:
        """The stored document, re-validated, or ``{}`` when there is none.

        A bot with no manifest applies nothing and reports nothing applied —
        not an error, the same rule that makes an absent manifest read as an
        empty document rather than a 404.

        Re-validating is not paranoia: capabilities resolve from the bot's
        engine, which can change after a document is accepted, so a construct
        that was appliable at ``PUT`` may not be now.
        """
        record = self._manifests.get(entity_id=entity_id, bot_id=bot_id)
        if record is None:
            return {}
        result = self._manifests.validate(
            document=record.document,
            active_engine=bot.get("active_engine"),
            bot_type=bot.get("bot_type"),
        )
        return result.parsed

    def _to_report(
        self, record: Any, *, entity_id: str, bot_id: str
    ) -> Optional[ApplyReport]:
        """Rebuild a stored report, deriving ``FAILED`` for a stranded one.

        A report still ``RUNNING`` whose lock has gone stale belonged to a
        process that died before its ``finally`` could run. Reading it as
        ``FAILED`` is what stops a poller waiting forever, and doing it here —
        at read time — means there is no sweeper process to keep alive.
        """
        if record is None:
            return None
        payload = json.loads(record.report) if record.report else {}
        status = ApplyStatus(record.status)
        if status is ApplyStatus.RUNNING and self._is_abandoned(
            entity_id=entity_id, bot_id=bot_id
        ):
            status = ApplyStatus.FAILED
        return _report_from_payload(payload, record=record, status=status)

    def _is_abandoned(self, *, entity_id: str, bot_id: str) -> bool:
        """True when no live lock backs a ``RUNNING`` report.

        Either the lock is gone (released without the terminal write landing) or
        it is older than the TTL, so no apply can still be working under it.
        """
        env = get_current_env()
        held = self._locks.get(env=env, entity_id=entity_id, bot_id=bot_id)
        if held is None:
            return True
        return (
            self._locks.get_if_stale(
                env=env,
                entity_id=entity_id,
                bot_id=bot_id,
                ttl_seconds=APPLY_LOCK_TTL_SECONDS,
            )
            is not None
        )

    def _reap_stale_lock(self, *, env: str, entity_id: str, bot_id: str) -> bool:
        """Drop a lock whose holder is long gone. Returns whether one was freed."""
        stale = self._locks.get_if_stale(
            env=env,
            entity_id=entity_id,
            bot_id=bot_id,
            ttl_seconds=APPLY_LOCK_TTL_SECONDS,
        )
        if stale is None:
            return False
        logger.warning(
            "[manifest_apply] reaping stale lock, env=%s, entity_id=%s, bot_id=%s",
            env,
            entity_id,
            bot_id,
        )
        return self._locks.release(
            env=env,
            entity_id=entity_id,
            bot_id=bot_id,
            lock_token=stale.lock_token,
        )


def _report_from_payload(
    payload: dict, *, record: Any, status: ApplyStatus
) -> ApplyReport:
    """Rebuild the in-memory report from what was stored.

    The stored JSON is the wire shape, so this is its inverse. Entry outcomes
    round-trip through the enum rather than being carried as raw strings: a
    value the enum does not know is a corrupted row, and failing here is better
    than serving it onward as if it meant something.
    """
    categories: list[CategoryResult] = []
    by_category: dict[str, list[EntryResult]] = {}
    for entry in payload.get("entries") or []:
        construct = _construct_of(entry.get("category"))
        if construct is None:
            continue
        by_category.setdefault(entry["category"], []).append(
            EntryResult(
                construct=construct,
                identity=entry.get("name") or "",
                outcome=EntryOutcome(entry["action"]),
                reason=entry.get("error"),
                note=entry.get("note"),
            )
        )
    for category in payload.get("categories") or []:
        construct = _construct_of(category.get("category"))
        if construct is None:
            continue
        categories.append(
            CategoryResult(
                construct=construct,
                entries=tuple(by_category.get(category["category"], ())),
                removals=tuple(category.get("removed") or ()),
                aborted=bool(category.get("aborted")),
            )
        )
    return ApplyReport(
        apply_id=record.apply_id,
        bot_id=record.bot_id,
        trigger=record.trigger,
        status=status,
        started_at=record.started_at,
        finished_at=record.finished_at,
        categories=tuple(categories),
        sources=tuple(
            SourceResolution(
                name=source.get("name", ""),
                ref=source.get("ref"),
                resolved_sha=source.get("resolved_sha"),
                auth=source.get("auth"),
            )
            for source in payload.get("sources") or []
        ),
    )


def _construct_of(name: Any) -> ManifestCategory | ManifestSection | None:
    """A stored category name back into its construct, or ``None`` if unknown."""
    category = parse_category(name)
    if category is not None:
        return category
    try:
        return ManifestSection(name)
    except ValueError:
        return None


__all__ = ["APPLY_LOCK_TTL_SECONDS", "BotConfigManifestApplyService"]
