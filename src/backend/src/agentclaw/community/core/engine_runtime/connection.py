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

from agentclaw.community.core.bot_collaborator.repository.protocol import (
    CollaboratorRepositoryProtocol,
)
from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.devices.errors import (
    DeviceDomainError,
    DeviceServiceError,
)
from agentclaw.community.core.devices.repository.protocol import (
    DeviceBindingRepository,
)
from agentclaw.community.core.devices.models import OperatorContext
from agentclaw.community.core.devices.services.device_service import DeviceService
from agentclaw.community.core.engine_runtime.errors import (
    EngineBotTypeNotSupportedError,
    EngineDeviceNotReadyError,
    EngineUpstreamError,
)
from agentclaw.community.core.engine_runtime.models import ConnectionResult, SocketInfo
from agentclaw.community.core.engine_runtime.sharing import bot_is_shared
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.sandbox_runtime import (
    SandboxRuntimeClient,
    SandboxRuntimeUnavailableError,
)

logger = get_logger()

#: Lifetime requested for the proxy credential, mirroring the internal WS token.
CONNECTION_TTL_SECONDS = 120 * 60

#: Header the proxy gateway authenticates with.
_PROXY_TOKEN_HEADER = "x-proxypass-token"

#: Connection mode requested from the device provider. See ``build``.
_RELAY_MODE = "relay"

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


#: The only bot type a socket is published for — the same rule, and the same
#: reason, as the sessions group's gate. Necessary but not sufficient; the bot
#: must also be unshared. See :func:`_require_private_personal_bot`.
_SUPPORTED_BOT_TYPE = "personal"


def _require_private_personal_bot(
    bot_type: str, bot_id: str, *, is_shared: bool
) -> None:
    """Reject bots more than one caller reaches, before a socket is composed.

    The socket this endpoint publishes is **not** chat-scoped, however it is
    labelled. The engine's WebSocket server answers ``hello`` by advertising
    ``sessions.list``, ``sessions.patch``, ``sessions.delete``,
    ``sessions.reset`` and the ``exec.approvals`` methods, grants
    ``operator.admin``, and forwards any method it does not handle itself to
    the active engine's relay plugin. A caller holding the returned credential
    therefore has an operator channel, not a chat channel.

    On a ``service`` bot that is the same exposure the sessions group already
    refuses. The engine has no tenant axis and its session list is not scoped
    per caller, so one device holds every caller's sessions; the sessions group
    answers 501 rather than let the bot's owner enumerate them. Publishing this
    socket for the same bot would hand the same owner the same data over a
    different transport — a 501 on the front door with the window left open.

    ``is_shared`` closes the same window on a ``personal`` bot that is public
    or has collaborators: those are multi-caller too, and this socket's
    ``sessions.*`` methods reach every caller's conversations on the device.
    See :func:`~agentclaw.community.core.engine_runtime.sharing.bot_is_shared`
    — one predicate, so this gate and the sessions gate cannot drift apart.

    Gated here rather than in the router because the rule is about what may be
    *composed*, not about how it is served: any future caller of ``build`` is
    covered without repeating the check.
    """
    if bot_type != _SUPPORTED_BOT_TYPE:
        raise EngineBotTypeNotSupportedError(
            f"connections are not served for bot_type={bot_type!r}"
        )
    if is_shared:
        raise EngineBotTypeNotSupportedError(
            "connections are not served for a shared bot: the socket grants "
            "operator.admin over every caller's sessions on the device"
        )


