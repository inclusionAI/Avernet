from __future__ import annotations

from agentclaw.community.core.agentswarm.domain.models import AgentId, AgentSpec, Run
from agentclaw.community.core.agentswarm.service.protocols import (
    AgentSwarmConfigError,
)
from agentclaw.community.plugin_api.agentswarm.bot import BotPlatform
from agentclaw.community.plugin_api.agentswarm.llm import LLMProvider
from agentclaw.community.plugin_api.agentswarm.subagent import Subagent
from agentclaw.community.plugins.local.agentswarm.subagent_llm import LLMBackedSubagent


class DefaultSubagentFactory:

    def __init__(self, llm: LLMProvider,
                 platform: BotPlatform | None = None) -> None:
        self._llm = llm
        self._platform = platform

    def create(self, spec: AgentSpec, run: Run,
               agent_id: AgentId | None = None) -> Subagent:
        if spec.kind.value == "avernet":
            # Fail clear at spawn time: an avernet-kind agent needs the
            # host's in-process BotPlatform binding; silently skipping to
            # the LLM flavor (or to a removed HTTP loop) would misrun the work.
            if self._platform is None:
                raise AgentSwarmConfigError(
                    "agentswarm avernet agent requested but no "
                    "BotPlatform is bound in this profile"
                )
            from agentclaw.community.plugins.community.agentswarm.avernet_subagent import AvernetSubagent
            return AvernetSubagent(spec, run, self._platform, agent_id=agent_id)
        return LLMBackedSubagent(spec, run, self._llm)