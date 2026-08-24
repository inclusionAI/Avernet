"""ARCA target resolver — resolves ``ARCA_`` targets into an Aliyun ACK pod upstream host.

Target format: ``ARCA_{sandbox_id}[@{tenant}][:{port}]``.

The resolved upstream is a stable gateway that fronts the ACK pods. The
sandbox is selected by injecting ``x-agent-sandbox-id``/``x-agent-sandbox-port``
headers plus the per-tenant ``x-agent-sandbox-api-key`` credential (read straight
from config in the community build — no Mist/SM4).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sandboxproxy.community.config import AliyunAckClusterConfig, UserConfig

_DEFAULT_PORT = "8080"
_DEFAULT_TENANT = "0"


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
        if not rest:
            raise ValueError("ARCA_ target has no sandbox id")

        sandbox_id, sandbox_port, tenant = _parse_rest(rest)

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
            "x-agent-sandbox-id": sandbox_id,
            "x-agent-sandbox-port": sandbox_port,
            "x-agent-sandbox-api-key": cluster.api_key_for(tenant),
        }


def _parse_rest(rest: str) -> tuple[str, str, str]:
    """Parse the portion after ``ARCA_`` into ``(sandbox_id, port, tenant)``.

    Supported shapes:
        ``12345``          → id=12345, port=8080, tenant=0
        ``12345:9090``     → id=12345, port=9090, tenant=0
        ``12345@1``        → id=12345, port=8080, tenant=1
        ``12345@1:9090``   → id=12345, port=9090, tenant=1
    """
    head = rest
    tail = ""
    tenant = _DEFAULT_TENANT

    # Split off the @tenant suffix first: {head}@{tenant}[:{port}]
    if "@" in rest:
        head, _, suffix = rest.partition("@")
        if suffix:
            tenant, _, port_part = suffix.partition(":")
            tail = port_part if port_part else tail
            if not tenant:
                tenant = _DEFAULT_TENANT

    # head is now {sandbox_id} or {sandbox_id}:{port}
    sid, sep, port = head.partition(":")
    if not sid:
        raise ValueError("ARCA_ target has no sandbox id")

    sandbox_id = sid
    sandbox_port = port if sep and port else (tail or _DEFAULT_PORT)
    if not sandbox_port:
        raise ValueError("ARCA_ target has an empty port")

    return sandbox_id, sandbox_port, tenant
