from __future__ import annotations

from typing import Any

from agentclaw.community.core.agentcompute.config.settings import LlmConfig
from agentclaw.community.core.agentcompute.domain.models import AgentSpecRaw, Run
from agentclaw.community.core.agentcompute.spi.subagent import Subagent
from agentclaw.community.core.agentcompute.plugins.agents.llm_subagent import LLMBackedSubagent
from agentclaw.community.core.agentcompute.spi.llm_provider import LLMProvider


class DefaultSubagentFactory:

    def __init__(self, llm: LLMProvider, egress: Any = None) -> None:
        self._llm = llm
        self._egress = egress

    def create(self, spec: AgentSpecRaw, run: Run) -> Subagent:
        if spec.kind.value == "avernet":
            from agentclaw.community.core.agentcompute.plugins.agents.avernet_subagent import AvernetSubagent
            return AvernetSubagent(spec, run, self._llm, egress=self._egress)
        return LLMBackedSubagent(spec, run, self._llm)