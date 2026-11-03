"""``AcliPort`` over the engine adapter's ``/api/cli/acli/*`` endpoints.

teclaw serves the same endpoints under its own API prefix,
``/api/v1/cli/acli/*``; the runtime's engine picks the prefix.

Built like ``ArcaCliToolPort``: resolve the runtime's device, call the engine
over :class:`DeviceAdapterTransport`, and shape every failure the same way.
The envelope is read as well as the status: an operation that ended in
failure is a ``200`` with ``success: false`` and the operation in
``data.operation``, and ``409`` / ``404`` answers carry an envelope too.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping, Protocol, runtime_checkable

from agentclaw.community.core.acli_clis.operations import (
    AcliAction,
    AcliRuntime,
    EngineOperation,
    EngineState,
)
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
class RuntimeContextPort(Protocol):
    """The device context of one stage of a bot (``resolve_stage_device_context``).

    Named by the one call used rather than imported: the stage resolver's
    package reaches the DI container at import time.
    """

    def resolve(self, *, bot_id: str, owner_id: str, stage: str) -> Any: ...


_ACLI_PREFIX = "/api/cli/acli"
#: The prefix per engine that does not serve the adapter's.
_ENGINE_PREFIXES = {"teclaw": "/api/v1/cli/acli"}

INSTALL_PATH = "/api/cli/acli/install"
UNINSTALL_PATH = "/api/cli/acli/uninstall"
OPERATION_PATH = "/api/cli/acli/operations/{id}"
TOOLS_PATH = "/api/cli/acli/tools"
_SUBMIT_PATHS = {AcliAction.INSTALL: INSTALL_PATH, AcliAction.UNINSTALL: UNINSTALL_PATH}

#: Submitting and cancelling are ``async`` on the engine, so they answer at
#: once; reading an operation is a local file read there. The listing runs
#: ``acli tool list``, which the engine bounds at 30 s.
SUBMIT_TIMEOUT_SECONDS = 30.0
READ_TIMEOUT_SECONDS = 15.0
LIST_TIMEOUT_SECONDS = 45.0


class ArcaAcliPort(AcliPort):
    """Calls the engine's acli endpoints. Nothing else."""

    def __init__(
        self, *, contexts: RuntimeContextPort, transport: DeviceAdapterTransport
    ) -> None:
        self._contexts = contexts
        self._transport = transport

    async def submit(
        self, runtime: AcliRuntime, *, action: AcliAction, name: str
    ) -> EngineOperation:
        envelope = await self._invoke(
            runtime, "POST", _SUBMIT_PATHS[action],
            body={"name": name, "async": True}, timeout=SUBMIT_TIMEOUT_SECONDS,
        )
        return _operation_of(envelope)

    async def get_operation(
        self, runtime: AcliRuntime, operation_id: str
    ) -> EngineOperation | None:
        try:
            envelope = await self._invoke(
                runtime, "GET", OPERATION_PATH.format(id=operation_id),
                timeout=READ_TIMEOUT_SECONDS, route_known=True,
            )
        except AcliPortError as error:
            if error.code == "operation_not_found":
                return None
            raise
        return _operation_of(envelope)

    async def cancel(
        self, runtime: AcliRuntime, *, action: AcliAction, name: str
    ) -> EngineOperation:
        envelope = await self._invoke(
            runtime, "POST", f"{_SUBMIT_PATHS[action]}/cancel",
            body={"name": name, "async": True}, timeout=SUBMIT_TIMEOUT_SECONDS,
        )
        return _operation_of(envelope)

    async def list_tools(self, runtime: AcliRuntime) -> list[AcliTool]:
        envelope = await self._invoke(runtime, "GET", TOOLS_PATH, timeout=LIST_TIMEOUT_SECONDS)
        if envelope.get("success") is not True:
            raise _refusal(envelope)
        tools = (envelope.get("data") or {}).get("tools") if isinstance(
            envelope.get("data"), Mapping) else None
        if not isinstance(tools, list) or not all(
            isinstance(t, Mapping) and isinstance(t.get("name"), str) and t.get("name")
            for t in tools
        ):
            raise AcliPortError("bad_output", "the engine's tool listing is unreadable")
        return [
            AcliTool(
                name=str(t["name"]), command=str(t.get("command") or ""),
                description=str(t.get("description") or ""), version=str(t.get("version") or ""),
            )
            for t in tools
        ]

    async def _invoke(
        self,
        runtime: AcliRuntime,
        method: str,
        path: str,
        *,
        timeout: float,
        body: dict[str, Any] | None = None,
        route_known: bool = False,
    ) -> Mapping[str, Any]:
        """The engine's envelope for a ``2xx``; every other answer raises.

        ``route_known`` is for reading back an operation the engine accepted:
        the endpoints exist there, so a ``404`` came from the hop and may pass.
        """
        path = _engine_path(runtime.engine, path)
        try:
            # Resolution can block on network I/O (BaaS connection info), so it
            # runs off the event loop, as the engine relay's does.
            context = await asyncio.to_thread(
                self._contexts.resolve,
                bot_id=runtime.bot_id, owner_id=runtime.owner_id, stage=runtime.stage.value,
            )
        except Exception as error:
            # No binding, an offline device, a stage with no live runtime, or no
            # connection info: in every case there is no container to run acli in.
            logger.warning(
                "acli_port_not_running bot_id=%s stage=%s path=%s error_type=%s",
                runtime.bot_id, runtime.stage, path, type(error).__name__,
            )
            raise AcliPortError("not_running", "the bot has no running container") from error
        try:
            response = await self._transport.invoke(
                context.conn_info, method, path, body=body, timeout=timeout,
            )
        except DeviceAdapterEndpointNotFoundError as error:
            envelope = _envelope_in(error)
            if envelope is not None and envelope.get("error") == "operation_not_found":
                raise _refusal(envelope) from error
            if route_known:
                # The engine accepted this operation, so its endpoints exist: the
                # 404 came from the hop (proxy, a recycled container).
                logger.warning(
                    "acli_port_unreachable bot_id=%s path=%s status=404", runtime.bot_id, path,
                )
                raise AcliPortError(
                    "engine_unreachable", "the engine could not be reached (HTTP 404)"
                ) from error
            # Any other 404 means the endpoint is not there: an engine build that
            # predates it, or an engine that does not implement it at all (one
            # serving its own API answers in its own 404 shape, not FastAPI's).
            # Nothing ran, and asking again will not change the answer.
            logger.warning(
                "acli_port_endpoint_missing bot_id=%s path=%s standard_route_missing=%s",
                runtime.bot_id, path, error.standard_route_missing,
            )
            raise AcliPortError(
                "engine_outdated",
                "the bot's engine does not provide acli endpoints; update the engine",
            ) from error
        except DeviceAdapterHTTPStatusError as error:
            if error.status_code == 501:
                raise AcliPortError(
                    "engine_unsupported", "the bot's engine does not support acli tools"
                ) from error
            if error.status_code == 400:
                raise AcliPortError("invalid_input", "the engine refused the request") from error
            envelope = _envelope_in(error)
            if error.status_code == 409 and envelope is not None:
                raise _refusal(envelope) from error
            raise AcliPortError(
                "engine_unreachable", f"the engine answered HTTP {error.status_code}"
            ) from error
        except DeviceAdapterTimeoutError as error:
            raise AcliPortError("timeout", "the engine did not answer in time") from error
        except Exception as error:
            logger.warning(
                "acli_port_unreachable bot_id=%s path=%s error_type=%s",
                runtime.bot_id, path, type(error).__name__,
            )
            raise AcliPortError("engine_unreachable", "the engine could not be reached") from error

        if not isinstance(response, Mapping):
            raise AcliPortError("engine_unreachable", "the engine answered with no envelope")
        return response


