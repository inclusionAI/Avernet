"""Prefix target resolver — dispatches a proxypass target string by prefix."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sandboxproxy.community.config import UserConfig
from sandboxproxy.community.plugins.resolver.arca import ArcaTargetResolver
from sandboxproxy.community.plugins.resolver.local import LocalTargetResolver
from sandboxproxy.community.plugins.resolver.teclaw import TeclawTargetResolver


class PrefixTargetResolver:
    """Composite resolver dispatching ``ARCA_``/``TECLAW_``/``LOCAL_`` targets.

    The config-selected default (``plugins.resolver: prefix``); each concrete
    prefix resolver is independently swappable for enterprise extension.
    """

    prefix = "prefix"

    def __init__(self, config: UserConfig | Mapping[str, Any]) -> None:
        normalized = (
            config
            if isinstance(config, UserConfig)
            else UserConfig.model_validate(config)
        )
        self._resolvers: dict[
            str, ArcaTargetResolver | TeclawTargetResolver | LocalTargetResolver
        ] = {
            "ARCA_": ArcaTargetResolver(normalized),
            "TECLAW_": TeclawTargetResolver(normalized),
            "LOCAL_": LocalTargetResolver(normalized),
        }

    async def start(self) -> None:
        for resolver in self._resolvers.values():
            start = getattr(resolver, "start", None)
            if start is not None:
                await start()

    async def shutdown(self) -> None:
        for resolver in self._resolvers.values():
            shutdown = getattr(resolver, "shutdown", None)
            if shutdown is not None:
                await shutdown()

    async def resolve(self, target_host: str) -> dict[str, str]:
        for prefix, resolver in self._resolvers.items():
            if target_host.startswith(prefix):
                return await resolver.resolve(target_host)
        raise ValueError(f"Unsupported proxypass target: {target_host!r}")
