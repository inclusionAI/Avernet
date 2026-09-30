"""Desktop WS policy; HTTP device credentials never enter this channel."""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

from agentclaw.community.core.engine_runtime.errors import EngineUpstreamError
from agentclaw.community.core.service_bot.services.baas_service import (
    BotWsConnectionInfoResponse,
)


@dataclass(frozen=True)
class DesktopConnectionConfig:
    mode: str = "direct"

    def __post_init__(self) -> None:
        if self.mode not in ("direct", "relay"):
            raise ValueError("desktop_connection.mode must be direct or relay")


class DesktopWsPort(Protocol):
    def get_ws_info(
        self,
        bind_id: int,
        port: int = 20003,
        path: str = "/api/openclaw/ws",
        tenant: str = "",
        device_affinity: str | None = None,
        device_uuid: str | None = None,
        ws_conn_mode: str | None = None,
    ) -> BotWsConnectionInfoResponse: ...


def validate_direct_url(url: str, path: str) -> str:
    try:
        parts = urlsplit(url)
        valid = (
            parts.scheme == "ws"
            and parts.hostname in ("localhost", "127.0.0.1", "::1")
            and parts.port is not None
            and parts.username is None
            and parts.password is None
            and not parts.fragment
            and parts.path == path
        )
    except ValueError:
        valid = False
    if not valid:
        raise EngineUpstreamError(
            "desktop provider returned an invalid local WebSocket address"
        )
    return url


@runtime_checkable
class DesktopConnectionServiceProtocol(Protocol):
    @property
    def config(self) -> DesktopConnectionConfig: ...

    def resolve(
        self, binding_id: int, owner_id: str, path: str
    ) -> BotWsConnectionInfoResponse: ...


class DesktopConnectionService:
    def __init__(self, provider: DesktopWsPort, config: DesktopConnectionConfig):
        self._provider = provider
        self.config = config

    def resolve(
        self, binding_id: int, owner_id: str, path: str
    ) -> BotWsConnectionInfoResponse:
        info = self._provider.get_ws_info(
            bind_id=binding_id,
            device_affinity=owner_id,
            path=path,
            ws_conn_mode=self.config.mode,
        )
        if self.config.mode == "direct":
            validate_direct_url(info.ws_url, path)
        return info