class EngineConnectionService:
    """Compose the sockets a caller may open against their bot."""

    @inject
    def __init__(
        self,
        bot_service: BotService,
        binding_repository: DeviceBindingRepository,
        device_service: DeviceService,
        sandbox_client: SandboxRuntimeClient,
        collaborator_repo: CollaboratorRepositoryProtocol,
    ) -> None:
        self._bot_service = bot_service
        self._binding_repository = binding_repository
        self._device_service = device_service
        self._sandbox_client = sandbox_client
        self._collaborator_repo = collaborator_repo

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
        _require_private_personal_bot(
            str(bot.get("bot_type") or ""),
            bot_id,
            is_shared=bot_is_shared(
                bot,
                self._collaborator_repo,
                bot_id=str(bot.get("bot_id") or bot_id),
                owner_id=str(bot.get("owner_id") or owner_id),
            ),
        )
        engine = str(bot.get("active_engine") or "")

        binding_id = self._active_binding_id(bot_id, owner_id)

        operator = OperatorContext(
            staff_id=owner_id,
            staff=owner_id,
            nick_name=owner_id,
            operator_name=owner_id,
        )
        chat_path = self._chat_path(engine)
        try:
            info = self._get_connection(binding_id, operator, chat_path)
        except DeviceServiceError as exc:
            # The provider itself failed — a BaaS ws-info call that timed out or
            # answered an error. The bot's device may be perfectly healthy, so
            # this is an upstream fault, not "your device is not ready".
            raise EngineUpstreamError(
                f"device provider failed for bot={bot_id}"
            ) from exc
        except DeviceDomainError as exc:
            # The device is in no state to serve a connection: no binding, a
            # failed one, or one the operator cannot reach. Same class of answer
            # as an unresolvable device above — retry later.
            raise EngineDeviceNotReadyError(
                f"device not connectable for bot={bot_id}"
            ) from exc

        if not getattr(info, "available", True):
            raise EngineDeviceNotReadyError(f"device unavailable for bot={bot_id}")

        # The *WebSocket* credential, not `token`. On the local provider's
        # healthy path `token` is http-info's while the address is built from
        # ws-info's target — a mismatched pair. Providers that issue no separate
        # ws credential leave it empty and `token` is right there.
        token = getattr(info, "ws_token", "") or getattr(info, "token", "") or ""
        headers = {_PROXY_TOKEN_HEADER: token} if token else {}

        sockets = [
            SocketInfo(
                kind="chat", url=self._socket_url(info, chat_path), headers=headers
            )
        ]
        return ConnectionResult(
            engine=engine, expires_at=self._expires_at(info), sockets=sockets
        )

    def _active_binding_id(self, bot_id: str, owner_id: str) -> int:
        """The bot's active binding id — and *only* the id.

        Deliberately not ``DeviceContextResolver.resolve_for_bot``. That builds
        full connection info, which on the BaaS path means a blocking
        ``get_ws_info`` call with a 30-second timeout — and every field of it
        was then discarded here except ``binding_id``, because
        ``_get_connection`` immediately performs its own ``get_ws_info`` in
        relay mode to obtain the URL and credential this endpoint publishes.
        Every successful connection request therefore made two provider calls
        for one answer, and a slow pair could burn nearly two 30-second
        timeouts before either could fail.

        The relay's rule that device resolution goes through the resolver is
        about *choosing a provider and building conn info*, which this endpoint
        does not do — the second lookup is the one that supplies the response,
        and it takes a binding id. So this reads the binding directly and lets
        the relay-mode lookup remain the only provider call.

        Owner-scoped for the same reason ``resolve_for_bot`` was: the query is
        ``(bot_id, owner_id)``, so another owner's binding cannot be returned
        even though ownership was already established above.
        """
        binding = self._binding_repository.get_active_by_bot_and_owner(
            bot_id, owner_id
        )
        if binding is None:
            raise EngineDeviceNotReadyError(f"device not ready for bot={bot_id}")
        return binding.id

    def _get_connection(
        self, binding_id: int, operator: OperatorContext, chat_path: str
    ) -> object:
        """Ask the provider for this device's connection."""
        return self._device_service.get_device_connection(
            binding_id=binding_id,
            operator=operator,
            ttl=CONNECTION_TTL_SECONDS,
            # Relay is the mode that yields a *finished* WebSocket URL, which is
            # this endpoint's whole contract — callers concatenate nothing. The
            # BaaS provider fills `url` only when relay is asked for; leaving the
            # mode unset makes it hand back a bare routing target instead, and
            # the community build has no proxy gateway to turn that target into
            # a URL. Asking once also keeps the URL and the token describing the
            # same mode.
            ws_conn_mode=_RELAY_MODE,
            # The provider bakes this path *into* the relay URL, so it has to be
            # the bot's own engine path. The provider default is openclaw's, and
            # the engine closes a socket whose pinned engine is not the active
            # one (code 4001) — a claude_code bot handed the default would be
            # rejected on connect.
            path=chat_path,
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

    def _socket_url(self, info: object, socket_path: str) -> str:
        """The finished URL for ``socket_path``.

        Two shapes:

        1. **The provider returned a WebSocket URL** (BaaS relay mode) — it is
           already complete, built server-side *around the path we asked for*.
           Used verbatim. Appending ``socket_path`` again would produce
           ``…/api/openclaw/ws/api/openclaw/ws``, which cannot connect.
        2. **The provider returned a routing target** — we compose, so the path
           is appended here.
        """
        url = str(getattr(info, "url", "") or "")
        if url.startswith(("ws://", "wss://")):
            return url

        return self._ws_base(info) + socket_path

    def _ws_base(self, info: object) -> str:
        """URL prefix a socket path is appended to, for providers that route.

        Two shapes:

        1. A local device — reach it directly.
        2. Otherwise, the proxy gateway: ``{base}/proxypass/{target}``.
        """
        target = str(getattr(info, "target", "") or "")
        if not target:
            raise EngineUpstreamError("device connection carries no routing target")

        if str(getattr(info, "type", "") or "") == "local":
            return f"ws://{target}"

        try:
            base = self._sandbox_client.proxy_base_url() or ""
        except SandboxRuntimeUnavailableError as exc:
            # Deployments without a proxy gateway (the community build) raise
            # here rather than returning "". Letting it out would be a 500 on a
            # condition we can name: there is no way to reach this device.
            raise EngineUpstreamError("no proxy gateway in this deployment") from exc
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

        Read from the ``ws_*`` pair for the same reason the token is: on the
        local provider's healthy path ``expires_at`` describes the http-info
        token, which is not the credential published here.

        Falling back to the computed value covers the paths that report nothing
        (a provider that issues no ws credential and whose HTTP token carries no
        stated expiry): a bound of the right order beats omitting a field the
        contract makes mandatory.
        """
        reported = str(
            getattr(info, "ws_expires_at", "")
            or getattr(info, "expires_at", "")
            or ""
        )
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
