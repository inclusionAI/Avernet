"""``AcliPort`` over the engine adapter's ``/api/cli/acli/*`` endpoints.

Built like ``ArcaCliToolPort``: resolve the bot's device, call the engine over
:class:`DeviceAdapterTransport`, and shape every failure the same way. The
envelope is read as well as the status, because the engine answers a refusal
with ``200`` + ``success: false``.
"""
from __future__ import annotations

import asyncio

from typing import Any, Mapping, Protocol, runtime_checkable

from agentclaw.community.core.acli_clis.port import (
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

#: The engine gives acli 300 s to install and 30 s + 300 s to uninstall
#: (list, then uninstall); these leave room for the hop on top.
INSTALL_TIMEOUT_SECONDS = 330.0
UNINSTALL_TIMEOUT_SECONDS = 360.0


class ArcaAcliPort(AcliPort):
    """Calls the engine's acli endpoints. Nothing else."""

    def __init__(
        self, *, resolver: BotDeviceContextPort, transport: DeviceAdapterTransport
    ) -> None:
        self._resolver = resolver
        self._transport = transport

    async def install(
        self, *, bot_id: str, owner_id: str, name: str
    ) -> AcliTool:
        data = await self._invoke(
            bot_id, owner_id, "POST", INSTALL_PATH,
            body={"name": name},
            timeout=INSTALL_TIMEOUT_SECONDS,
        )
        tool = _tool(data)
        if not tool.name or not tool.command:
            # Without the installed name the service could neither check it
            # nor undo the install, so a reply without it is not a success.
            raise AcliPortError("bad_output", "the engine's install reply names no tool")
        return tool

    async def uninstall(
        self, *, bot_id: str, owner_id: str, name: str
    ) -> bool:
        data = await self._invoke(
            bot_id, owner_id, "POST", UNINSTALL_PATH,
            body={"name": name},
            timeout=UNINSTALL_TIMEOUT_SECONDS,
        )
        uninstalled = data.get("uninstalled")
        if not isinstance(uninstalled, bool):
            # "Not installed" is a real answer; a missing one must not pass
            # for it, or a remove would succeed without evidence.
            raise AcliPortError("bad_output", "the engine's uninstall reply has no result")
        return uninstalled

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
            # Resolution can block on network I/O (BaaS connection info), so it
            # runs off the event loop, as the engine relay's does.
            context = await asyncio.to_thread(self._resolver.resolve_for_bot, bot_id, owner_id)
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
        if not isinstance(data, Mapping):
            # A success with no payload is a version-skewed or broken engine,
            # not an empty result. ``bad_output`` is an uncertain outcome to
            # the service, so nothing is assumed about the container.
            raise AcliPortError("bad_output", "the engine answered success with no data")
        return data


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
    "UNINSTALL_PATH",
]
