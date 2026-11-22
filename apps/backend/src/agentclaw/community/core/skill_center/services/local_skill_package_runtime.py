"""Core orchestration for Local Skill package delivery to Engine runtimes."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass

from agentclaw.community.core.devices.services.device_context import DeviceContext
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.skill_center.errors import (
    LocalSkillEditBusyError,
    LocalSkillEditLockUnavailableError,
    LocalSkillInvalidPackageError,
    LocalSkillPackageOutcomeUnknownError,
    LocalSkillRuntimeUnavailableError,
    LocalSkillTooLargeError,
)
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterHTTPStatusError,
    DeviceAdapterTransport,
)

_CAPABILITY = "skills.local_package.apply.v1"
_PARALLEL_TARGETS = "skills.local_package.parallel_targets.v1"
_STANDARD_PATH = "/api/skills/local/apply"
_TECLAW_PATH = "/api/v1/file/skill-package"


@dataclass(frozen=True, slots=True)
class LocalSkillPackageRuntimeResult:
    skill_name: str
    action: str
    content_digest: str
    target_path: str | None = None


@dataclass(frozen=True, slots=True)
class LocalSkillPackageDestination:
    context: DeviceContext
    supports_package_apply: bool
    supports_parallel_targets: bool

    @property
    def is_teclaw(self) -> bool:
        return self.context.provider == "teclaw"



class LocalSkillPackageRuntime:
    """Resolve a bot and translate package apply to its runtime transport."""

    def __init__(
        self,
        resolver: DeviceContextResolver,
        transport: DeviceAdapterTransport,
    ) -> None:
        self._resolver = resolver
        self._transport = transport

    async def apply(
        self,
        *,
        bot_id: str,
        owner_id: str,
        skill_name: str,
        layout: str,
        package: bytes,
    ) -> LocalSkillPackageRuntimeResult | None:
        destination = await self.destination(bot_id=bot_id, owner_id=owner_id)
        if not destination.supports_package_apply:
            return None
        return await self.publish(
            destination.context, bot_id=bot_id, skill_name=skill_name,
            layout=layout, package=package,
        )

    async def resolve(self, *, bot_id: str, owner_id: str) -> DeviceContext:
        try:
            return await asyncio.to_thread(
                self._resolver.resolve_for_bot, bot_id, owner_id
            )
        except Exception as exc:
            raise LocalSkillRuntimeUnavailableError() from exc

    async def destination(self, *, bot_id: str, owner_id: str) -> LocalSkillPackageDestination:
        context = await self.resolve(bot_id=bot_id, owner_id=owner_id)
        if context.provider == "teclaw":
            return LocalSkillPackageDestination(context, True, False)
        capabilities = await self._capabilities(context.conn_info)
        whole_package = _CAPABILITY in capabilities
        return LocalSkillPackageDestination(
            context, whole_package, whole_package and _PARALLEL_TARGETS in capabilities
        )

    async def publish(
        self, context: DeviceContext, *, bot_id: str, skill_name: str,
        layout: str, package: bytes,
    ) -> LocalSkillPackageRuntimeResult:
        is_teclaw = context.provider == "teclaw"
        path = _TECLAW_PATH if is_teclaw else _STANDARD_PATH
        data = (
            {"skills_name": skill_name}
            if is_teclaw
            else {"skill_name": skill_name, "layout": layout}
        )
        headers = {"x-target-bot-id": bot_id} if is_teclaw else None
        try:
            raw = await self._transport.invoke_multipart(
                context.conn_info,
                path,
                files={"file": (f"{skill_name}.zip", package, "application/zip")},
                data=data,
                headers=headers,
            )
        except DeviceAdapterHTTPStatusError as exc:
            self._raise_known_failure(exc)
            raise LocalSkillPackageOutcomeUnknownError() from exc
        except Exception as exc:
            # The request may already have committed. Never retry through the
            # legacy per-file protocol after this point.
            raise LocalSkillPackageOutcomeUnknownError() from exc
        return self._normalise_result(
            raw,
            skill_name=skill_name,
            expected_digest="sha256:" + hashlib.sha256(package).hexdigest(),
            is_teclaw=is_teclaw,
        )

    async def _capabilities(self, conn_info: dict) -> frozenset[str]:
        try:
            raw = await self._transport.invoke(
                conn_info,
                "GET",
                "/api/engine/capabilities",
            )
        except DeviceAdapterEndpointNotFoundError as exc:
            if not exc.standard_route_missing:
                raise LocalSkillRuntimeUnavailableError() from exc
            try:
                health = await self._transport.invoke(conn_info, "GET", "/health")
            except Exception as health_exc:
                raise LocalSkillRuntimeUnavailableError() from health_exc
            if not isinstance(health, dict) or not (
                health.get("status") == "ok" or health.get("ok") is True
            ):
                raise LocalSkillRuntimeUnavailableError()
            return frozenset()
        except Exception as exc:
            raise LocalSkillRuntimeUnavailableError() from exc
        if not isinstance(raw, dict) or raw.get("success") is not True:
            raise LocalSkillRuntimeUnavailableError()
        data = raw.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("supported"), list):
            raise LocalSkillRuntimeUnavailableError()
        return frozenset(item for item in data["supported"] if isinstance(item, str))

    @staticmethod
    def _raise_known_failure(exc: DeviceAdapterHTTPStatusError) -> None:
        try:
            payload = json.loads(exc.response_text)
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        error = payload.get("error") or payload.get("error_code")
        if error is None and isinstance(payload.get("detail"), dict):
            error = payload["detail"].get("error") or payload["detail"].get(
                "error_code"
            )
        if exc.status_code == 413:
            raise LocalSkillTooLargeError() from exc
        if exc.status_code == 400:
            raise LocalSkillInvalidPackageError() from exc
        if exc.status_code == 409 and error == "publish_in_progress":
            raise LocalSkillEditBusyError() from exc
        if exc.status_code == 503 and error == "publish_lock_unavailable":
            raise LocalSkillEditLockUnavailableError() from exc

    @staticmethod
    def _normalise_result(
        raw: object,
        *,
        skill_name: str,
        expected_digest: str,
        is_teclaw: bool,
    ) -> LocalSkillPackageRuntimeResult:
        if not isinstance(raw, dict) or raw.get("success") is not True:
            raise LocalSkillPackageOutcomeUnknownError()
        payload = raw if is_teclaw else raw.get("data")
        if not isinstance(payload, dict):
            raise LocalSkillPackageOutcomeUnknownError()
        digest = payload.get("sha256") if is_teclaw else payload.get("content_digest")
        if is_teclaw and isinstance(digest, str):
            digest = f"sha256:{digest.lower()}"
        action = payload.get("action")
        if (
            payload.get("skill_name") != skill_name
            or action not in {"created", "replaced", "unchanged"}
            or digest != expected_digest
        ):
            raise LocalSkillPackageOutcomeUnknownError()
        target_path = payload.get("target_path")
        return LocalSkillPackageRuntimeResult(
            skill_name=skill_name,
            action=str(action),
            content_digest=expected_digest,
            target_path=str(target_path) if isinstance(target_path, str) else None,
        )


__all__ = [
    "LocalSkillPackageDestination", "LocalSkillPackageRuntime", "LocalSkillPackageRuntimeResult",
]
