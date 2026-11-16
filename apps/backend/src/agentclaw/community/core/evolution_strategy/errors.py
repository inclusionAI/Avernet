"""Failures a strategy must handle (03-strategy.zh-CN.md §4.3)."""

from __future__ import annotations


class EvolutionError(Exception):
    """Base for every refusal a strategy can observe through ``ctx``."""


class CapabilityNotGranted(EvolutionError):
    """A context part the strategy did not declare in ``needs``."""


class BudgetExhausted(EvolutionError):
    """A charge would exceed the run's budget; the strategy must stop."""


class RunCancelled(EvolutionError):
    """The run was cancelled (owner or kill switch); the strategy must stop."""


class UnknownAgentDefinition(EvolutionError):
    """``ctx.agents.start`` named a definition the registration record does not carry."""


class StaleAttempt(EvolutionError):
    """A call from a superseded attempt of a re-dispatched run."""


class UnknownCapability(EvolutionError):
    """``needs`` named a ``name@version`` the catalog does not carry (03 §3.2)."""


class InvalidCapabilityArguments(EvolutionError):
    """``needs`` arguments do not satisfy the catalog entry's schema."""


class NoProviderForEngine(EvolutionError):
    """A needed capability, or an agent definition, has no provider for the engine."""


class AgentDefinitionInvalid(EvolutionError):
    """An uploaded definition failed its engine's definition contract."""


class VersionExists(EvolutionError):
    """The id and version are already registered with different content."""


class StrategyNotFound(EvolutionError):
    """No registered version under that id and version."""
