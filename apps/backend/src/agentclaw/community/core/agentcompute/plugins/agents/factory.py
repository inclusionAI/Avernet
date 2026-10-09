from __future__ import annotations

from agentclaw.community.core.agentcompute.config.settings import LlmConfig
from agentclaw.community.core.agentcompute.domain.models import AgentSpecRaw, Run
from agentclaw.community.core.agentcompute.spi.subagent import Subagent
from agentclaw.community.core.agentcompute.plugins.agents.llm_subagent import LLMBackedSubagent
from agentclaw.community.core.agentcompute.spi.llm_provider import LLMProvider


class DefaultSubagentFactory:

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    def create(self, spec: AgentSpecRaw, run: Run) -> Subagent:
        if spec.kind.value == "avernet":
            from agentclaw.community.core.agentcompute.plugins.agents.avernet_subagent import AvernetSubagent
            return AvernetSubagent(spec, run, self._llm)
        return LLMBackedSubagent(spec, run, self._llm)