"""Validate Caller-owned sessions on the same runtime that receives the overlay."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid

from agentclaw.community.core.caller_identity.boundary_logging import (
    install_httpx_credential_filter,
    redact_boundary,
)
from agentclaw.community.core.caller_identity.contracts import (
    CallerIdentityPermissionError,
)
from agentclaw.community.core.caller_identity.credential import (
    CALLER_CREDENTIAL_REQUEST_INVALID,
    CALLER_OUTBOUND_UPDATE_FAILED,
    CallerCredentialError,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.repository.protocols.devices import (
    DeviceBindingRepository,
)
from agentclaw.community.core.runtime_binding.models import (
    RuntimeBindingRequest,
    RuntimeBindingTarget,
)
from agentclaw.community.core.runtime_binding.service import (
    RuntimeBindingResolutionService,
)
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterTransport,
)
from agentclaw.community.log import get_logger

logger = get_logger()


class CallerSessionAuthorizer:
    """Use the existing session listing authority; never register caller keys."""

    def __init__(
        self,
        *,
        binding_repository: DeviceBindingRepository,
        resolver: DeviceContextResolver,
        transport: DeviceAdapterTransport,
        runtime_bindings: RuntimeBindingResolutionService,
    ) -> None:
        self._bindings = binding_repository
        self._resolver = resolver
        self._transport = transport
        self._runtime_bindings = runtime_bindings

    def authorize(
        self,
        *,
        bot_id: str,
        owner_id: str,
        caller_user_id: str,
        binding_id: int | None,
        stage: str,
        session_key: str | None,
    ) -> bool:
        if binding_id is None:
            binding_id = self._runtime_bindings.resolve(
                RuntimeBindingRequest(
                    bot_id=bot_id,
                    owner_id=owner_id,
                    actor_user_id=caller_user_id,
                    stage=stage,
                    target=RuntimeBindingTarget.CALLER_SERVICE,
                )
            ).binding_id
        binding = self._bindings.get_by_id(binding_id)
        if binding is None or binding.device_provider != "teclaw":
            return False
        # COSEC: preserve original bytes after nonempty validation; never invent a key.
        if session_key is None or not session_key.strip():
            logger.warning(
                "caller_session_key_rejected bot_id=%s binding_id=%s reason=missing_session_key",
                bot_id,
                binding_id,
            )
            raise CallerCredentialError(CALLER_CREDENTIAL_REQUEST_INVALID)
        install_httpx_credential_filter()
        params = {
            "session_key": session_key,
            "user_id": caller_user_id,
            "source": "all_but_others",
            "agent_id": bot_id,
            "limit": 1,
            "offset": 0,
        }
        fields = {
            "system": "engine",
            "direction": "outbound",
            "operation": "caller_session_lookup",
            "operation_id": uuid.uuid4().hex,
            "method": "GET",
            "route": "/api/sessions",
            "bot_id": bot_id,
            "owner_id": owner_id,
            "caller_user_id": caller_user_id,
            "binding_id": binding_id,
            "stage": stage,
            "params": redact_boundary(params),
        }
        started_at = time.monotonic()
        logger.info("caller_session_lookup_started fields=%s", fields)
        response = None
        try:
            context = self._resolver.resolve_for_binding(
                binding_id, caller_user_id, bot_id=bot_id
            )
            response = asyncio.run(
                self._transport.invoke(
                    context.conn_info,
                    "GET",
                    "/api/sessions",
                    params=params,
                    timeout=30.0,
                )
            )
            if response.get("success") is False:
                raise CallerCredentialError(CALLER_OUTBOUND_UPDATE_FAILED)
            items = response.get("data")
            if isinstance(items, dict):
                items = items.get("items")
            # COSEC: verify the runtime response as well as the upstream filters.
            authorized = isinstance(items, list) and any(
                isinstance(item, dict)
                and item.get("id") == session_key
                and str(item.get("user_id") or "") == caller_user_id
                and item.get("agent_id") == bot_id
                for item in items
            )
            if not authorized:
                raise CallerIdentityPermissionError()
        except Exception as exc:
            error_body = getattr(exc, "response_text", None)
            if response is None and isinstance(error_body, str):
                try:
                    response = json.loads(error_body)
                except ValueError:
                    response = {
                        "response_type": "non_json",
                        "length": len(error_body),
                        "sha256": hashlib.sha256(error_body.encode()).hexdigest(),
                    }
            logger.warning(
                "caller_session_lookup_failed fields=%s duration_ms=%.1f status_code=%s error_type=%s error_message=session_lookup_failed response=%s",
                fields,
                (time.monotonic() - started_at) * 1000,
                getattr(exc, "status_code", None),
                type(exc).__name__,
                redact_boundary(
                    response, session_response=True, secrets=(session_key,)
                ),
            )
            if isinstance(exc, (CallerIdentityPermissionError, CallerCredentialError)):
                raise
            raise CallerCredentialError(CALLER_OUTBOUND_UPDATE_FAILED) from None
        logger.info(
            "caller_session_lookup_succeeded fields=%s duration_ms=%.1f status=success business_success=%s business_code=%s response=%s",
            fields,
            (time.monotonic() - started_at) * 1000,
            response.get("success"),
            response.get("code"),
            redact_boundary(response, session_response=True, secrets=(session_key,)),
        )
        return True
