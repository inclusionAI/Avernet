"""EngineConnectionService — the public replacement for the connection hand-off.

The internal console asks for a device connection and composes
``/proxypass/{target}{path}`` itself. That is fine between our own frontend and
our own backend, and wrong to hand an external tenant: it publishes routing
topology and a raw device credential, both of which become things we cannot
change without breaking integrators.

This service returns **finished** socket URLs instead, addressed to the public
gateway under ``/openapi/v1/bots/messages/ws``. The target and the credential do
travel *inside* that URL — a browser's WebSocket handshake can carry a credential
nowhere else — but no field beside it hands a caller the pieces to assemble a
different one, and nothing names the hop behind the gateway.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit

from injector import inject

from agentclaw.community.core.repository.protocols.bot import CollaboratorRepositoryProtocol
from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.devices.errors import (
    DeviceDomainError,
    DeviceServiceError,
)
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.core.devices.models import OperatorContext
from agentclaw.community.core.devices.services.device_service import DeviceService
from agentclaw.community.core.engine_runtime.errors import (
    EngineDeviceNotReadyError,
    EngineUpstreamError,
)
from agentclaw.community.core.engine_runtime.gate import require_operable_bot
from agentclaw.community.core.engine_runtime.models import ConnectionResult, SocketInfo
from agentclaw.community.core.engine_runtime.sharing import bot_is_shared
from agentclaw.community.di.config import GatewayEndpoint
from agentclaw.community.log import get_logger

logger = get_logger()

#: Lifetime requested for the proxy credential, mirroring the internal WS token.
CONNECTION_TTL_SECONDS = 120 * 60

#: Query parameter the proxy authenticates with. A *parameter* and not a header
#: because the caller is a browser: ``new WebSocket(url, protocols)`` takes no
#: headers, so a URL is the only place a credential can ride. The internal
#: console already opens this socket the same way.
_PROXY_TOKEN_PARAM = "x-proxypass-token"

#: Path prefix the gateway routes on. It sits *inside* the published API
#: namespace rather than at the host root, and inside the ``bots`` scope rather
#: than beside it: the whole bots surface — management here, runtime on the hop
#: behind the gateway — is one team's, and the leading segment names the owner
#: rather than the process. The gateway resolves it by path pattern, so a
#: socket prefix nested under another domain's is an ordinary configuration.
#:
#: ``messages`` names the channel the messages travel over. Other domains are
#: expected to grow their own, so the word is shared vocabulary rather than
#: this surface's alone.
#:
#: ``ws`` names the transport, and it is a segment rather than an implication.
#: The channel is expected to grow HTTP endpoints, and the gateway's socket
#: domain claims this prefix outright — along with the one route-security rule
#: that waives authentication, since a browser handshake carries its credential
#: in the query and can present nothing else. Keeping that claim one segment
#: deeper than ``messages`` is what stops a later endpoint from being born
#: inside it.
#:
#: This value and the gateway's ``bots-messages-ws`` domain are one contract:
#: the gateway matches ``/openapi/v1/bots/messages/ws/**`` and rewrites that
#: prefix onto ``/proxypass``. A change to either without the other publishes
#: URLs the gateway resolves to no domain, and every handshake fails.
_ENGINE_PREFIX = "/openapi/v1/bots/messages/ws"

#: The routing prefix the hop behind the gateway serves. Recognised, then swapped
#: for :data:`_ENGINE_PREFIX` — see :meth:`_readdress_onto_gateway`.
_PROXYPASS_PREFIX = "/proxypass/"

#: Connection kinds whose provider returns a *bare* routing target and a signed
#: proxypass credential, and leaves the URL for the caller to assemble.
#:
#: The ARCA provider answers ``"proxy"``: it composes no URL in any mode, and is
#: never handed the in-device path to bake into one (the base
#: ``get_device_connection`` does not forward ``path`` to
#: ``_compose_device_conn_info``). ``"arca"`` is accepted beside it because that
#: is the ``device_provider`` key the same device carries everywhere else, and a
#: provider spelling one where the other was meant should not be the difference
#: between a socket and a 502.
#:
#: Assembling here is the platform's normal case rather than a special one:
#: ``DeviceService.get_device_connection_v2`` and the internal console's frontend
#: each build ``/proxypass/{target}{path}`` for themselves out of exactly these
#: two values. This surface builds the same thing addressed to the gateway.
_PROXY_TARGET_TYPES = frozenset({"proxy", "arca"})

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


def _quote_or_reject(value: str, *, safe: str, what: str) -> str:
    """Percent-encode ``value`` for a URL, or refuse it by name.

    ``quote`` raises ``UnicodeEncodeError`` on a lone surrogate, which a
    provider's JSON can carry (``json.loads`` decodes ``\\ud800`` happily). That
    would leave this endpoint answering 500 on a value it can describe — the
    same failure the relay-shape guard exists to avoid, one method along.

    ``what`` names the part for the message; the value itself is never included,
    since one of the three callers passes the credential.
    """
    try:
        return quote(value, safe=safe)
    except UnicodeEncodeError as exc:
        raise EngineUpstreamError(
            f"device connection carries an unencodable {what}"
        ) from exc


class EngineConnectionService:
    """Compose the sockets a caller may open against their bot."""

    @inject
    def __init__(
        self,
        bot_service: BotService,
        binding_repository: DeviceBindingRepository,
        device_service: DeviceService,
        gateway: GatewayEndpoint,
        collaborator_repo: CollaboratorRepositoryProtocol,
    ) -> None:
        self._bot_service = bot_service
        self._binding_repository = binding_repository
        self._device_service = device_service
        self._gateway = gateway
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
        # The socket this endpoint publishes is **not** chat-scoped, however it
        # is labelled: the engine's ``hello`` advertises the ``sessions.*`` and
        # ``exec.approvals`` methods and grants ``operator.admin``, so the
        # credential is an operator channel over every session on the device.
        # The shared gate admits exactly the bots whose device only the owner
        # reaches — see ``gate.py`` for the full rule, including why a service
        # bot's *draft* device (the one ``_active_binding_id`` resolves) is in
        # that set. Gated here rather than in the router because the rule is
        # about what may be *composed*, not about how it is served: any future
        # caller of ``build`` is covered without repeating the check.
        require_operable_bot(
            str(bot.get("bot_type") or ""),
            is_shared=bot_is_shared(
                bot,
                self._collaborator_repo,
                bot_id=str(bot.get("bot_id") or bot_id),
                owner_id=str(bot.get("owner_id") or owner_id),
            ),
            surface="connections",
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

        For a ``service`` bot this is the **draft** binding, and that is what
        the gate above relies on: the verify/online runtimes publishing
        produces are bound under ``publish_bot_id`` on the publish records,
        so a ``bot_id``-keyed read cannot select them.
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
            # Both arguments below are meaningful to the BaaS provider and inert
            # elsewhere. They are passed unconditionally because this call site
            # does not know which provider answers it, and because getting them
            # to BaaS is what makes its answer re-addressable at all.
            #
            # Relay is the mode in which BaaS yields a *finished* WebSocket URL.
            # Left unset, it hands back a bare routing target and there would be
            # nothing to re-address. Asking once also keeps the URL and the
            # credential describing the same mode. A bare-target provider returns
            # a bare target either way — the mode does not reach its decision.
            ws_conn_mode=_RELAY_MODE,
            # BaaS bakes this path *into* the relay URL, and that URL is what a
            # caller ends up connecting to, so it has to be the bot's own engine
            # path. Its default is openclaw's, and the engine closes a socket
            # whose pinned engine is not the active one (code 4001) — a
            # claude_code bot handed the default would be rejected on connect.
            #
            # A bare-target provider never sees this: the base
            # ``get_device_connection`` does not forward ``path`` to
            # ``_compose_device_conn_info``. That is why ``_compose_onto_gateway``
            # appends the engine path itself rather than trusting the provider to
            # have applied it.
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

        Providers describe a connection in one of two ways, and both end at the
        same published URL. Some build a finished relay URL around the engine
        path we ask them for; that URL addresses the hop *behind* the gateway, so
        we re-address it rather than rebuilding it from parts — rebuilding would
        assert our own grammar for a URL the provider owns and silently drop
        anything it put there that we did not anticipate. Others hand back the
        routing target and the credential and leave the assembly to the caller,
        which is what every other caller of those providers already does.

        Four cases, in the order they are tested:

        1. A local device — reached directly, with no credential. The gateway
           routes to the hop behind it, which has no path to a device on the
           caller's own machine, and there is no proxy to authenticate to. Its
           ``url`` is not consulted at all: the local provider fills that field
           from *http*-info (``local_device_service.py``), which was never a
           relay URL and was never published here either.
        2. A provider that supplied a ``url`` — re-addressed onto the gateway.
           Tested before the connection kind on purpose: a URL the provider went
           to the trouble of issuing records a routing decision it made and we
           did not, so composing our own over the top would override it in
           silence. It is also what keeps this method's shape from being a
           one-way door — should a bare-target provider ever start issuing relay
           URLs, they win here with no change, and case 3 simply stops being
           reached.
        3. A provider of a bare-target kind (:data:`_PROXY_TARGET_TYPES`) —
           composed onto the gateway from the target, the engine path and the
           credential.
        4. Anything else — refused by name. Deliberately *not* folded into case 3
           as a catch-all: a provider that was supposed to supply a ``url`` and
           did not has a bug, and composing a plausible-looking URL for it would
           bury that bug in a socket that fails at handshake instead.
        """
        target = str(
            getattr(info, "ws_target", "")
            or getattr(info, "target", "")
            or ""
        )
        if not target:
            raise EngineUpstreamError("device connection carries no routing target")

        conn_type = str(getattr(info, "type", "") or "")

        if conn_type == "local":
            # Composed, because there is no relay URL to re-address. This target
            # is an *authority*, so ``@``, ``:`` and the brackets of an IPv6 host
            # all have to survive — singlebox reclassifies a ``::1`` binding as
            # local, and ``ws://%5B::1%5D:20003`` is not a valid authority.
            # Anything else is escaped so it cannot end the path early.
            segment = _quote_or_reject(target, safe="@:[]", what="routing target")
            return f"ws://{segment}{_quote_or_reject(socket_path, safe='/', what='socket path')}"

        if str(getattr(info, "url", "") or ""):
            return self._readdress_onto_gateway(info, token)

        if conn_type in _PROXY_TARGET_TYPES:
            return self._compose_onto_gateway(target, socket_path, token)

        raise EngineUpstreamError(
            f"device connection of kind {conn_type!r} carries no relay url and "
            f"is not a kind this endpoint can compose one for"
        )

    def _compose_onto_gateway(
        self, target: str, socket_path: str, token: str
    ) -> str:
        """The gateway URL for a provider that hands back a bare routing target.

        Byte-for-byte what :meth:`_readdress_onto_gateway` produces for the same
        target, path and credential — the provider's origin and routing prefix
        are discarded there anyway, so the two branches differ only in where the
        tail came from, never in what is published.

        Encoded here, unlike the re-addressed branch, because these are raw
        provider values rather than a URL something already encoded. The target
        keeps ``@``, ``:`` and brackets: it is an authority-like segment
        (``ARCA_{sandbox_id}@{alt}:{port}``), and the credential is a signature
        over that exact string, so a reshaped target is a rejected handshake.
        """
        segment = _quote_or_reject(target, safe="@:[]", what="routing target")
        path = _quote_or_reject(socket_path, safe="/", what="socket path")
        return self._gateway_url(f"{segment}{path}", "", token)

    def _readdress_onto_gateway(self, info: object, token: str) -> str:
        """The provider's relay URL, re-pointed at the gateway.

        Exactly two things change: the origin becomes the gateway's, and the
        hop's ``/proxypass/`` routing prefix becomes
        ``/openapi/v1/bots/messages/ws/``.
        Everything past that prefix — the target, the engine path, any query the
        provider set — is carried through as the provider wrote it, so this
        endpoint holds no opinion about a URL grammar it does not own.

        Refused rather than published when the provider's URL is missing,
        unparseable, or not the ``/proxypass/`` shape: BaaS's LOCAL platform
        answers ``/wsrelay/{session_id}``, which the gateway's rewrite cannot
        express. No bot this endpoint serves produces one, so this is a guard on
        that assumption rather than a supported path. Falling back to the
        provider's own URL is not the alternative — that publishes the hop behind
        the gateway, the one thing this surface must not do.
        """
        url = str(getattr(info, "url", "") or "")
        try:
            parts = urlsplit(url)
        except ValueError:
            # Unparseable is no more re-addressable than the wrong shape, and
            # letting it out would be the 500 this guard exists to prevent.
            parts = None
        if parts is None or not parts.path.startswith(_PROXYPASS_PREFIX):
            raise EngineUpstreamError(
                "device connection carries no relay url this endpoint can "
                "re-address onto the gateway"
            )
        if parts.fragment:
            # A fragment is never sent on the wire, so a relay URL carrying one
            # is already broken upstream and everything after the ``#`` is a
            # path we would silently drop. Named here rather than re-addressed
            # into something shorter that merely looks valid.
            raise EngineUpstreamError(
                "device connection relay url carries a fragment"
            )
        try:
            # Not re-encoded — the provider already encoded this and doing it
            # twice is its own bug. Only checked: a lone surrogate would survive
            # ``urlsplit`` and fail when the response is serialised, turning a
            # describable provider fault into a 500.
            url.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise EngineUpstreamError(
                "device connection carries an unencodable relay url"
            ) from exc

        # ``urlsplit`` has already cut the query and fragment away, so the tail
        # cannot end the path early no matter what it holds. The fragment is
        # dropped rather than carried: a browser never sends one, so keeping it
        # would publish a component that cannot reach the upstream.
        return self._gateway_url(
            parts.path[len(_PROXYPASS_PREFIX) :], parts.query, token
        )

    def _gateway_url(self, tail: str, query: str, token: str) -> str:
        """The gateway URL addressing ``tail``, carrying ``token`` in its query.

        The one place a published socket URL is spelled out, so the callers that
        arrive at a ``tail`` by different routes — re-addressing a provider's
        relay URL, or composing from a bare routing target — cannot drift into
        publishing two different grammars for the same device.

        ``tail`` is everything the gateway forwards: the routing target and the
        in-device path, already encoded by whoever produced it. ``query`` is the
        provider's own, or empty.
        """
        if token:
            # Appended, not assigned — a provider query would otherwise be lost,
            # and the socket would fail with nothing pointing at why. Absent
            # stays absent: an empty ``?x-proxypass-token=`` fails the handshake
            # just as opaquely.
            credential = _quote_or_reject(token, safe="", what="credential")
            separator = "&" if query else ""
            query = f"{query}{separator}{_PROXY_TOKEN_PARAM}={credential}"

        url = f"{self._gateway_ws_base()}{_ENGINE_PREFIX}/{tail}"
        return f"{url}?{query}" if query else url

    def _gateway_ws_base(self) -> str:
        """WebSocket origin of the gateway.

        Which environment's gateway this is was decided by the composition root
        (:class:`~agentclaw.community.di.config.GatewayEndpoint`); this only
        rewrites the scheme, so one configured value serves both.
        """
        base = self._gateway.base_url.strip().rstrip("/")
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
        # A bare origin is all this may be. The prefix appended below already
        # carries the API namespace, so a base url with a path of its own would
        # double it — ``https://gw.example/api`` would publish
        # ``/api/openapi/v1/bots/messages/ws/…``, which no gateway domain resolves. A
        # ``#`` or ``?`` is worse still: it ends the path before the prefix is
        # even appended, putting the credential somewhere a browser never sends.
        if any(delimiter in rest for delimiter in "/#?") or not rest:
            raise EngineUpstreamError(
                f"gateway base url is not a bare origin: {base!r}"
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
