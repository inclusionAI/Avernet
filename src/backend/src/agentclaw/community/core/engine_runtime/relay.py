"""EngineRuntimeRelay — one public request → one engine-adapter call.

The single place Track C crosses into a bot's device. Every public
``/openapi/v1/bots/{bot_id}/…`` runtime handler goes through here, so the four
things that must hold on *every* such request are enforced once:

1. **The caller's bot is resolved owner-scoped, before any device work.** The
   engine has no tenant axis — once a request lands on a device nothing
   constrains it — so this is the last point at which isolation exists.
2. **The device is resolved through ``DeviceContextResolver``**, the repo's
   single provider-resolution point; the relay never picks a provider itself.
3. **The engine's envelope is normalised once.** A ``200`` carrying
   ``success: false`` raises rather than reaching a caller as success.
4. **Transport failures become semantic errors**, so the adapter can map them
   to fixed public messages instead of leaking device detail.

Shape follows ``CronRelayService`` (``core/cron/services/cron_relay.py``), which
has carried ``/api/cron`` over this same path in production since before the
public surface existed.
"""

from __future__ import annotations

from typing import Any

from injector import inject

from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.devices.services.device_context import (
    ConnInfoBuildError,
    DeviceContext,
    DeviceNotBoundError,
    UnknownProviderError,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.engine_runtime.errors import (
    EngineCapabilityUnsupportedError,
    EngineDeviceNotReadyError,
    EngineResourceNotFoundError,
    EngineUpstreamError,
)
from agentclaw.community.core.engine_runtime.models import BotFacts, EngineResult
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterHTTPStatusError,
    DeviceAdapterTransport,
)

logger = get_logger()

#: The engine answers 501 for a capability it does not declare
#: (``src/engine/.../api/caps.py``). That is the one upstream status with a
#: distinct public meaning, so it gets its own error rather than folding into
#: the generic upstream failure.
_CAPABILITY_UNSUPPORTED_STATUS = 501


