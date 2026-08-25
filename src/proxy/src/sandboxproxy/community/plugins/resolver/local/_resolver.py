"""LOCAL target resolver — resolves ``LOCAL_{device_id}@{template_id}:{port}[:{session_id}]`` targets."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sandboxproxy.community.config import UserConfig


class LocalTargetResolver:
    """Resolve ``LOCAL_`` targets into a BaaS host + ``local_path_prefix``."""

    prefix = "local"

    def __init__(self, config: UserConfig | Mapping[str, Any]) -> None:
        self._config = (
            config
            if isinstance(config, UserConfig)
            else UserConfig.model_validate(config)
        )

    def resolve(self, target_host: str) -> dict[str, str]:
        if not target_host.startswith("LOCAL_"):
            raise ValueError(f"Not a LOCAL_ target: {target_host!r}")
        rest = target_host[len("LOCAL_") :]
        if "@" not in rest:
            raise ValueError(
                "LOCAL_ target must be LOCAL_<device_id>@<template>:<port>[:session]"
            )
        if rest.count("@") != 1:
            raise ValueError("LOCAL_ target must contain exactly one '@'")
        device_id, sep, after = rest.partition("@")
        if not device_id or not sep or not after:
            raise ValueError("LOCAL_ target missing device id or remainder")

        template_id, colon, port_rest = after.partition(":")
        if not template_id or not colon or not port_rest:
            raise ValueError("LOCAL_ target missing '@' or port separator")
        port = port_rest.split(":", 1)[0]
        if not port:
            raise ValueError("LOCAL_ target missing port")

        baas_host = self._config.baas.get("host", "")
        if not baas_host:
            raise RuntimeError(
                "baas host is not configured; cannot resolve LOCAL target"
            )
        path_prefix = (
            f"/api/v1/paas/devices/{device_id}@{template_id}/invoke-http/{port}"
        )
        return {
            "baas_host": baas_host,
            "device_id": device_id,
            "template_id": template_id,
            "port": port,
            "local_path_prefix": path_prefix,
        }
