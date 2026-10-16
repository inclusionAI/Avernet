from __future__ import annotations

from agentclaw.community.core.agentswarm.domain.models import (
    AgentId, AgentKind, AgentSpec, Run,
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


class DefaultSubagentFactory:

    def __init__(self, llm: LLMProvider,
                 platform: BotPlatform | None = None,
                 attempt_repo: AgentAttemptRepository | None = None) -> None:
        self._llm = llm
        self._platform = platform
        self._attempt_repo = attempt_repo

    def create(self, spec: AgentSpec, run: Run,
               agent_id: AgentId, attempt: int = 1) -> Subagent:
        # No default for ``agent_id``: the avernet kind derives the
        # platform idempotency key from it, and a name fallback would let
        # one same-named spawn's teardown delete the other's bot.
        if spec.kind is AgentKind.BOT:
            # Fail clear at spawn time: an avernet-kind agent needs the host's
            # in-process BotPlatform binding; silently skipping to the LLM
            # flavor (or to a removed HTTP loop) would misrun the work.
            if self._platform is None:
                raise AgentSwarmConfigError(
                    "agentswarm avernet agent requested but no "
                    "BotPlatform is bound in this profile"
                )
            # Bots provision under the run's authenticated caller (P0-9
            # no-service-account stance); a callerless run cannot own
            # what it creates.
            if not (run.owner_id or "").strip():
                raise AgentSwarmConfigError(
                    "agentswarm avernet agent requested but the run "
                    "carries no caller identity to provision bots under"
                )
            from agentclaw.community.plugins.community.agentswarm.avernet_subagent import AvernetSubagent
            return AvernetSubagent(spec, run, self._platform,
                                   agent_id=agent_id, attempt=attempt)
        return LLMBackedSubagent(spec, run, self._llm, agent_id,
                                 attempt=attempt)

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
                # binding: they stay SPAWNED as the attempt's durable
                # identity and the sweep never deletes for them.
                continue
            executor_id = row["executor_id"]
            if not executor_id.strip() or executor_id in seen_ids:
                continue
            seen_ids.add(executor_id)
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
                self._attempt_repo.mark_binding_destroyed(
                    run.run_id, run.tenant, executor_id)
                destroyed.append(executor_id)
        return destroyed

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