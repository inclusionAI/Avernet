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
import json
from typing import Any

from injector import inject

from agentclaw.community.core.bot_collaborator.repository.protocol import (
    CollaboratorRepositoryProtocol,
)
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
from agentclaw.community.core.engine_runtime.sharing import bot_is_shared
from agentclaw.community.core.service_bot.repository.bot_publish_repository import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.service_bot.repository.models import (
    PublishStatus,
    select_stage_bind_id,
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

#: A published bot, whose runtime device is **not** the one ``ac_bots.binding_id``
#: names. See :meth:`EngineRuntimeRelay._resolve_published_device`.
_SERVICE_BOT_TYPE = "service"


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
    ) -> None:
        self._bot_service = bot_service
        self._resolver = resolver
        self._transport = transport
        self._publish_repo = publish_repo
        self._collaborator_repo = collaborator_repo

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
        resolved_id = str(bot.get("bot_id") or bot_id)
        resolved_owner = str(bot.get("owner_id") or owner_id)
        return BotFacts(
            bot_id=resolved_id,
            bot_type=str(bot.get("bot_type") or ""),
            active_engine=str(bot.get("active_engine") or ""),
            bot_pk=int(bot.get("id") or 0),
            is_shared=bot_is_shared(
                bot,
                self._collaborator_repo,
                bot_id=resolved_id,
                owner_id=resolved_owner,
            ),
        )

    async def resolve_bot_off_loop(self, bot_id: str, owner_id: str) -> BotFacts:
        """:meth:`resolve_bot`, run in a worker thread.

        ``resolve_bot`` is synchronous and not cheap: ``BotService.get_bot``
        does an owner-scoped row read, a device-binding fetch and a template
        fetch, and :meth:`_is_shared` may add a collaborator query. Running
        that inline parks the event loop for the length of one slow database
        round trip and stalls every unrelated request on the worker — the same
        reason :meth:`call` already offloads device resolution.

        Handlers that gate on bot facts before forwarding use this, then hand
        the result to :meth:`call` as ``facts`` so the bot is resolved once per
        request rather than once per gate plus once per forward.
        """
        return await asyncio.to_thread(self.resolve_bot, bot_id, owner_id)

    def _resolve_device(
        self, bot_id: str, owner_id: str, facts: BotFacts, draft_device: bool
    ) -> DeviceContext:
        """Resolve the bot's device, translating "not reachable" to one error.

        ``draft_device`` picks which of a ``service`` bot's devices this call
        addresses. The default resolves the **published** runtime — the device
        the bot a caller addressed actually runs on. ``True`` resolves the
        bot's own binding (``ac_bots.binding_id``, the pre-publication draft)
        instead; the sessions group passes it because that surface operates the
        owner's draft workspace, and the published runtime is a multi-caller
        device whose session collection is not scoped per caller. The draft
        lookup is the same owner-scoped ``resolve_for_bot`` a personal bot
        uses, so for a personal bot the flag changes nothing.

        ``DeviceNotBoundError`` (never provisioned, released, or — for a service
        bot — never published) and ``ConnInfoBuildError`` (the provider could not
        build connection info) are both "try again later" from a caller's point
        of view, so they collapse to a single retryable error.
        ``UnknownProviderError`` does **not** — it means the binding row names a
        provider we do not know, which is bad data on our side and not something
        a caller can fix by retrying. It propagates to the adapter's 500 mapping.

        **Synchronous on purpose, and never called from the event loop.** The
        provider leg of this is blocking network I/O — the BaaS builder calls
        ``BaasService.get_ws_info`` over a sync ``httpx`` client with a 30-second
        timeout — so :meth:`call` runs it in a worker thread.
        """
        try:
            if facts.bot_type == _SERVICE_BOT_TYPE and not draft_device:
                return self._resolve_published_device(facts, owner_id)
            return self._resolver.resolve_for_bot(bot_id, owner_id)
        except (DeviceNotBoundError, ConnInfoBuildError) as exc:
            raise EngineDeviceNotReadyError(
                f"device not ready for bot={bot_id}"
            ) from exc
        except UnknownProviderError:
            raise

    def _resolve_published_device(
        self, facts: BotFacts, owner_id: str
    ) -> DeviceContext:
        """Resolve a ``service`` bot through its **published** runtime binding.

        A service bot's ``ac_bots.binding_id`` is the pre-publication draft — on
        the BaaS path it is the owner's own personal device, and the binding
        publishing produced is not on that column at all
        (``baas_builder.BaasConnInfoBuilder._resolve_bot``). So the by-bot entry
        point resolves the wrong device for these bots: engine, model and
        approval calls would land on the owner's draft box while the published
        bot a caller actually addressed runs elsewhere, or fail as "not ready"
        once the draft binding is released while the published bot is healthy.

        The live binding is the publish record's ``ext.binding.online``, and
        ``select_stage_bind_id`` is the shared selector for that choice rather
        than a second copy of the rule; on a ``success`` record it yields
        ``online``.

        **Keyed on the bot's primary key, never on ``bot_id``.** ``bot_id`` is
        not unique across owners — the column carries no unique constraint, and
        ``create_bot_for_others`` gives every user a bot called ``default`` — so
        a lookup by ``(bot_id, env)`` alone selects whichever owner published
        most recently, and could hand one caller another owner's running device.
        The owner-scoped bot resolution in :meth:`call` does not constrain a
        second query that does not mention the row it authorised, which is why
        the ``ac_bots`` primary key is threaded through :class:`BotFacts` and
        used here. Filtering by ``owner_id`` instead would also be safe, but it
        re-introduces the false negative
        ``get_latest_success_by_source_bot_id`` warns about — an org bot whose
        record was created under a different staff id. The primary key has
        neither problem: it is the identity of the exact row ownership was
        proven against.

        No fallback to the draft binding. Serving the draft is the defect this
        replaces, so a bot with no published runtime is "not ready" — the same
        answer an unprovisioned personal bot gets.
        """
        bot_id = facts.bot_id
        if not facts.bot_pk:
            raise DeviceNotBoundError(
                f"EngineRuntimeRelay: no bot primary key for bot={bot_id}; "
                "cannot resolve a published runtime without one"
            )

        env = get_current_env()
        # Records come back newest-first; the newest *successful* one is the
        # published runtime. Scoped to this bot row, so "newest" cannot mean
        # "some other owner's".
        record = next(
            (
                r
                for r in self._publish_repo.list_by_source_bot(facts.bot_pk, env)
                if r.status == PublishStatus.SUCCESS.value
            ),
            None,
        )
        if record is None:
            raise DeviceNotBoundError(
                f"EngineRuntimeRelay: no published runtime for bot={bot_id} env={env}"
            )

        # ``BotPublishRecord.ext`` is parsed to a dict by ``to_record()``; the
        # str branch mirrors the defensive handling in ``DeviceInstanceService``.
        ext = record.ext or {}
        if isinstance(ext, str):
            try:
                ext = json.loads(ext)
            except (json.JSONDecodeError, TypeError):
                raise DeviceNotBoundError(
                    f"EngineRuntimeRelay: unreadable publish ext for bot={bot_id} "
                    f"publish_id={record.id}"
                ) from None

        bind_id = select_stage_bind_id(ext.get("binding") or {}, record.status)
        if not bind_id:
            raise DeviceNotBoundError(
                f"EngineRuntimeRelay: no stage binding for bot={bot_id} "
                f"publish_id={record.id} status={record.status}"
            )

        # ``…_invoke`` rather than ``resolve_for_binding``: for the multi-instance
        # providers a published bot runs on, the transport fetches the address per
        # binding at call time and this only has to carry the routing fields. It
        # falls through to full resolution for the providers that need it.
        return self._resolver.resolve_for_binding_invoke(
            int(bind_id), owner_id, bot_id=bot_id
        )

    def _resolve_bot_and_device(
        self, bot_id: str, owner_id: str, facts: BotFacts | None, draft_device: bool
    ) -> DeviceContext:
        """Prove ownership, then resolve the device — one worker-thread hop.

        Kept together so :meth:`call` offloads once rather than twice. The
        order is the isolation order: ``resolve_bot`` raises for a bot the
        caller does not own, before any device is touched.
        """
        resolved = facts if facts is not None else self.resolve_bot(bot_id, owner_id)
        return self._resolve_device(bot_id, owner_id, resolved, draft_device)

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
        draft_device: bool = False,
    ) -> EngineResult:
        """Issue ``method path`` against the caller's bot's engine adapter.

        Resolution order is load-bearing: bot first (isolation), then device,
        then the forward. A handler that reverses it would touch a device for a
        bot the caller does not own.

        ``facts`` is the bot this call was already resolved against, for
        handlers that had to resolve it to gate on it — the sessions group
        resolves to check :attr:`BotFacts.is_shared` before forwarding. Passing
        it keeps the request at one owner-scoped resolution instead of two.
        ``None`` means "not resolved yet" and this call resolves it, which is
        what every ungated route does. It is **not** a way to supply bot facts
        from outside: the only safe value is one this relay returned for the
        same ``bot_id``/``owner_id``, since it stands in for the ownership
        proof.

        ``draft_device`` addresses a ``service`` bot's pre-publication draft
        binding instead of its published runtime — see :meth:`_resolve_device`
        for the rule and who passes it. Inert for a personal bot.

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
            self._resolve_bot_and_device, bot_id, owner_id, facts, draft_device
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
