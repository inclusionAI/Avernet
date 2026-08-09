"""EngineRuntimeRelay — one public request → one engine-adapter call.

The single place Track C crosses into a bot's device. Every public
``/openapi/v1/bots/<component>/{bot_id}/…`` runtime handler goes through here,
so the four
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

import asyncio
from typing import Any

from injector import inject

from agentclaw.community.core.repository.protocols.bot import CollaboratorRepositoryProtocol
from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.devices.services.device_context import (
    ConnInfoBuildError,
    DeviceContext,
    DeviceNotBoundError,
    UnknownProviderError,
)
from agentclaw.community.core.repository.protocols.devices import (
    DeviceBindingRepository,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.engine_runtime.errors import (
    EngineCapabilityUnsupportedError,
    EngineDeviceNotReadyError,
    EngineResourceNotFoundError,
    EngineStageNotLiveError,
    EngineUpstreamError,
)
from agentclaw.community.core.engine_runtime.gate import require_bot_operator
from agentclaw.community.core.engine_runtime.models import BotFacts, EngineResult
from agentclaw.community.core.engine_runtime.stage import (
    SERVICE_BOT_TYPE,
    STAGE_DRAFT,
    resolve_stage_bind_id,
)
from agentclaw.community.core.repository.protocols.publishing import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env
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

#: A published bot, whose stage runtimes are **not** the one
#: ``ac_bots.binding_id`` names. See
#: :meth:`EngineRuntimeRelay._resolve_published_device`.
_SERVICE_BOT_TYPE = SERVICE_BOT_TYPE


class EngineRuntimeRelay:
    """Forward one call to a bot's engine adapter and normalise the answer."""

    @inject
    def __init__(
        self,
        bot_service: BotService,
        resolver: DeviceContextResolver,
        transport: DeviceAdapterTransport,
        publish_repo: BotPublishRepositoryProtocol,
        collaborator_repo: CollaboratorRepositoryProtocol,
        binding_repo: DeviceBindingRepository,
    ) -> None:
        self._bot_service = bot_service
        self._resolver = resolver
        self._transport = transport
        self._publish_repo = publish_repo
        self._collaborator_repo = collaborator_repo
        self._binding_repo = binding_repo

    # ── resolution ────────────────────────────────────────────────────────

    def resolve_bot(self, bot_id: str, owner_id: str, caller_id: str) -> BotFacts:
        """Return the few bot facts handlers need, or raise.

        **The isolation seam, in two steps.** ``BotService.get_bot`` resolves
        ``(bot_id, owner_id)`` through ``get_by_id_and_owner``, so a bot that
        does not exist under that owner — or belongs to another tenant, which
        the Track A guard on ``BotModel`` filters out before ownership is even
        considered — raises ``BotNotFoundError``. Then
        :func:`require_bot_operator` decides whether *this caller* may operate
        the resolved bot: the owner, or a collaborator at member level or
        above; anyone else raises the same ``BotNotFoundError``, so a refused
        non-operator cannot tell a bot they may not operate from one that does
        not exist. The adapter maps both to a masked 404.

        ``caller_id`` must come from the authenticated principal; ``owner_id``
        is the owner the request *addresses* and may name someone else — the
        adjudication is exactly what makes that safe.

        Returns :class:`BotFacts`, **not** the raw record. ``get_bot`` attaches
        ``device_binding`` — ``device_id``, ``device_provider``, ``device_props``
        — and this is a public-surface entry point whose entire purpose is to
        stop publishing device topology. A narrow value object makes leaking it
        impossible rather than merely discouraged.
        """
        bot = self._bot_service.get_bot(bot_id, owner_id)
        resolved_id = str(bot.get("bot_id") or bot_id)
        resolved_owner = str(bot.get("owner_id") or owner_id)
        require_bot_operator(
            self._collaborator_repo,
            bot_pk=int(bot.get("id") or 0),
            bot_id=resolved_id,
            caller_id=caller_id,
            owner_id=resolved_owner,
        )
        return BotFacts(
            bot_id=resolved_id,
            bot_type=str(bot.get("bot_type") or ""),
            active_engine=str(bot.get("active_engine") or ""),
            owner_id=resolved_owner,
            bot_pk=int(bot.get("id") or 0),
        )

    async def resolve_bot_off_loop(
        self, bot_id: str, owner_id: str, caller_id: str
    ) -> BotFacts:
        """:meth:`resolve_bot`, run in a worker thread.

        ``resolve_bot`` is synchronous and not cheap: ``BotService.get_bot``
        does an owner-scoped row read, a device-binding fetch and a template
        fetch, and the operator adjudication may add a collaborator query.
        Running that inline parks the event loop for the length of one slow
        database round trip and stalls every unrelated request on the worker —
        the same reason :meth:`call` already offloads device resolution.

        Handlers that gate on bot facts before forwarding use this, then hand
        the result to :meth:`call` as ``facts`` so the bot is resolved once per
        request rather than once per gate plus once per forward.
        """
        return await asyncio.to_thread(self.resolve_bot, bot_id, owner_id, caller_id)

    def _resolve_device(
        self, bot_id: str, owner_id: str, facts: BotFacts, stage: str
    ) -> DeviceContext:
        """Resolve the bot's device, translating "not reachable" to one error.

        ``stage`` picks which of a ``service`` bot's runtimes this call
        addresses. :data:`~agentclaw.community.core.engine_runtime.stage.\
STAGE_DRAFT` resolves the bot's own binding (``ac_bots.binding_id``, the
        pre-publication workspace); a published stage resolves that stage's
        live publish-record binding through
        :func:`~agentclaw.community.core.engine_runtime.stage.\
resolve_stage_bind_id`. The draft lookup is the same owner-scoped
        ``resolve_for_bot`` a personal bot uses, and a personal bot ignores
        the stage entirely — it has only its workspace, and the *refusal* of a
        published stage on one is the gate's job
        (``require_operable_bot``), before any device work.

        ``DeviceNotBoundError`` (never provisioned, released, or malformed
        publish data) and ``ConnInfoBuildError`` (the provider could not build
        connection info) are both "try again later" from a caller's point of
        view, so they collapse to a single retryable error.
        ``EngineStageNotLiveError`` does **not** — a stage with no live
        runtime is its own caller-facing answer, not a retry.
        ``UnknownProviderError`` also propagates — the binding row names a
        provider we do not know, which is bad data on our side and not
        something a caller can fix by retrying.

        **Synchronous on purpose, and never called from the event loop.** The
        provider leg of this is blocking network I/O — the BaaS builder calls
        ``BaasService.get_ws_info`` over a sync ``httpx`` client with a 30-second
        timeout — so :meth:`call` runs it in a worker thread.
        """
        try:
            if facts.bot_type == _SERVICE_BOT_TYPE and stage != STAGE_DRAFT:
                return self._resolve_published_device(facts, owner_id, stage)
            return self._resolver.resolve_for_bot(bot_id, owner_id)
        except (DeviceNotBoundError, ConnInfoBuildError) as exc:
            raise EngineDeviceNotReadyError(
                f"device not ready for bot={bot_id}"
            ) from exc
        except (EngineStageNotLiveError, UnknownProviderError):
            raise

    def _resolve_published_device(
        self, facts: BotFacts, owner_id: str, stage: str
    ) -> DeviceContext:
        """Resolve a ``service`` bot through a published stage's runtime binding.

        A service bot's ``ac_bots.binding_id`` is the pre-publication draft — on
        the BaaS path it is the owner's own personal device, and the bindings
        publishing produced are not on that column at all
        (``baas_builder.BaasConnInfoBuilder._resolve_bot``): they live only in
        ``ac_bot_publish.ext.binding.{verify,online}``. Which record and which
        key a stage names is :func:`~agentclaw.community.core.engine_runtime.\
stage.resolve_stage_bind_id`'s rule, shared with the connection service so a
        socket and an HTTP forward for the same (bot, stage) cannot address
        different devices. A stage with no live record raises
        ``EngineStageNotLiveError`` there; no fallback to the draft binding,
        and none between stages.
        """
        bind_id = resolve_stage_bind_id(
            self._publish_repo,
            self._binding_repo,
            bot_pk=facts.bot_pk,
            bot_id=facts.bot_id,
            stage=stage,
            env=get_current_env(),
        )
        # ``…_invoke`` rather than ``resolve_for_binding``: for the multi-instance
        # providers a published bot runs on, the transport fetches the address per
        # binding at call time and this only has to carry the routing fields. It
        # falls through to full resolution for the providers that need it.
        return self._resolver.resolve_for_binding_invoke(
            bind_id, owner_id, bot_id=facts.bot_id
        )

    def _resolve_bot_and_device(
        self, bot_id: str, owner_id: str, facts: BotFacts | None, stage: str
    ) -> DeviceContext:
        """Prove ownership, then resolve the device — one worker-thread hop.

        Kept together so :meth:`call` offloads once rather than twice. The
        order is the isolation order: ``resolve_bot`` raises for a bot the
        caller may not operate, before any device is touched. On this path the
        caller *is* the owner: a route that serves someone other than the
        bot's owner must adjudicate the real caller first and pass ``facts``.
        """
        resolved = (
            facts
            if facts is not None
            else self.resolve_bot(bot_id, owner_id, owner_id)
        )
        return self._resolve_device(bot_id, owner_id, resolved, stage)

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
        facts: BotFacts | None = None,
        stage: str,
    ) -> EngineResult:
        """Issue ``method path`` against the addressed bot's engine adapter.

        Resolution order is load-bearing: bot first (isolation and the
        operator adjudication), then device, then the forward. A handler that
        reverses it would touch a device for a bot the caller may not operate.

        ``facts`` is the bot this call was already resolved against, for
        handlers that had to resolve it to gate on it — the gated groups
        resolve to run the operator adjudication and the type gate before
        forwarding. Passing it keeps the request at one resolution instead of
        two. ``None`` means "not resolved yet" and this call resolves it with
        the owner as the caller, which is what every ungated (owner-scoped)
        route does. It is **not** a way to supply bot facts from outside: the
        only safe value is one this relay returned for the same
        ``bot_id``/``owner_id``, since it stands in for the ownership proof.

        ``stage`` names which of a ``service`` bot's runtimes this call
        addresses — see :meth:`_resolve_device` for the rule. Required, with
        **no default**, deliberately: the gate's stage and the forward's stage
        must be the same value, and a default here would let a handler that
        gated on one silently address another — the cross-device leak the old
        mandatory ``draft_device=True`` discipline prevented. A personal bot
        ignores it (it has only its workspace; refusing a published stage on
        one is the gate's job).

        Bot and device resolution share one **worker thread** hop. Both legs are
        synchronous and neither belongs on the event loop:
        ``BotService.get_bot`` does an owner-scoped row read plus device-binding
        and template fetches, and the device leg's provider call is blocking
        network I/O — a BaaS-backed bot resolves through
        ``BaasService.get_ws_info``, a sync ``httpx`` call with a 30-second
        timeout — so running either inline would park the event loop for the
        length of one slow database or provider round trip and stall every
        unrelated request on the worker. ``CronRelayService`` offloads the same resolution for the same
        reason (``_prepare_runtime_query_async``), which is also what makes this
        safe: the repositories underneath are already driven from worker threads
        on that path in production. No semaphore here — cron needs one because it
        fans out over every target of one request, while this resolves exactly
        one device per inbound request, where the bound belongs to the server.

        ``enveloped`` declares whether this engine route answers with the
        standard ``{success, data, …}`` envelope. Almost all do — but
        ``GET /api/engine/status`` returns ``EngineManager.status()`` **raw**
        (``src/engine/.../api/engine/router.py``), with no ``success`` and no
        ``data`` wrapper. Callers of those routes pass ``enveloped=False`` and
        the whole body becomes the payload. It is an explicit per-route fact
        rather than sniffing for a ``success`` key, because a body that happens
        to lack one is exactly the malformed case that must still fail.
        """
        ctx = await asyncio.to_thread(
            self._resolve_bot_and_device, bot_id, owner_id, facts, stage
        )
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