class EngineRuntimeRelay:
    """Forward one call to a bot's engine adapter and normalise the answer."""

    @inject
    def __init__(
        self,
        bot_service: BotService,
        resolver: DeviceContextResolver,
        transport: DeviceAdapterTransport,
    ) -> None:
        self._bot_service = bot_service
        self._resolver = resolver
        self._transport = transport

    # ── resolution ────────────────────────────────────────────────────────

    def resolve_bot(self, bot_id: str, owner_id: str) -> BotFacts:
        """Return the few bot facts handlers need, or raise.

        **The isolation seam.** ``BotService.get_bot`` goes through
        ``get_by_id_and_owner``, so a bot belonging to someone else — or to
        another tenant, which the Track A guard on ``BotModel`` filters out
        before ownership is even considered — raises ``BotNotFoundError``. The
        adapter maps that to a masked 404 indistinguishable from "no such bot".

        ``owner_id`` must come from the authenticated principal. Passing a
        caller-supplied value turns this check into a formality.

        Returns :class:`BotFacts`, **not** the raw record. ``get_bot`` attaches
        ``device_binding`` — ``device_id``, ``device_provider``, ``device_props``
        — and this is a public-surface entry point whose entire purpose is to
        stop publishing device topology. A narrow value object makes leaking it
        impossible rather than merely discouraged.
        """
        bot = self._bot_service.get_bot(bot_id, owner_id)
        return BotFacts(
            bot_id=str(bot.get("bot_id") or bot_id),
            bot_type=str(bot.get("bot_type") or ""),
            active_engine=str(bot.get("active_engine") or ""),
        )

    def _resolve_device(self, bot_id: str, owner_id: str) -> DeviceContext:
        """Resolve the bot's device, translating "not reachable" to one error.

        ``DeviceNotBoundError`` (never provisioned, or released) and
        ``ConnInfoBuildError`` (the provider could not build connection info)
        are both "try again later" from a caller's point of view, so they
        collapse to a single retryable error. ``UnknownProviderError`` does
        **not** — it means the binding row names a provider we do not know,
        which is bad data on our side and not something a caller can fix by
        retrying. It propagates to the adapter's 500 mapping.
        """
        try:
            return self._resolver.resolve_for_bot(bot_id, owner_id)
        except (DeviceNotBoundError, ConnInfoBuildError) as exc:
            raise EngineDeviceNotReadyError(
                f"device not ready for bot={bot_id}"
            ) from exc
        except UnknownProviderError:
            raise

    # ── forwarding ────────────────────────────────────────────────────────

    async def call(
        self,
        *,
        bot_id: str,
        owner_id: str,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
        enveloped: bool = True,
    ) -> EngineResult:
        """Issue ``method path`` against the caller's bot's engine adapter.

        Resolution order is load-bearing: bot first (isolation), then device,
        then the forward. A handler that reverses it would touch a device for a
        bot the caller does not own.

        ``enveloped`` declares whether this engine route answers with the
        standard ``{success, data, …}`` envelope. Almost all do — but
        ``GET /api/engine/status`` returns ``EngineManager.status()`` **raw**
        (``src/engine/.../api/engine/router.py``), with no ``success`` and no
        ``data`` wrapper. Callers of those routes pass ``enveloped=False`` and
        the whole body becomes the payload. It is an explicit per-route fact
        rather than sniffing for a ``success`` key, because a body that happens
        to lack one is exactly the malformed case that must still fail.
        """
        self.resolve_bot(bot_id, owner_id)
        ctx = self._resolve_device(bot_id, owner_id)
        raw = await self._invoke(ctx, method, path, body, params, timeout)
        return self._normalise(raw, bot_id=bot_id, path=path, enveloped=enveloped)

    async def _invoke(
        self,
        ctx: DeviceContext,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        params: dict[str, Any] | None,
        timeout: float | None,
    ) -> dict[str, Any]:
        """Cross the system boundary, mapping transport failures to our errors.

        ``DeviceAdapterTimeoutError`` is deliberately **not** caught: it is
        already a precise, publicly meaningful failure and the adapter maps it
        straight to 504. Wrapping it would lose that.
        """
        try:
            return await self._transport.invoke(
                ctx.conn_info, method, path, body=body, params=params, timeout=timeout
            )
        except DeviceAdapterEndpointNotFoundError as exc:
            # Any adapter 404, NOT just "this runtime has no such route" — the
            # engine returns 404 for an unknown session id, an unknown model id,
            # an unknown engine name. Mapping it to "capability unsupported"
            # would tell a caller polling a deleted session that its bot lost
            # the sessions capability. A capability the engine does not declare
            # arrives as a 501 below, which is a different status entirely.
            raise EngineResourceNotFoundError(f"engine returned 404 for {path}") from exc
        except DeviceAdapterHTTPStatusError as exc:
            if exc.status_code == _CAPABILITY_UNSUPPORTED_STATUS:
                raise EngineCapabilityUnsupportedError(
                    f"engine does not support {path}"
                ) from exc
            raise EngineUpstreamError(
                f"engine returned HTTP {exc.status_code} for {path}"
            ) from exc
        except ValueError as exc:
            # The transport contract documents a bare ``ValueError`` for
            # transport/HTTP failure in the production implementation, and
            # ``httpx``'s ``.json()`` raises ``JSONDecodeError`` — itself a
            # ``ValueError`` — on a non-JSON body. Neither is engine-runtime
            # specific, so without this they would escape to the app catch-all
            # as a 500. Worse, ``JSONDecodeError`` is already mapped globally to
            # "Malformed engine configuration", which would point a caller at
            # their engine config when the real fault is a malfunctioning
            # device. Both are upstream failures: 502.
            #
            # Listed after the two specific subclasses above, which are also
            # ``ValueError``s and must keep their own meanings.
            raise EngineUpstreamError(f"engine transport failed for {path}") from exc

    # ── normalisation ─────────────────────────────────────────────────────

    def _normalise(
        self, raw: object, *, bot_id: str, path: str, enveloped: bool = True
    ) -> EngineResult:
        """Turn the engine's envelope into an :class:`EngineResult`.

        The engine's shape is ``{success, data, message, warning, total}``
        (``src/engine/.../api/response.py``). Two failure modes are handled here
        rather than downstream:

        - **``success: false`` inside an HTTP 200.** The engine reports business
          failure this way, so a relay that only checked the HTTP status would
          hand a caller a successful envelope wrapping a failure. The message is
          logged, never propagated — it is internal-facing text.
        - **A non-dict body.** A device that answers with something other than
          the envelope is malfunctioning, not returning an empty result.
        """
        if not isinstance(raw, dict):
            logger.warning(
                "[engine_runtime] non-envelope response bot=%s path=%s type=%s",
                bot_id,
                path,
                type(raw).__name__,
            )
            raise EngineUpstreamError(f"engine returned a non-envelope body for {path}")

        # An explicit ``success: false`` is a failure on EVERY route, enveloped
        # or not. A raw-payload route simply has no ``success`` key at all — but
        # a transport can still answer with a failure envelope for one (the
        # community transport returns ``{"success": False, ...}`` for every
        # call). Returning that dict as the payload made ``/engine/status``
        # answer 200 with empty defaults instead of surfacing the upstream
        # failure, so the check runs before the raw-payload branch.
        if raw.get("success") is False:
            logger.warning(
                "[engine_runtime] engine reported failure bot=%s path=%s message=%s",
                bot_id,
                path,
                raw.get("message"),
            )
            raise EngineUpstreamError(f"engine reported failure for {path}")

        if not enveloped:
            # A raw-payload route: the body *is* the data. No success flag, no
            # total, no warning — the engine attaches those only to its envelope.
            return EngineResult(data=raw)

        if not raw.get("success", False):
            logger.warning(
                "[engine_runtime] engine reported failure bot=%s path=%s message=%s",
                bot_id,
                path,
                raw.get("message"),
            )
            raise EngineUpstreamError(f"engine reported failure for {path}")

        # The engine attaches ``warning`` when it serves a capability it
        # declares as limited. It goes no further than this log line. Two
        # reasons: the strings are internal engineering prose and not always
        # English ("通过 mcporter 命令启动"), and across both OSS engines the
        # only limited capability this surface can even reach is
        # ``SESSION_CREATE`` on claude_code — whose caveat describes how the
        # session key is established, not a degraded result. Callers that need
        # to know which capabilities a bot serves with a caveat ask the
        # engine-capabilities endpoint.
        if raw.get("warning"):
            logger.info(
                "[engine_runtime] engine served with a declared limitation "
                "bot=%s path=%s warning=%s",
                bot_id,
                path,
                raw.get("warning"),
            )

        total = raw.get("total")
        return EngineResult(
            data=raw.get("data"),
            total=total if isinstance(total, int) else None,
        )


__all__ = ["EngineRuntimeRelay"]
