"""Relay ready session files to the selected Engine, without upload DB records."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any
from urllib.parse import quote

from injector import inject

from agentclaw.community.core.devices.services.device_context import (
    ConnInfoBuildError, DeviceNotBoundError, UnknownProviderError,
)
from agentclaw.community.core.devices.services.device_context_resolver import DeviceContextResolver
from agentclaw.community.core.engine_runtime.errors import (
    EngineDeviceNotReadyError, EngineResourceNotFoundError, EngineUpstreamError,
    EngineRuntimeError,
)
from agentclaw.community.core.resources.service import FileTooLargeError
from agentclaw.community.core.runtime_binding.models import RuntimeBindingRequest, RuntimeBindingTarget
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.runtime_binding.errors import RuntimeBindingResolutionError
from agentclaw.community.core.runtime_binding.service import RuntimeBindingResolutionService
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterHTTPStatusError,
    DeviceAdapterStreamResponse,
    DeviceAdapterTimeoutError,
    DeviceAdapterTransport,
)

log = get_logger()


class EngineSessionFilesService:
    @inject
    def __init__(
        self, runtime_bindings: RuntimeBindingResolutionService,
        resolver: DeviceContextResolver, transport: DeviceAdapterTransport,
        bot_repository: BotRepository,
    ) -> None:
        self._bindings = runtime_bindings
        self._resolver = resolver
        self._transport = transport
        self._bots = bot_repository

    def _connection(
        self, target: RuntimeBindingRequest, session_user_id: str | None = None,
    ) -> dict[str, Any]:
        if session_user_id is not None and session_user_id != target.actor_user_id:
            # COSEC: only an authorized owner may address another session subject.
            if target.actor_user_id != target.owner_id:
                raise EngineResourceNotFoundError("session file not found")
            bot = self._bots.get_by_id_and_owner(target.bot_id, target.owner_id)
            if not bot:
                raise EngineResourceNotFoundError("session file bot not found")
            if bot.get("bot_type") == "service" and bot.get("call_type") == "caller":
                target = replace(target, actor_user_id=session_user_id, target=RuntimeBindingTarget.CALLER_INSTANCE)
        try:
            binding = self._bindings.resolve(target)
            return self._resolver.resolve_for_binding(
                binding.binding_id, target.actor_user_id, bot_id=target.bot_id,
            ).conn_info
        except (RuntimeBindingResolutionError, ConnInfoBuildError, DeviceNotBoundError):
            raise EngineDeviceNotReadyError("session file binding unavailable") from None
        except UnknownProviderError:
            raise EngineUpstreamError("session file provider unavailable") from None

    @staticmethod
    def _path(resource_id: str = "") -> str:
        if not resource_id:
            return "/api/session-files"
        # COSEC: a resource identifier must remain one URL path segment.
        if resource_id in {".", ".."} or "/" in resource_id or "\\" in resource_id:
            raise EngineResourceNotFoundError("invalid session file resource")
        return f"/api/session-files/{quote(resource_id, safe='')}"

    async def _invoke(
        self, target: RuntimeBindingRequest, session_key: str,
        method: str, path: str,
        *, session_user_id: str | None = None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        log.info(
            "session_files.engine.request system=engine direction=out method=%s path=%s bot_id=%s owner_id=%s actor_user_id=%s stage=%s sessionKey=%s",
            method, path, target.bot_id, target.owner_id, target.actor_user_id,
            target.stage, session_key,
        )
        try:
            result = await self._transport.invoke(
                self._connection(target, session_user_id), method, path,
                params={"sessionKey": session_key}, timeout=60.0,
            )
            if method == "GET":
                result = {"files": self._list_view(result)}
            elif (
                result.get("deleted") is not True
                or not isinstance(result.get("resource_id"), str)
                or quote(result["resource_id"], safe="") != path.rsplit("/", 1)[-1]
            ):
                raise EngineUpstreamError("invalid session file deletion result")
        except Exception as exc:
            log.warning(
                "session_files.engine.failure system=engine method=%s path=%s sessionKey=%s error_type=%s status=%s elapsed_ms=%s",
                method, path, session_key, type(exc).__name__,
                getattr(exc, "status_code", None), int((time.monotonic() - started) * 1000),
            )
            if isinstance(exc, DeviceAdapterEndpointNotFoundError):
                raise EngineResourceNotFoundError("session file not found") from None
            if isinstance(exc, DeviceAdapterHTTPStatusError):
                raise EngineUpstreamError("session file operation failed") from None
            if isinstance(exc, TimeoutError):
                raise DeviceAdapterTimeoutError("session file request timed out") from None
            if isinstance(exc, EngineRuntimeError):
                raise
            # COSEC: public error logging prints tracebacks; exclude raw transport
            # exception chains, which can contain headers and signed URLs.
            raise EngineUpstreamError("session file transport unavailable") from None
        log.info(
            "session_files.engine.response system=engine method=%s path=%s sessionKey=%s result=success files=%s resource_id=%s deleted=%s elapsed_ms=%s",
            method, path, session_key, result.get("files"), result.get("resource_id"),
            result.get("deleted"), int((time.monotonic() - started) * 1000),
        )
        return result

    async def list_files(
        self, target: RuntimeBindingRequest, session_key: str,
        *, session_user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        result = await self._invoke(target, session_key, "GET", self._path(), session_user_id=session_user_id)
        return result["files"]

    @staticmethod
    def _list_view(result: dict[str, Any]) -> list[dict[str, Any]]:
        files = result.get("files")
        if not isinstance(files, list) or any(
            not isinstance(item, dict)
            or not isinstance(item.get("resource_id"), str)
            or not isinstance(item.get("display_name"), str)
            or item.get("availability") not in {"ready", "missing", "changed"}
            or type(item.get("size_bytes")) is not int
            or item["size_bytes"] < 0
            for item in files
        ):
            raise EngineUpstreamError("invalid session file list")
        # Only the declared Engine view crosses this boundary; never raw headers/URLs.
        return [
            {
                "resource_id": item["resource_id"],
                "display_name": item["display_name"],
                "status": item["availability"],
                "size_bytes": item["size_bytes"],
            }
            for item in files
        ]

    async def open_content(
        self, target: RuntimeBindingRequest, session_key: str,
        resource_id: str, disposition: str,
        *, session_user_id: str | None = None,
    ) -> DeviceAdapterStreamResponse:
        if disposition not in {"inline", "attachment"}:
            raise ValueError("invalid_disposition")
        path = self._path(resource_id) + "/content"
        started = time.monotonic()
        log.info(
            "session_files.engine.request system=engine direction=out method=GET path=%s sessionKey=%s bot_id=%s owner_id=%s actor_user_id=%s stage=%s disposition=%s",
            path, session_key, target.bot_id, target.owner_id, target.actor_user_id,
            target.stage, disposition,
        )
        status_code = None
        try:
            response = await self._transport.stream(
                self._connection(target, session_user_id), "GET", path,
                params={"sessionKey": session_key, "disposition": disposition},
                timeout=60.0,
            )
            status_code = response.status_code
            if response.status_code not in {200, 202}:
                await response.close()
                if response.status_code in {404, 409}:
                    raise EngineResourceNotFoundError("session file not found")
                if response.status_code == 413:
                    raise FileTooLargeError("File too large for preview")
                raise EngineUpstreamError("session file content unavailable")
        except Exception as exc:
            log.warning(
                "session_files.engine.failure system=engine method=GET path=%s sessionKey=%s status=%s error_type=%s elapsed_ms=%s",
                path, session_key, status_code, type(exc).__name__, int((time.monotonic() - started) * 1000),
            )
            if isinstance(exc, DeviceAdapterEndpointNotFoundError):
                raise EngineResourceNotFoundError("session file not found") from None
            if isinstance(exc, TimeoutError):
                raise DeviceAdapterTimeoutError("session file request timed out") from None
            if isinstance(exc, (EngineRuntimeError, FileTooLargeError)):
                raise
            raise EngineUpstreamError("session file transport unavailable") from None
        log.info(
            "session_files.engine.response system=engine method=GET path=%s sessionKey=%s status=%s elapsed_ms=%s",
            path, session_key, response.status_code, int((time.monotonic() - started) * 1000),
        )
        return response

    async def delete_file(
        self, target: RuntimeBindingRequest, session_key: str, resource_id: str,
    ) -> None:
        await self._invoke(target, session_key, "DELETE", self._path(resource_id))
