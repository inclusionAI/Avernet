"""EngineConnectionService — the public replacement for the connection hand-off.

The internal console asks for a device connection and composes
``/proxypass/{target}{path}`` itself. That is fine between our own frontend and
our own backend, and wrong to hand an external tenant: it publishes routing
topology and a raw device credential, both of which become things we cannot
change without breaking integrators.

This service returns **finished** socket URLs instead. Nothing in
:class:`ConnectionResult` exposes a target, a connection type, or a bare token.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from injector import inject

from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.devices.services.device_context import (
    ConnInfoBuildError,
    DeviceNotBoundError,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.devices.models import OperatorContext
from agentclaw.community.core.devices.services.device_service import DeviceService
from agentclaw.community.core.engine_runtime.errors import (
    EngineDeviceNotReadyError,
    EngineUpstreamError,
)
from agentclaw.community.core.engine_runtime.models import ConnectionResult, SocketInfo
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.sandbox_runtime import SandboxRuntimeClient

logger = get_logger()

#: Lifetime requested for the proxy credential, mirroring the internal WS token.
CONNECTION_TTL_SECONDS = 120 * 60

#: Header the proxy gateway authenticates with.
_PROXY_TOKEN_HEADER = "x-proxypass-token"

#: Engines with a dedicated chat socket. Anything else uses the engine-generic
#: route the adapter also serves (``/api/{engine}/ws``).
_CHAT_WS_PATHS = {
    "openclaw": "/api/openclaw/ws",
    "claude_code": "/api/claude_code/ws",
}

# No terminal socket. The engine serves an interactive PTY and openclaw
# declares the capability, but the spec excludes an interactive shell on a
# tenant's device from v1 at any scope. (It would not have worked as published
# either: the engine's terminal route authenticates a `token` *query* parameter,
# which a header-only connection does not supply.)


class EngineConnectionService:
    """Compose the sockets a caller may open against their bot."""

    @inject
    def __init__(
        self,
        bot_service: BotService,
        resolver: DeviceContextResolver,
        device_service: DeviceService,
        sandbox_client: SandboxRuntimeClient,
    ) -> None:
        self._bot_service = bot_service
        self._resolver = resolver
        self._device_service = device_service
        self._sandbox_client = sandbox_client

    def build(self, *, bot_id: str, owner_id: str) -> ConnectionResult:
        """Return the bot's usable sockets.

        ``owner_id`` must be the authenticated principal: the bot is resolved
        owner-scoped here and the resolved owner is passed on as the operator.
        That matters because ``DeviceService.get_device_connection`` applies a
        *wider* permission model of its own — public bots and collaborators —
        and this surface is owner-only. Resolving first means the wider check
        can never widen it.
        """
        bot = self._bot_service.get_bot(bot_id, owner_id)
        engine = str(bot.get("active_engine") or "")

        try:
            ctx = self._resolver.resolve_for_bot(bot_id, owner_id)
        except (DeviceNotBoundError, ConnInfoBuildError) as exc:
            raise EngineDeviceNotReadyError(f"device not ready for bot={bot_id}") from exc

        operator = OperatorContext(
            staff_id=owner_id,
            staff=owner_id,
            nick_name=owner_id,
            operator_name=owner_id,
        )
        info = self._device_service.get_device_connection(
            binding_id=ctx.binding_id,
            operator=operator,
            ttl=CONNECTION_TTL_SECONDS,
        )
        if not getattr(info, "available", True):
            raise EngineDeviceNotReadyError(f"device unavailable for bot={bot_id}")

        base = self._ws_base(info)
        token = getattr(info, "token", "") or ""
        headers = {_PROXY_TOKEN_HEADER: token} if token else {}

        sockets = [
            SocketInfo(kind="chat", url=base + self._chat_path(engine), headers=headers)
        ]
        return ConnectionResult(
            engine=engine, expires_at=self._expires_at(info), sockets=sockets
        )

    # ── composition ───────────────────────────────────────────────────────

    def _chat_path(self, engine: str) -> str:
        """Chat socket path for ``engine``.

        Falls back to the adapter's engine-generic route rather than guessing a
        dedicated one, so a newly-added engine is reachable without a code
        change here.
        """
        if engine in _CHAT_WS_PATHS:
            return _CHAT_WS_PATHS[engine]
        return f"/api/{engine}/ws" if engine else "/ws"

    def _ws_base(self, info: object) -> str:
        """URL prefix that a socket path is appended to.

        Three shapes, in order of preference:

        1. The provider already returned a WebSocket URL (BaaS relay mode) —
           use its origin and routing verbatim rather than rebuilding it.
        2. A local device — reach it directly.
        3. Otherwise, the proxy gateway: ``{base}/proxypass/{target}``.
        """
        url = str(getattr(info, "url", "") or "")
        if url.startswith(("ws://", "wss://")):
            return url.rstrip("/")

        target = str(getattr(info, "target", "") or "")
        if not target:
            raise EngineUpstreamError("device connection carries no routing target")

        if str(getattr(info, "type", "") or "") == "local":
            return f"ws://{target}"

        base = self._sandbox_client.proxy_base_url() or ""
        if not base:
            raise EngineUpstreamError("no proxy base url configured")
        base = base.rstrip("/").replace("https://", "wss://").replace("http://", "ws://")
        return f"{base}/proxypass/{target}"

    def _expires_at(self, info: object) -> str:
        """When the credential above stops working.

        The issuer's own value wins whenever it gives one. The TTL this service
        requests is advisory — the BaaS path documents that it ignores it and
        decides server-side — so a locally computed expiry there is a guess that
        disagrees with the real token, and a caller that trusts it either
        re-fetches early or keeps using a dead credential.

        Falling back to the computed value covers the paths that report nothing
        (a local device whose HTTP token carries no stated expiry): a bound of
        the right order beats omitting a field the contract makes mandatory.
        """
        reported = str(getattr(info, "expires_at", "") or "")
        if reported:
            normalised = self._as_utc_iso(reported)
            if normalised:
                return normalised
            # Unparseable — publishing it verbatim would break the ISO 8601
            # contract, so fall through to the computed bound instead.
            logger.warning(
                "[engine_runtime] provider expires_at is not ISO 8601: %r", reported
            )
        return (
            datetime.now(timezone.utc) + timedelta(seconds=CONNECTION_TTL_SECONDS)
        ).isoformat()

    @staticmethod
    def _as_utc_iso(value: str) -> str:
        """``value`` as a UTC ISO 8601 instant, or ``""`` if it is not one.

        Providers spell UTC with a trailing ``Z``; the computed branch spells it
        ``+00:00``. Normalising means one shape on the wire regardless of which
        branch produced it. A naive timestamp is read as UTC — the field is
        documented as UTC and every producer here emits UTC.
        """
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return ""
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()


__all__ = ["CONNECTION_TTL_SECONDS", "EngineConnectionService"]
