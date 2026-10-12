from __future__ import annotations

from agentclaw.community.core.agentcompute.domain.models import AgentSpec, Run
from agentclaw.community.core.agentcompute.runtime.observability import (
    AgentComputeObservability,
)
from agentclaw.community.core.agentcompute.service.protocols import (
    AgentComputeConfigError,
)
from agentclaw.community.core.agentcompute.spi.bot import BotPlatform
from agentclaw.community.core.agentcompute.spi.llm import LLMProvider
from agentclaw.community.core.agentcompute.spi.subagent import Subagent
from agentclaw.community.core.agentcompute.plugins.agents.llm import LLMBackedSubagent


class DefaultSubagentFactory:

    def __init__(self, llm: LLMProvider,
                 platform: BotPlatform | None = None,
                 observability: AgentComputeObservability | None = None) -> None:
        self._llm = llm
        self._platform = platform
        self._obs = observability

    def create(self, spec: AgentSpec, run: Run) -> Subagent:
        if spec.kind.value == "avernet":
            # Fail clear at spawn time: an avernet-kind agent needs the
            # host's in-process BotPlatform binding; silently skipping to
            # the LLM flavor (or to a removed HTTP loop) would misrun the work.
            if self._platform is None:
                raise AgentComputeConfigError(
                    "agentcompute avernet agent requested but no "
                    "BotPlatform is bound in this profile"
                )
            from agentclaw.community.core.agentcompute.plugins.agents.bot import AvernetSubagent
            return AvernetSubagent(spec, run, self._platform, observability=self._obs)
        return LLMBackedSubagent(spec, run, self._llm)