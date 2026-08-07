"""Resolve the effective runtime engine from bot metadata.

This module keeps engine-identity rules in a small, explicit contract so core
callers can depend on a stable interface instead of a concrete template helper.
Composition roots select the concrete resolver.
"""
from __future__ import annotations

from typing import Mapping, Protocol


def normalize_engine_type(engine_type: str | None) -> str:
    """Normalize historical engine spellings to the canonical wire form."""
    return (engine_type or "openclaw").strip().lower().replace("-", "_")


def normalize_template_type(template_type: str | None) -> str:
    return (template_type or "").strip().lower()


def resolve_effective_engine_for_template(
    *,
    engine_type: str | None,
    template_type: str | None,
) -> str:
    """Resolve the effective runtime engine for a bot/template pair."""
    normalized_engine = normalize_engine_type(engine_type)
    normalized_template_type = normalize_template_type(template_type)
    if (
        normalized_engine == "claude_code"
        and normalized_template_type
        and normalized_template_type != "normalcc"
    ):
        return "aicoding"
    return normalized_engine


class EffectiveEngineResolverProtocol(Protocol):
    """Contract for resolving the effective runtime engine."""

    def resolve_effective_engine(
        self,
        *,
        engine_type: str | None,
        template_type: str | None,
    ) -> str:
        """Return the effective engine after template-aware normalization."""
        ...


class IdentityEffectiveEngineResolver:
    """Default resolver that only normalizes the raw engine spelling."""

    def resolve_effective_engine(
        self,
        *,
        engine_type: str | None,
        template_type: str | None,
    ) -> str:
        return normalize_engine_type(engine_type)


class ClaudeCodeTemplateEffectiveEngineResolver:
    """Template-aware resolver for claude_code bots."""

    def resolve_effective_engine(
        self,
        *,
        engine_type: str | None,
        template_type: str | None,
    ) -> str:
        return resolve_effective_engine_for_template(
            engine_type=engine_type,
            template_type=template_type,
        )


class ConfiguredEffectiveEngineResolver:
    """Composition-root helper that picks a resolver by normalized engine key."""

    def __init__(
        self,
        *,
        resolvers: Mapping[str, EffectiveEngineResolverProtocol],
        default_resolver: EffectiveEngineResolverProtocol | None = None,
    ) -> None:
        self._default_resolver = default_resolver or IdentityEffectiveEngineResolver()
        self._resolvers = {
            normalize_engine_type(engine_type): resolver
            for engine_type, resolver in resolvers.items()
        }

    def resolve_effective_engine(
        self,
        *,
        engine_type: str | None,
        template_type: str | None,
    ) -> str:
        normalized_engine = normalize_engine_type(engine_type)
        resolver = self._resolvers.get(normalized_engine, self._default_resolver)
        return resolver.resolve_effective_engine(
            engine_type=normalized_engine,
            template_type=template_type,
        )
