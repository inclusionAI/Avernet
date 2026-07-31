"""EngineConnectionService — the public replacement for the connection hand-off.

The internal console asks for a device connection and composes
``/proxypass/{target}{path}`` itself. That is fine between our own frontend and
our own backend, and wrong to hand an external tenant: it publishes routing
topology and a raw device credential, both of which become things we cannot
change without breaking integrators.

This service returns **finished** socket URLs instead, addressed to the public
gateway under ``/engine``. The target and the credential do travel *inside* that
URL — a browser's WebSocket handshake can carry a credential nowhere else — but
no field beside it hands a caller the pieces to assemble a different one, and
nothing names the hop behind the gateway.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit

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
from agentclaw.community.di.config import GatewayConfig
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()

#: Lifetime requested for the proxy credential, mirroring the internal WS token.
CONNECTION_TTL_SECONDS = 120 * 60

#: Query parameter the proxy authenticates with. A *parameter* and not a header
#: because the caller is a browser: ``new WebSocket(url, protocols)`` takes no
#: headers, so a URL is the only place a credential can ride. The internal
#: console already opens this socket the same way.
_PROXY_TOKEN_PARAM = "x-proxypass-token"

#: Path prefix the gateway routes on, directly after the host.
_ENGINE_PREFIX = "/engine"

#: The prefix the hop behind the gateway serves. Only used to recognise a
#: provider URL we can re-address — see :meth:`_reject_unroutable_provider_url`.
_PROXYPASS_PREFIX = "/proxypass/"

#: WebSocket scheme for each scheme a gateway base url may be configured with.
#: ``ws``/``wss`` are accepted so a deployment that spells the base out as a
#: socket origin is not rejected for being explicit.
_WS_SCHEMES = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}

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
# tenant's device from v1 at any scope.


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
        gateway_config: GatewayConfig,
        collaborator_repo: CollaboratorRepositoryProtocol,
    ) -> None:
        self._bot_service = bot_service
        self._binding_repository = binding_repository
        self._device_service = device_service
        self._gateway_config = gateway_config
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

        sockets = [
            SocketInfo(kind="chat", url=self._socket_url(info, chat_path, token))
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
            # Relay is the mode whose credential is scoped to a relayed socket,
            # and asking for one mode keeps the credential and the address we
            # publish describing the same one. We no longer consume the URL relay
            # mode returns — `_socket_url` recomposes against the gateway — but
            # the mode still selects what the provider issues, so it is not
            # vestigial.
            ws_conn_mode=_RELAY_MODE,
            # The provider builds its relay around this path, so it has to be the
            # bot's own engine path: the provider default is openclaw's, and the
            # engine closes a socket whose pinned engine is not the active one
            # (code 4001). We publish our own path rather than the provider's
            # URL, so this no longer decides what a caller connects to — it
            # decides what the provider relays. The production provider is not in
            # this repository, so this stays as it was rather than being trimmed
            # on the assumption that only the discarded URL depended on it.
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

    def _socket_url(self, info: object, socket_path: str, token: str) -> str:
        """The finished URL for ``socket_path``.

        Composed here rather than passed through from the provider. The provider
        addresses the hop *behind* the gateway, which is the topology this
        surface exists not to publish; and we already hold every part the URL
        needs — the target, the path we asked for, and the credential.

        Two shapes:

        1. A local device — reached directly, with no credential. The gateway
           routes to the hop behind it, which has no path to a device on the
           caller's own machine, and there is no proxy to authenticate to. Its
           ``url`` is not consulted at all, so the relay-shape guard below does
           not apply: the local provider fills that field from *http*-info
           (``local_device_service.py``), which was never a relay URL and was
           never published here either.
        2. Otherwise the gateway: ``{base}/engine/{target}{path}``, credential
           in the query.
        """
        target = str(getattr(info, "target", "") or "")
        if not target:
            raise EngineUpstreamError("device connection carries no routing target")

        if str(getattr(info, "type", "") or "") == "local":
            return f"ws://{target}{socket_path}"

        self._reject_unroutable_provider_url(info)

        # ``@`` and ``:`` stay raw — both are legal in a path segment and the hop
        # behind the gateway matches the target verbatim. Everything else is
        # escaped: a ``?`` or ``#`` in a target would otherwise end the path and
        # silently strip the credential off the URL we publish.
        base = self._gateway_ws_base()
        url = f"{base}{_ENGINE_PREFIX}/{quote(target, safe='@:')}{socket_path}"
        # Only when there is one. An empty ``?x-proxypass-token=`` would fail the
        # handshake with nothing to diagnose from, so absent is published as
        # absent — exactly as it was when this was a header.
        if token:
            url = f"{url}?{_PROXY_TOKEN_PARAM}={quote(token, safe='')}"
        return url

    def _reject_unroutable_provider_url(self, info: object) -> None:
        """Refuse a provider URL the gateway prefix cannot express.

        The gateway rewrites ``/engine/{rest}`` onto the hop behind it, so the
        only provider shape this endpoint can re-address is that hop's own
        ``/proxypass/{target}{path}``. A provider that relays some other way —
        BaaS's LOCAL platform answers ``/wsrelay/{session_id}`` and ignores the
        path entirely — cannot be rebuilt from a target and a path.

        Such a bot cannot reach this endpoint, so this never fires. It exists so
        that if that ever stops being true, it surfaces as a named server-side
        error instead of a tenant reporting a socket that will not open. Falling
        back to the provider's own URL is not the alternative: that publishes the
        hop behind the gateway, which is the one thing this surface must not do.
        """
        url = str(getattr(info, "url", "") or "")
        if not url:
            return
        try:
            path = urlsplit(url).path
        except ValueError:
            # A malformed url is no more re-addressable than a well-formed one of
            # the wrong shape, and letting the parse error out would be the 500
            # this guard exists to prevent.
            path = ""
        if not path.startswith(_PROXYPASS_PREFIX):
            raise EngineUpstreamError(
                "device connection carries a relay url this endpoint cannot "
                "re-address onto the gateway"
            )

    def _gateway_ws_base(self) -> str:
        """WebSocket origin of the gateway, for the environment we run in.

        The pre gateway is a separate host from prod's, and the pair is selected
        the same way every other host pair in this build is. Held as an HTTP
        origin and rewritten here, so one value serves both schemes.
        """
        configured = (
            self._gateway_config.base_url_pre
            if get_current_env() == "pre"
            else self._gateway_config.base_url
        )
        base = configured.strip().rstrip("/")
        if not base:
            # A deployment that fronts no gateway cannot publish a socket. Named
            # rather than allowed out as a 500: this is a configuration state we
            # can describe, and it is the community build's normal one.
            raise EngineUpstreamError(
                "no gateway configured for this deployment (user_config.gateway)"
            )

        # Anchored on the scheme rather than substituted anywhere in the string:
        # a host or path that happens to contain ``http://`` must not be rewritten
        # too. Schemes are case-insensitive, and a value carrying none at all is
        # refused rather than published as a URL nothing can open.
        scheme, separator, rest = base.partition("://")
        ws_scheme = _WS_SCHEMES.get(scheme.lower(), "") if separator else ""
        if not ws_scheme:
            raise EngineUpstreamError(
                f"gateway base url has no usable scheme: {base!r}"
            )
        return f"{ws_scheme}://{rest}"

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