def _engine_path(engine: str, path: str) -> str:
    """``path`` (an adapter ``/api/cli/acli/*`` path) as ``engine`` serves it."""
    prefix = _ENGINE_PREFIXES.get(engine)
    if prefix is None:
        return path
    return prefix + path.removeprefix(_ACLI_PREFIX)


def _envelope_in(error: Exception) -> Mapping[str, Any] | None:
    """The JSON envelope in an error response's body, if it has one."""
    text = getattr(error, "response_text", None)
    if not isinstance(text, str):
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, Mapping) and "success" in value else None


def _parse_operation(data: Any) -> EngineOperation | None:
    if not isinstance(data, Mapping):
        return None
    op = data.get("operation")
    if not isinstance(op, Mapping):
        return None
    fields = {k: op.get(k) for k in ("id", "action", "name", "state")}
    if not all(isinstance(v, str) and v for v in fields.values()):
        return None
    try:
        action, state = AcliAction(fields["action"]), EngineState(fields["state"])
    except ValueError:
        # An action or state this contract does not name: unreadable.
        return None
    result, error = op.get("result"), op.get("error")
    return EngineOperation(
        id=fields["id"], action=action, name=fields["name"], state=state,
        result=result if isinstance(result, Mapping) else None,
        error=error if isinstance(error, Mapping) else None,
    )


def _operation_of(envelope: Mapping[str, Any]) -> EngineOperation:
    """The operation an envelope carries, ended in failure or not.

    An operation that failed is still an answer about the operation, so it is
    returned rather than raised; only an envelope with no operation is a
    refusal.
    """
    op = _parse_operation(envelope.get("data"))
    if op is not None:
        return op
    if envelope.get("success") is True:
        raise AcliPortError("bad_output", "the engine answered success with no operation")
    raise _refusal(envelope)


def _refusal(envelope: Mapping[str, Any]) -> AcliPortError:
    return AcliPortError(
        str(envelope.get("error") or "engine_refused"),
        str(envelope.get("message") or "the engine refused the request"),
        operation=_parse_operation(envelope.get("data")),
    )


__all__ = [
    "ArcaAcliPort",
    "INSTALL_PATH",
    "OPERATION_PATH",
    "RuntimeContextPort",
    "TOOLS_PATH",
    "UNINSTALL_PATH",
]
