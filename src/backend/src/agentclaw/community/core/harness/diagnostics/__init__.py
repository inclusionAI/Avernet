"""Diagnostic check items for LLM-powered bot configuration analysis.

Each diagnostic is an independent class with its own system prompt and
data gathering logic. Register new diagnostics here.
"""

from agentclaw.community.core.harness.diagnostics.base import Diagnostic, DiagnosticContext
from agentclaw.community.core.harness.diagnostics.agents import (
    AgentsSafetyRulesDiagnostic,
    AgentsFailFirstDiagnostic,
    AgentsBehaviorBoundariesDiagnostic,
)
from agentclaw.community.core.harness.diagnostics.tools import (
    ToolsDeclarationDiagnostic,
    ToolsMcpFormatDiagnostic,
)
from agentclaw.community.core.harness.diagnostics.config import (
    SoulPersonaDiagnostic,
)

__all__ = [
    "Diagnostic",
    "DiagnosticContext",
    "register_all_diagnostics",
]


def register_all_diagnostics() -> list[Diagnostic]:
    """Instantiate and return all built-in diagnostic check items."""
    return [
        AgentsSafetyRulesDiagnostic(),
        AgentsFailFirstDiagnostic(),
        AgentsBehaviorBoundariesDiagnostic(),
        ToolsDeclarationDiagnostic(),
        ToolsMcpFormatDiagnostic(),
        SoulPersonaDiagnostic(),
    ]