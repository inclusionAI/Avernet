"""TECLAW target resolver — resolves ``TECLAW_{bot_id}@{template_id}:{port}`` targets."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sandboxproxy.community.config import UserConfig


class TeclawTargetResolver:
    """Resolve ``TECLAW_`` targets into a teclaw upstream host + bot-id header."""

    prefix = "teclaw"

    def __init__(self, config: UserConfig | Mapping[str, Any]) -> None:
        self._config = (
            config
            if isinstance(config, UserConfig)
            else UserConfig.model_validate(config)
        )

    def resolve(self, target_host: str) -> dict[str, str]:
        if not target_host.startswith("TECLAW_"):
            raise ValueError(f"Not a TECLAW_ target: {target_host!r}")
        rest = target_host[len("TECLAW_") :]
        if not rest or "@" not in rest:
            raise ValueError("TECLAW_ target must be TECLAW_<bot_id>@<template>:<port>")
        bot_id = rest.split("@", 1)[0]
        if not bot_id:
            raise ValueError("TECLAW_ target has no bot id")
        host = self._config.teclaw.get("host", "")
        if not host:
            raise RuntimeError(
                "teclaw host is not configured; cannot resolve TECLAW target"
            )
        return {"teclaw_host": host, "x-target-bot-id": bot_id}
