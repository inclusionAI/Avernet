"""Resolve a bot template's explicit runtime engine by bot type."""

from __future__ import annotations

from typing import Mapping, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from agentclaw.community.core.bot_management.services.template_service import (
        TemplateService,
    )


class TemplateRuntimeEngineTypeResolver(Protocol):
    """Resolve the template runtime engine for a bot type."""

    def resolve(self, *, bot_type: str, bot_id: str) -> str:
        """Return the explicit runtime engine, or an empty string if unsupported."""


class PersonalTemplateRuntimeEngineTypeResolver:
    """Read the explicit runtime engine from a personal bot's template."""

    def __init__(self, template_service: TemplateService) -> None:
        self._template_service = template_service

    def resolve(self, *, bot_type: str, bot_id: str) -> str:
        del bot_type
        template_config = self._template_service.get_template_config(bot_id)
        if not isinstance(template_config, dict):
            return ""
        runtime_engine_type = template_config.get("template_runtime_engine_type")
        if not isinstance(runtime_engine_type, str):
            return ""
        return runtime_engine_type.strip()


class EmptyTemplateRuntimeEngineTypeResolver:
    """Return no template runtime engine for unsupported bot types."""

    def resolve(self, *, bot_type: str, bot_id: str) -> str:
        del bot_type, bot_id
        return ""


class BotTypeTemplateRuntimeEngineTypeResolver:
    """Dispatch template runtime engine resolution by bot type."""

    def __init__(
        self,
        *,
        resolvers: Mapping[str, TemplateRuntimeEngineTypeResolver],
        default_resolver: TemplateRuntimeEngineTypeResolver,
    ) -> None:
        self._resolvers = dict(resolvers)
        self._default_resolver = default_resolver

    def resolve(self, *, bot_type: str, bot_id: str) -> str:
        resolver = self._resolvers.get(bot_type, self._default_resolver)
        return resolver.resolve(bot_type=bot_type, bot_id=bot_id)
