"""SPI contracts for the demo's community layer.

These protocols define behavior; concrete ``community.plugins``
implementations supply them later.
"""

from ._agent import Agent, AgentContext, AgentSpec, NodeInput, NodeResult
from ._database import DatabasePlugin
from ._driver import Driver, EventCallback, RunEvent, RunLog, RunResult
from ._llm import LLMProvider, LLMProviderPlugin
from ._logger import LoggerPlugin
from ._planner import PlanError, Planner
from ._replanner import HaltReason, PlanExtension, ReplanError, Replanner
from ._runner import AppRunnerPlugin
from ._tracer import TracerPlugin

__all__ = [
    "Agent",
    "AgentContext",
    "AgentSpec",
    "AppRunnerPlugin",
    "DatabasePlugin",
    "Driver",
    "EventCallback",
    "HaltReason",
    "LLMProvider",
    "LLMProviderPlugin",
    "LoggerPlugin",
    "NodeInput",
    "NodeResult",
    "PlanError",
    "PlanExtension",
    "Planner",
    "Replanner",
    "ReplanError",
    "RunEvent",
    "RunLog",
    "RunResult",
    "TracerPlugin",
]
