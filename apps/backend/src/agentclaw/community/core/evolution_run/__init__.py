"""Deprecated core exports; use avernet_evolution.core.evolution_run."""
from avernet_evolution.core.evolution_run import *  # noqa: F403


def __getattr__(name):
    if name in {"EvolutionRuntime", "build_evolution_runtime"}:
        from agentclaw.community.di.evolution_acceptance import runtime

        return getattr(runtime, name)
    raise AttributeError(name)
