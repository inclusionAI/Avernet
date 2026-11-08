from __future__ import annotations

import time

from agentclaw.community.core.agentswarm.config.settings import (
    AgentSwarmOrchestrationConfig,
)
from agentclaw.community.core.agentswarm.domain.models import (
    AgentId, AgentKind, AgentSpec, BotState, Run, spawn_digest,
)
from agentclaw.community.core.agentswarm.repository.agent_attempt import (
    AgentAttemptRepository,
)
from agentclaw.community.core.agentswarm.service.protocols import (
    AgentSwarmConfigError,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.agentswarm.bot import BotPlatform, BotId
from agentclaw.community.plugin_api.agentswarm.llm import LLMProvider
from agentclaw.community.plugin_api.agentswarm.subagent import Subagent
from agentclaw.community.plugins.local.agentswarm.subagent_llm import LLMBackedSubagent

logger = get_logger(__name__)

# The blank-intent materialization grace derives from the same bootstrap
# deadline the pool's setup poll enforces (``orchestration.setup_timeout``):
# the factory takes the configured value from the DI root, and direct
# constructions keep the settings default rather than re-hardcoding it.
_DEFAULT_SETUP_TIMEOUT_SECONDS = AgentSwarmOrchestrationConfig().setup_timeout


def _now_ms() -> int:
    return int(time.time() * 1000)


class DefaultSubagentFactory:

    def __init__(self, llm: LLMProvider,
                 platform: BotPlatform | None = None,
                 attempt_repo: AgentAttemptRepository | None = None,
                 setup_timeout: float = _DEFAULT_SETUP_TIMEOUT_SECONDS,
                 setup_ceiling: float | None = None,
                 subagent_setup_delay: float = 0.0,
                 ) -> None:
        self._llm = llm
        self._platform = platform
        self._attempt_repo = attempt_repo
        self._setup_timeout = float(setup_timeout)
        # Without a deployment-provided ceiling the ready-poll budget can
        # only stay at or below the configured timeout — the DI root wires
        # the true slice room (slice_max_seconds - per_attempt_timeout).
        self._setup_ceiling = (float(setup_ceiling)
                               if setup_ceiling else self._setup_timeout)
        self._subagent_setup_delay = float(subagent_setup_delay)

    def create(self, spec: AgentSpec, run: Run,
               agent_id: AgentId, attempt: int = 1) -> Subagent:
        # No default for ``agent_id``: the bot kind derives the
        # platform idempotency key from it, and a name fallback would let
        # one same-named spawn's teardown delete the other's bot.
        if spec.kind is AgentKind.BOT:
            # Fail clear at spawn time: a bot-kind agent needs the host's
            # in-process BotPlatform binding; silently skipping to the LLM
            # flavor (or to a removed HTTP loop) would misrun the work.
            if self._platform is None:
                raise AgentSwarmConfigError(
                    "agentswarm bot agent requested but no "
                    "BotPlatform is bound in this profile"
                )
            # Bots provision under the run's authenticated caller (P0-9
            # no-service-account stance); a callerless run cannot own
            # what it creates.
            if not (run.owner_id or "").strip():
                raise AgentSwarmConfigError(
                    "agentswarm bot agent requested but the run "
                    "carries no caller identity to provision bots under"
                )
            from agentclaw.community.plugins.community.agentswarm.bot_subagent import AvernetSubagent
            opts = getattr(run, "run_options", None)
            ready_poll = (
                self._setup_timeout if opts is None
                else opts.resolved_setup_timeout(self._setup_timeout,
                                                 self._setup_ceiling)
            )
            return AvernetSubagent(spec, run, self._platform,
                                   agent_id=agent_id, attempt=attempt,
                                   ready_poll_timeout=ready_poll,
                                   setup_delay_seconds=self._subagent_setup_delay)
        return LLMBackedSubagent(spec, run, self._llm, agent_id,
                                 attempt=attempt,
                                 setup_delay_seconds=self._subagent_setup_delay)

    def destroy_run_agents_bots(self, run: Run, agents) -> list[str]:
        """Run-terminal reconciliation (D-M): ledger first, then the belt.

        Ledger sweep — for every persona the run journaled across all
        attempts and slices: bot-kind executor ids are deleted on the
        platform and their ledger rows marked DESTROYED (per row, after
        destroy success); llm-kind rows are skipped (nothing platform
        -backed); a cross-slice replay can journal one executor id on
        two rows, so delete calls are deduped by executor_id while both
        rows get marked. The legacy deterministic derivation
        (``ac_{sha1(run:agent)[:16]}`` — pre-change rows whose bots
        predate the ledger) runs alongside, quiet-if-gone: both paths
        destroy idempotently and never block each other. Best-effort per
        id: one failure never blocks the other deletes or the run's
        terminal write.

        Either a surviving agent row's bot_id, or a journaled ledger row,
        or the deterministic identity — must never touch another run's
        bot (identity is spawn-scoped)."""
        destroyed: list[str] = []
        if self._platform is None:
            return destroyed
        if self._attempt_repo is not None:
            destroyed += self._sweep_ledger_bots(run)
        destroyed += self._sweep_legacy_belt(run, agents)
        if destroyed:
            logger.info(
                "[agentswarm][factory] destroyed %d stranded bot(s) for run=%s: %s",
                len(destroyed), run.run_id, destroyed,
            )
        return destroyed

    def _sweep_ledger_bots(self, run: Run) -> list[str]:
        destroyed: list[str] = []
        seen_ids: set[str] = set()
        for row in self._attempt_repo.list_agent_bindings(
                run.run_id, run.tenant):
            if row["executor_kind"] != AgentKind.BOT.value:
                # llm-kind rows carry bare digests with no platform
                # binding: the sweep never deletes for them, but this
                # only ever runs for a TERMINAL run (the slice's own
                # finalize paths and the reaper's terminal pages), so
                # the row must still settle here — nothing platform
                # -backed exists that ever will, and an unsettled row
                # keeps _has_undestroyed_ledger True, re-visiting the
                # run every reaper cycle forever.
                # mark_binding_destroyed covers every live row of that
                # executor id (the destroy path's own id-coverage); a
                # blank llm intent row is instead addressed by its
                # (run, agent, attempt) correlation.
                try:
                    llm_executor_id = str(row["executor_id"] or "").strip()
                    if llm_executor_id:
                        self._attempt_repo.mark_binding_destroyed(
                            run.run_id, run.tenant, llm_executor_id)
                    else:
                        self._attempt_repo.mark_intent_binding_destroyed(
                            run.run_id, run.tenant,
                            AgentId(row["agent_id"]), int(row["slice_retry_index"]),
                        )
                except Exception as exc:
                    # A concurrent settle on the same row can win the
                    # ledger write; one row's mark failure must not
                    # abort the rest of the sweep — the reaper
                    # re-attempts this run.
                    logger.warning(
                        "[agentswarm][factory] ledger llm settle failed for "
                        "run=%s agent=%s: %s", run.run_id, row["agent_id"],
                        exc,
                    )
                continue
            executor_id = str(row["executor_id"] or "").strip()
            intent_row = not executor_id
            if intent_row:
                # A write-ahead intent row whose setup() never settled:
                # the with-attempt persona id is derivable from the row's
                # own correlation columns (host create digests the same
                # f"{run}:{agent}:{attempt}" key), so a partially created
                # persona is still addressable.
                executor_id = "agent_swarm_" + spawn_digest(
                    str(run.run_id), str(row["agent_id"]),
                    int(row["slice_retry_index"]), 16,
                )
            if executor_id in seen_ids:
                continue
            seen_ids.add(executor_id)
            if intent_row and self._defer_or_settle_unmaterialized(
                    run, row, executor_id):
                continue
            try:
                ok = bool(self._platform.delete_bot(
                    BotId(executor_id), owner_id=run.owner_id))
            except Exception as exc:
                logger.warning(
                    "[agentswarm][factory] ledger bot destroy failed for "
                    "run=%s executor=%s: %s", run.run_id, executor_id, exc,
                )
                continue
            if ok:
                try:
                    self._attempt_repo.mark_binding_destroyed(
                        run.run_id, run.tenant, executor_id)
                    if intent_row:
                        # Blank-id rows kill the executor_id-level mark: address
                        # them by their (run, agent, attempt) correlation.
                        self._attempt_repo.mark_intent_binding_destroyed(
                            run.run_id, run.tenant,
                            AgentId(row["agent_id"]), int(row["slice_retry_index"]),
                        )
                except Exception as exc:
                    # A concurrent settle on the same row can win the ledger
                    # write; the persona is already destroyed, so one row's
                    # mark failure must not abort the rest of the sweep.
                    logger.warning(
                        "[agentswarm][factory] ledger destroy mark failed for "
                        "run=%s executor=%s: %s", run.run_id, executor_id, exc,
                    )
                destroyed.append(executor_id)
        return destroyed

    def _defer_or_settle_unmaterialized(self, run: Run, row: dict,
                                        derived_id: str) -> bool:
        """Gate a blank-intent row on whether its persona materialized.

        A bot-kind persona's bootstrap is a DURABLE async job: the blank
        intent row is journaled at spawn, the persona materializes
        seconds to minutes later (task-queue retry cadence). A
        cancel-time sweep that deletes-if-gone and marks the row
        DESTROYED before that materialization orphans the persona
        forever — live-proven: a live ac_bot row, an ACTIVE BaaS
        device, and held quota that no path ever reclaims (a DESTROYED
        row is invisible to both the reaper and the settle fence).

        bot_status answers PROVISIONING (or raises / comes back
        blank) while nothing has landed for the id — no creation job,
        no bot row — and any other BotState means the platform holds
        something for this persona. Probe failures read as
        not-materialized: only the give-up horizon below bounds the
        deferral. The bootstrap deadline (``orchestration.setup_timeout``)
        bounds materialization; twice that is the give-up horizon.

        Returns True when the row is handled WITHOUT the destroy path:
        either deferred wholesale (no delete, no marks — the reaper's
        next cycle re-attempts after materialization), or settled
        after the give-up horizon was passed — the delete-if-gone
        would settle nothing there (nothing materialized, the
        quiet-if-gone delete reads False and the mark never fires),
        so give up cleanly: one quiet-if-gone delete as a
        belt-and-suspenders (harmless when no persona exists, a real
        destroy in the probe/delete race), then both marks
        unconditionally, so the row can never churn the reaper forever.
        Returns False when the persona materialized: the caller's
        destroy path owns the row exactly as before this gate existed.
        """
        status = ""
        try:
            status = str(self._platform.bot_status(
                BotId(derived_id), owner_id=run.owner_id,
            )).strip()
        except Exception:
            status = ""
        if status and status != BotState.PROVISIONING.value:
            return False
        started_ms = int(row.get("started_at") or 0)
        horizon_ms = started_ms + 2.0 * self._setup_timeout * 1000.0
        if started_ms > 0 and _now_ms() < horizon_ms:
            logger.info(
                "[agentswarm][factory] persona not yet materialized, "
                "deferring ledger destroy run=%s executor=%s attempt=%s",
                run.run_id, derived_id, row["slice_retry_index"],
            )
            return True
        try:
            self._platform.delete_bot(BotId(derived_id),
                                      owner_id=run.owner_id)
        except Exception as exc:
            logger.warning(
                "[agentswarm][factory] ledger bot give-up delete failed "
                "for run=%s executor=%s: %s", run.run_id, derived_id, exc,
            )
        try:
            self._attempt_repo.mark_binding_destroyed(
                run.run_id, run.tenant, derived_id)
            # Blank-id rows kill the executor_id-level mark: address them
            # by their (run, agent, attempt) correlation.
            self._attempt_repo.mark_intent_binding_destroyed(
                run.run_id, run.tenant,
                AgentId(row["agent_id"]), int(row["slice_retry_index"]),
            )
        except Exception as exc:
            logger.warning(
                "[agentswarm][factory] ledger destroy mark failed for "
                "run=%s executor=%s: %s", run.run_id, derived_id, exc,
            )
        return True

    def _sweep_legacy_belt(self, run: Run, agents) -> list[str]:
        destroyed: list[str] = []
        from agentclaw.community.plugins.community.agentswarm.host import bot_id_for

        for a in agents:
            if a.kind is not AgentKind.BOT:
                continue
            bot_id = a.bot_id or bot_id_for(str(run.run_id), str(a.agent_id))
            try:
                ok = bool(self._platform.delete_bot(BotId(bot_id), owner_id=run.owner_id))
            except Exception as exc:
                logger.warning(
                    "[agentswarm][factory] post-terminal bot destroy failed "
                    "for run=%s agent=%s bot=%s: %s",
                    run.run_id, a.agent_id, bot_id, exc,
                )
                continue
            if ok:
                destroyed.append(bot_id)
        return destroyed