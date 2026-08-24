"""ARCA target resolver — resolves ``ARCA_{sandbox_id}[:{port}]`` targets."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sandboxproxy.community.config import AliyunAckClusterConfig, UserConfig

_DEFAULT_PORT = "8080"


class ArcaTargetResolver:
    """Resolve ``ARCA_`` targets into an Aliyun ACK pod upstream host."""

    prefix = "arca"

    def __init__(self, config: UserConfig | Mapping[str, Any]) -> None:
        self._config = (
            config
            if isinstance(config, UserConfig)
            else UserConfig.model_validate(config)
        )

    def resolve(self, target_host: str) -> dict[str, str]:
        if not target_host.startswith("ARCA_"):
            raise ValueError(f"Not an ARCA_ target: {target_host!r}")
        rest = target_host[len("ARCA_") :]
        sid_port = rest.split(":", 1)
        sandbox_id = sid_port[0]
        if not sandbox_id:
            raise ValueError("ARCA_ target has no sandbox id")
        sandbox_port = sid_port[1] if len(sid_port) > 1 else _DEFAULT_PORT

        cluster: AliyunAckClusterConfig = self._config.aliyun_ack_cluster
        api_server = cluster.api_server.strip()
        if not api_server:
            raise RuntimeError(
                "aliyun_ack_cluster.api_server is not configured; "
                "cannot resolve ARCA target"
            )
        if not api_server.startswith(("http://", "https://")):
            api_server = "https://" + api_server
        return {
            "arca_host": api_server,
            "sandbox_id": sandbox_id,
            "sandbox_port": sandbox_port,
        }
