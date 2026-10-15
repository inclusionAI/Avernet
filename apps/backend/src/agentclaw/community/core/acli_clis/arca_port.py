"""``AcliPort`` over the engine adapter's ``/api/cli/acli/*`` endpoints.

Built like ``ArcaCliToolPort``: resolve the bot's device, call the engine over
:class:`DeviceAdapterTransport`, and shape every failure the same way. The
envelope is read as well as the status, because the engine answers a refusal
with ``200`` + ``success: false``.
"""
from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable

from agentclaw.community.core.acli_clis.port import (
    AcliMarketPage,
    AcliPort,
    AcliPortError,
    AcliTool,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterHTTPStatusError,
    DeviceAdapterTimeoutError,
    DeviceAdapterTransport,
)

logger = get_logger()


@runtime_checkable
class BotDeviceContextPort(Protocol):
    """``DeviceContextResolver.resolve_for_bot``, named by the one method used.

    The real resolver is not imported: its package reaches the DI container at
    import time (see ``bot_config_manifest/cli_tools/arca_port.py``).
    """

    def resolve_for_bot(self, bot_id: str, user_id: str) -> Any: ...

INSTALL_PATH = "/api/cli/acli/install"
UNINSTALL_PATH = "/api/cli/acli/uninstall"
MARKET_PATH = "/api/cli/acli/market"

#: The engine gives acli 300 s to install and 30 s + 300 s to uninstall
#: (list, then uninstall); these leave room for the hop on top.
INSTALL_TIMEOUT_SECONDS = 330.0
UNINSTALL_TIMEOUT_SECONDS = 360.0
MARKET_TIMEOUT_SECONDS = 60.0


class ArcaAcliPort(AcliPort):
    """Calls the engine's acli endpoints. Nothing else."""

    def __init__(
        self, *, resolver: BotDeviceContextPort, transport: DeviceAdapterTransport
    ) -> None:
        self._resolver = resolver
        self._transport = transport

    async def install(
        self, *, bot_id: str, owner_id: str, name: str, install_dir: str
    ) -> AcliTool:
        data = await self._invoke(
            bot_id, owner_id, "POST", INSTALL_PATH,
            body={"name": name, "install_dir": install_dir},
            timeout=INSTALL_TIMEOUT_SECONDS,
        )
        return _tool(data)

    async def uninstall(
        self, *, bot_id: str, owner_id: str, name: str, install_dir: str
    ) -> bool:
        data = await self._invoke(
            bot_id, owner_id, "POST", UNINSTALL_PATH,
            body={"name": name, "install_dir": install_dir},
            timeout=UNINSTALL_TIMEOUT_SECONDS,
        )
        return bool(data.get("uninstalled"))

    async def market(
        self, *, bot_id: str, owner_id: str, keyword: str, page_no: int, page_size: int
    ) -> AcliMarketPage:
        data = await self._invoke(
            bot_id, owner_id, "GET", MARKET_PATH,
            params={"keyword": keyword, "page_no": page_no, "page_size": page_size},
            timeout=MARKET_TIMEOUT_SECONDS,
        )
        rows = data.get("items")
        items = tuple(
            _tool(row) for row in (rows if isinstance(rows, list) else [])
            if isinstance(row, Mapping)
        )
        total = data.get("total")
        return AcliMarketPage(
            items=items,
            total=total if isinstance(total, int) else len(items),
            has_more=bool(data.get("has_more")),
        )

    async def _invoke(
        self,
        bot_id: str,
        owner_id: str,
        method: str,
        path: str,
        *,
        timeout: float,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        try:
            context = self._resolver.resolve_for_bot(bot_id, owner_id)
        except Exception as error:
            # No binding, an offline device, or no connection info: in every
            # case there is no container to run acli in.
            logger.warning(
                "acli_port_not_running bot_id=%s path=%s error_type=%s",
                bot_id, path, type(error).__name__,
            )
            raise AcliPortError("not_running", "the bot has no running container") from error
        try:
            response = await self._transport.invoke(
                context.conn_info, method, path, body=body, params=params, timeout=timeout,
            )
        except DeviceAdapterEndpointNotFoundError as error:
            raise AcliPortError(
                "engine_unsupported", "the bot's engine has no acli endpoints"
            ) from error
        except DeviceAdapterHTTPStatusError as error:
            if error.status_code == 501:
                raise AcliPortError(
                    "engine_unsupported", "the bot's engine does not support acli tools"
                ) from error
            if error.status_code == 400:
                raise AcliPortError("invalid_input", "the engine refused the request") from error
            raise AcliPortError(
                "engine_unreachable", f"the engine answered HTTP {error.status_code}"
            ) from error
        except DeviceAdapterTimeoutError as error:
            raise AcliPortError("timeout", "the engine did not answer in time") from error
        except Exception as error:
            logger.warning(
                "acli_port_unreachable bot_id=%s path=%s error_type=%s",
                bot_id, path, type(error).__name__,
            )
            raise AcliPortError("engine_unreachable", "the engine could not be reached") from error

        if not isinstance(response, Mapping):
            raise AcliPortError("engine_unreachable", "the engine answered with no envelope")
        if response.get("success") is not True:
            raise AcliPortError(
                str(response.get("error") or "engine_refused"),
                str(response.get("message") or "the engine refused the request"),
            )
        data = response.get("data")
        return data if isinstance(data, Mapping) else {}


def _tool(data: Mapping[str, Any]) -> AcliTool:
    return AcliTool(
        name=str(data.get("name") or ""),
        command=str(data.get("command") or ""),
        description=str(data.get("description") or ""),
        version=str(data.get("version") or ""),
    )


__all__ = [
    "ArcaAcliPort",
    "BotDeviceContextPort",
    "INSTALL_PATH",
    "MARKET_PATH",
    "UNINSTALL_PATH",
]
