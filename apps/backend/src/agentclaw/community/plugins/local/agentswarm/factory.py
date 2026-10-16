from __future__ import annotations

from agentclaw.community.core.agentswarm.domain.models import AgentId, AgentKind, AgentSpec, Run
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
                 platform: BotPlatform | None = None) -> None:
        self._llm = llm
        self._platform = platform

    def create(self, spec: AgentSpec, run: Run,
               agent_id: AgentId) -> Subagent:
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
            return AvernetSubagent(spec, run, self._platform, agent_id=agent_id)
        return LLMBackedSubagent(spec, run, self._llm)

    def destroy_run_agents_bots(self, run: Run, agents) -> list[str]:
        """Run-terminal reconciliation: destroy every bot provisioned for
        this run's avernet-kind agents, whether or not a live subagent
        instance still holds it (fail/cancel races can strand the create).

        Either a surviving agent row's bot_id, or the deterministic
        bot identity derived from (run_id, agent_id) — must never touch
        another run's bot (identity is spawn-scoped). Best-effort per
        agent: one failure never blocks the other deletes or the run's
        terminal write."""
        destroyed: list[str] = []
        if self._platform is None:
            return destroyed
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
        if destroyed:
            logger.info(
                "[agentswarm][factory] destroyed %d stranded bot(s) for run=%s: %s",
                len(destroyed), run.run_id, destroyed,
            )
        return destroyed