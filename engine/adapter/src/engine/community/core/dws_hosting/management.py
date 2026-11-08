"""Owner commands and owned background tasks, isolated from interactive chat."""

import asyncio
import logging
import traceback
from typing import Protocol

from engine.community.core.dws_hosting.connection import (
    HostingAuthorization,
    verify_account,
)
from engine.community.core.dws_hosting.storage import durable_call
from engine.community.kernel.dws_connection import DwsError
from engine.community.kernel.dws_management import (
    EmptyCommand,
    EnabledUpdate,
    PageQuery,
    SettingsUpdate,
    TakeoverUpdate,
)
from engine.community.kernel.dws_runtime import Resolution
from engine.community.plugin_api.dws_management import HostingManagementStore


class HostingManagementService(Protocol):
    async def command(self, actor_id: str, operation: str, data: dict) -> dict: ...
    async def close(self) -> None: ...
    async def restore(self) -> None: ...


class HostingManagement:
    def __init__(
        self,
        runtime,
        authorization: HostingAuthorization,
        store: HostingManagementStore,
        *,
        clock_ms,
    ):
        self.runtime, self.authorization, self.store = runtime, authorization, store
        self.binding, self.clock_ms = runtime.binding, clock_ms
        self._lock = asyncio.Lock()
        self._worker = None
        self._login = None
        self._challenge = None
        self._login_status = "idle"
        self._error = ""

    def _running(self):
        return self._worker is not None and not self._worker.done()

    async def _run(self):
        try:
            await self.runtime.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # No subprocess output or message content in the public diagnostic.
            self._error = "runtime_stopped"
            # COSEC: log locations/type only, never exception text or local values.
            frames = traceback.extract_tb(exc.__traceback__)
            logging.getLogger(__name__).error(
                "DWS runtime stopped: type=%s frames=%s",
                type(exc).__name__,
                [(frame.name, frame.lineno) for frame in frames],
            )

    async def restore(self):
        """Called once by composition after binding/configuration validation."""
        async with self._lock:
            status = await self.runtime.status(self.binding.owner_id)
            if status.enabled and not self._running():
                self._worker = asyncio.create_task(self._run())

    async def _authorize(self, actor):
        async def challenge(value):
            self._challenge = value.model_dump()
            self._login_status = "pending"

        try:
            await self.authorization.authorize(actor, challenge)
            self._login_status = "authorized"
        except asyncio.CancelledError:
            self._login_status = "cancelled"
            raise
        except Exception:
            self._login_status = "failed"
        finally:
            self._challenge = None

    async def close(self):
        tasks = [t for t in (self._worker, self._login) if t is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def command(self, actor_id, operation, data):
        # COSEC: only the signature-verified human owner may manage this binding.
        if actor_id != self.binding.owner_id:
            raise PermissionError("owner_required")
        async with self._lock:
            binding_id = self.binding.binding_id
            if operation == "settings.get":
                EmptyCommand.model_validate(data)
                return await durable_call(self.store.get, self.binding)
            if operation == "settings.save":
                return await durable_call(
                    self.store.save, self.binding, SettingsUpdate.model_validate(data)
                )
            if operation == "status":
                EmptyCommand.model_validate(data)
                status = await self.runtime.status(actor_id)
                return {
                    **status.model_dump(),
                    "running": self._running(),
                    "error": self._error,
                    "authorization": self._login_status,
                    "challenge": self._challenge,
                }
            if operation == "authorization.check":
                EmptyCommand.model_validate(data)
                if self._running() or (self._login and not self._login.done()):
                    raise DwsError("connection_busy")
                status = await self.authorization.status(actor_id)
                self._login_status = (
                    "authorized" if status.authenticated else "required"
                )
                return {"authenticated": status.authenticated}
            if operation == "authorization.start":
                EmptyCommand.model_validate(data)
                if self._running():
                    raise DwsError("disable_before_authorizing")
                if self._login is None or self._login.done():
                    self._challenge = None
                    self._login_status = "starting"
                    self._login = asyncio.create_task(self._authorize(actor_id))
                return {"authorization": self._login_status}
            if operation == "enabled":
                update = EnabledUpdate.model_validate(data)
                if update.enabled:
                    if not await self.runtime.require_rollout():
                        raise DwsError("rollout_unavailable")
                    if self._login and not self._login.done():
                        raise DwsError("authorization_pending")
                    saved = await durable_call(self.store.get, self.binding)
                    if (
                        saved["version"] != update.expected_version
                        or not saved["settings"]["rules"].strip()
                    ):
                        raise ValueError("version_conflict")
                    if not self._running():
                        verify_account(
                            self.binding, await self.authorization.status(actor_id)
                        )
                await durable_call(
                    self.runtime.state.set_enabled,
                    binding_id,
                    update.enabled,
                    now_ms=self.clock_ms(),
                )
                if update.enabled and not self._running():
                    self._error = ""
                    self._worker = asyncio.create_task(self._run())
                elif not update.enabled:
                    await self.close()
                return {"enabled": update.enabled}
            if operation == "conversations":
                page = PageQuery.model_validate(data)
                return await durable_call(
                    self.store.conversations, self.binding, **page.model_dump()
                )
            if operation == "takeover":
                update = TakeoverUpdate.model_validate(data)
                await durable_call(
                    self.runtime.state.set_takeover,
                    binding_id,
                    update.conversation_id,
                    update.taken_over,
                    now_ms=self.clock_ms(),
                )
                return update.model_dump()
            if operation == "issues":
                page = PageQuery.model_validate(data)
                return {
                    "items": [
                        v.model_dump()
                        for v in await self.runtime.issues(
                            actor_id, **page.model_dump()
                        )
                    ]
                }
            if operation == "resolve":
                if self._running():
                    raise DwsError("disable_before_resolving")
                await self.runtime.resolve_manual(
                    actor_id, Resolution.model_validate(data)
                )
                return {"resolved": True}
            raise ValueError("unknown_operation")
