"""Deprecated acceptance builder; production composition lives in apps/evolution."""
from agentclaw.community.di.evolution_acceptance.runtime import (
    EvolutionRuntime, build_evolution_runtime,
)

__all__ = ["EvolutionRuntime", "build_evolution_runtime"]
