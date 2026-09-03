"""TeclawDeviceSyncService — Core DeviceSync service for Teclaw devices.

Whole-artifact runtime delivery: each method composes the full
:class:`BotConfigArtifact` (via the lazily-resolved core ``ConfigComposer``)
and POSTs it to the running container, which re-pulls/applies it.

Outbound HTTP uses an injected ``Annotated[HttpClient, QUALIFIER_GENERAL]``
instance (full-absolute-URL client). ``info.http_url`` is resolved INSIDE the
service at ``sync_*`` execution time via :meth:`BaasService.get_http_info` —
the dispatcher and DI service factory never call ``get_http_info`` and never
pre-bind ``info.http_url``, preserving the existing error dicts. The service
imports no transport module directly; HTTP failure classification goes through
the transport-neutral exception aliases re-exported from
``plugin_api.http_client``.

The ``bind_id`` that call needs is **not** re-derived here: it arrives as
``binding_id``, threaded in from the ``DeviceContext`` the factory was handed.
See :meth:`_deliver` for why that is the same value the old
:meth:`BaasService.get_bind_id` round trip returned.

Identity fields (read this before wiring a new call site)
--------------------------------------------------------
``entity_id`` and ``owner_id`` are **distinct** and must not be conflated
(same rule as ``skill_center/factories.py``: paths are scoped by the bot
entity, while bot lookup / device binding are owned by ``ac_bots.owner_id``;
they differ for staff, project and team bots):

* ``entity_id`` → ``ComposeRequest.entity_id`` — the bot's ``ac_bots.entity_id``
  (e.g. ``staff_{user_id}``). Scopes what the composer collects, so it must
  match what :class:`ExternalComposeProducer` uses for the same bot, or the
  runtime push and the publish snapshot would compose different content.
* ``owner_id`` — the bot's ``ac_bots.owner_id``, the identity the binding was
  resolved under and the fallback ``entity_id`` takes when omitted. It no
  longer keys a binding lookup of its own (``binding_id`` comes in resolved).

``entity_id`` defaults to ``owner_id`` when omitted, so a call site that
predates the split keeps its current behavior.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Optional

from agentclaw.community.core.config_compose.models import ComposeOccasion, ComposeRequest
from agentclaw.community.core.devices.services.device_sync import DeviceSync
from agentclaw.community.core.service_bot.services.deploy.engine_ext_stage import (
    enrich_engine_ext,
)
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.kernel.bot_config import BotConfigArtifact
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.http_client import (
    HttpClientRequestError,
    HttpClientStatusError,
)


if TYPE_CHECKING:
    from agentclaw.community.core.config_compose.services.config_composer import ConfigComposer
    from agentclaw.community.core.service_bot.services.baas_service import BaasService
    from agentclaw.community.core.service_bot.services.bot_publish_service import BotPublishService

logger = get_logger()

# Endpoint the running teclaw container exposes to apply a freshly-composed
# config. One home for the contract.
TECLAW_BOT_APPLY_PATH = "/api/v1/bot/apply"


def extract_teclaw_bot_id(target: str) -> str:
    """Pull the teclaw bot id out of a proxypass ``target``.

    ``target`` is ``TECLAW_{bot_id}@{template}:{port}`` (e.g.
    ``TECLAW_b_01KV5KRZG3FGC3H06RE5T9YRT0@4:20003``); the bot id is the substring
    between ``TECLAW_`` and ``@``.
    """
    return target.removeprefix("TECLAW_").split("@", 1)[0]


def build_bot_apply_body(bot_id: str, artifact: BotConfigArtifact) -> dict:
    """Shape the ``/api/v1/bot/apply`` request body (pure — no transport)."""
    return {
        "bot_id": bot_id,
        "operation": "UPDATE",
        "bot_config": artifact.to_dict(),
    }


class TeclawDeviceSyncService(DeviceSync):
    """Whole-artifact runtime delivery for a teclaw bot via the device-sync seam."""

    def __init__(
        self,
        *,
        conn_info: dict[str, Any] | None,
        bot_id: str,
        bot_name: str,
        binding_id: int,
        user_id: str,
        owner_id: str | None,
        engine_type: str,
        composer_provider: Callable[[], ConfigComposer],
        baas_service: BaasService,
        http_client: Any,
        entity_type: str = "staff",
        entity_id: str | None = None,
        draft_recorder: Callable[[], BotPublishService] | None = None,
    ) -> None:
        self._conn_info = conn_info or {}
        self._bot_id = bot_id
        self._bot_name = bot_name
        # ``DeviceContext.binding_id`` — the binding the caller's context was
        # resolved against, threaded straight through to ``get_http_info``
        # instead of being looked up again per delivery. Required, like the
        # field it comes from: a bot with no binding raises
        # ``DeviceNotBoundError`` in the resolver and never reaches this
        # constructor. See ``_deliver``.
        self._binding_id = binding_id
        self._user_id = user_id
        # ``ac_bots.owner_id`` — the identity the binding was resolved under;
        # also ``entity_id``'s fallback below.
        self._owner_id = owner_id or user_id
        # ``ac_bots.entity_id`` — the composer's collection scope. Distinct from
        # owner_id for staff/project/team bots; falls back to owner_id so a call
        # site that predates the split is unchanged. See the module docstring.
        self._entity_id = entity_id or self._owner_id
        self._engine_type = engine_type
        self._composer_provider = composer_provider
        # Resolves the container URL + proxypass token per delivery via
        # get_http_info, keyed by the already-resolved ``binding_id``.
        self._baas_service = baas_service
        self._entity_type = entity_type
        # Injected ``Annotated[HttpClient, QUALIFIER_GENERAL]`` (full-absolute-
        # URL client). The service posts the full ``info.http_url`` per call.
        self._http_client = http_client
        # Lazy thunk (same cycle-break as ``composer_provider``). When set, a
        # successful delivery records the just-delivered artifact onto the bot's
        # DRAFT publish row (observability for draft teclaw service bots). No-ops
        # for personal bots / non-draft rows inside ``record_draft_artifact``.
        self._draft_recorder = draft_recorder

    def sync_symlinks(
        self,
        symlinks: list[dict[str, str]],
        *,
        effective_mcps: Optional[list[dict[str, Any]]] = None,
        desired_skills: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        # symlinks ignored: teclaw re-pulls the whole composed artifact.
        # ``effective_mcps`` is not ignored — see ``_compose_and_deliver``.
        return self._compose_and_deliver(
            caller="sync_symlinks",
            effective_mcps=effective_mcps,
            desired_skills=desired_skills,
        )

    def deliver_manifest_apply(self) -> dict[str, Any]:
        """The closing redeliver of a manifest apply (W8).

        The same whole-artifact delivery as every other method here, composed
        for the ``MANIFEST_APPLY`` occasion: the platform has just written
        every category into its own state, so the artifact's ``ownership``
        map says the platform owns every category and the file categories
        are read from the platform's store. A runtime edit composes for the
        default occasion and leaves them the engine's.
        """
        return self._compose_and_deliver(
            caller="deliver_manifest_apply", occasion=ComposeOccasion.MANIFEST_APPLY
        )

    def sync_bot_config(
        self,
        bot_id: str,
        binding_id: int,
        public: str,
        permission_owner: str | None,
        user_id: str,
        nick_name: str,
    ) -> dict[str, Any]:
        # ROLE/VISIBILITY change is persisted to DB; teclaw re-pulls the whole.
        return self._compose_and_deliver(caller="sync_bot_config")

    # ── MCP delivery ─────────────────────────────────────────────────────
    # MCP edits are persisted to DB before delivery; teclaw consumes the whole
    # composed artifact, not a per-MCP delta, so every MCP method re-composes
    # and re-delivers the full artifact (same path as ``sync_symlinks``).
    def sync_all_mcp_servers(self, mcp_servers: list[dict[str, Any]]) -> bool:
        return self._compose_and_deliver(caller="sync_all_mcp_servers")["success"]

    def sync_single_mcp(
        self,
        mcp_data: dict[str, Any],
        *,
        api_key: str | None = None,
        custom_headers: dict[str, str] | None = None,
        endpoint_env: str = "PROD",
        transport_protocol: str | None = None,
    ) -> bool:
        return self._compose_and_deliver(caller="sync_single_mcp")["success"]

    def sync_remove_mcp(self, server_code: str) -> bool:
        return self._compose_and_deliver(caller="sync_remove_mcp")["success"]

    def has_mcp(self, server_code: str) -> bool:
        # No per-MCP probe on a whole-artifact device: always deliver and count
        # in the multi-bot batch (Option B — teclaw rolls back on failure like
        # arca/baas).
        return True

    def _compose_and_deliver(
        self,
        *,
        caller: str,
        effective_mcps: Optional[list[dict[str, Any]]] = None,
        desired_skills: Optional[list[dict[str, Any]]] = None,
        occasion: ComposeOccasion = ComposeOccasion.RUNTIME,
    ) -> dict[str, Any]:
        """Compose this bot's whole artifact and POST it to its container.

        ``occasion`` is what the compose is for (W8): a runtime edit by
        default, which leaves every category the engine's; the closing
        redeliver of a manifest apply says so and makes them the platform's.

        ``effective_mcps`` is the bot's effective MCP set when the caller
        already resolved it (capability projection does, before it decides
        anything). It rides on the request rather than being re-read: the
        composer's own read is the same ``collect_bot_active_mcps`` query
        against the same database, so the only thing a second one adds is its
        latency. The identifiers below scope the compose to this service's
        bot, and the entries were resolved for that same bot by the projection
        that called it, so the two cannot describe different bots. When it is
        ``None`` the collector reads the set itself, exactly as before.
        """
        try:
            artifact = self._composer_provider().compose(
                ComposeRequest(
                    entity_id=self._entity_id,
                    bot_id=self._bot_id,
                    user_id=self._user_id,
                    engine_type=self._engine_type,
                    entity_type=self._entity_type,
                    occasion=occasion,
                    effective_mcps=(
                        None if effective_mcps is None else tuple(effective_mcps)
                    ),
                    desired_skills=(
                        None if desired_skills is None else tuple(desired_skills)
                    ),
                )
            )
            # Enrich the composer's (empty) engine_ext with the backend identity/stage
            # keys, mirroring the publish producer so the engine sees the same shape on
            # every push. Runtime edits only touch the live/draft bot → stage=draft.
            # owner_id tracks ComposeRequest.user_id (= self._user_id), as in the
            # producer (engine_ext.owner_id == compose user_id, not entity_id).
            artifact = dataclasses.replace(
                artifact,
                engine_ext=enrich_engine_ext(
                    artifact.engine_ext,
                    bot_id=self._bot_id,
                    owner_id=self._user_id,
                    bot_name=self._bot_name,
                    stage=PublishStage.DRAFT,
                ),
            )
            self._deliver(artifact)
            self._record_draft_artifact(artifact)
        except HttpClientStatusError as e:
            logger.error(
                "[TeclawDeviceSyncService.%s] HTTP error for bot=%s: %s - %s",
                caller, self._bot_id, e.response.status_code, e.response.text,
            )
            return {"success": False, "message": f"HTTP 错误: {e.response.status_code}"}
        except HttpClientRequestError as e:
            logger.error(
                "[TeclawDeviceSyncService.%s] request failed for bot=%s: %s",
                caller, self._bot_id, e,
            )
            return {"success": False, "message": f"请求失败: {e}"}
        except Exception as e:  # compose / transport errors must not crash callers
            logger.exception(
                "[TeclawDeviceSyncService.%s] error for bot=%s: %s",
                caller, self._bot_id, e,
            )
            return {"success": False, "message": f"投递失败: {e}"}

        logger.info(
            "[TeclawDeviceSyncService.%s] delivered artifact for bot=%s",
            caller, self._bot_id,
        )
        return {"success": True, "message": "teclaw artifact delivered"}

    def _record_draft_artifact(self, artifact: BotConfigArtifact) -> None:
        """Best-effort: record the just-delivered artifact onto the bot's DRAFT
        publish row. Called only after a successful ``_deliver``, so the stored
        artifact equals what reached the container.

        Self-contained try/except: a recording failure must never flip the
        delivery result (the container already has the artifact).
        """
        if self._draft_recorder is None:
            return
        try:
            self._draft_recorder().record_draft_artifact(
                bot_id=self._bot_id,
                artifact=artifact.to_dict(),
            )
        except Exception as e:
            logger.warning(
                "[TeclawDeviceSyncService] draft artifact record failed bot=%s: %s",
                self._bot_id, e,
            )

    def _deliver(self, artifact: BotConfigArtifact) -> None:
        """POST the composed full artifact to the running teclaw container.

        Resolves the container URL + proxypass token per-request via
        :meth:`BaasService.get_http_info`, derives the teclaw bot id from the
        resolved ``target``, and POSTs the ``/api/v1/bot/apply`` contract with
        the proxy token in the ``x-proxypass-token`` header (the auth the
        agentclawproxy ``/proxypass`` gateway expects — same gateway/header
        ARCA uses). The full ``info.http_url`` is posted through the injected
        ``HttpClient`` (resolved INSIDE the service at ``sync_*`` time).

        ``bind_id`` is ``self._binding_id`` — the id the ``DeviceContext``
        already carried — not a fresh :meth:`BaasService.get_bind_id` round
        trip. The two are the same value on this path, and the substitution is
        safe because ``get_bind_id`` only performs publish-stage selection when
        it is *given* a ``publish_status``:

        * This call site never passed one, so ``get_bind_id`` always took its
          ``publish_status is None`` branch — ``get_by_id_and_owner`` then the
          row's ``binding_id`` column. ``select_stage_bind_id`` was unreachable
          from here (the stage-aware callers pass ``PublishStatus.SUCCESS``
          explicitly). Its ``bot_type`` argument is not read at all.
        * Every ``DeviceSync`` ctx in the tree is built by
          ``DeviceContextResolver.resolve_for_bot``, whose binding comes from
          ``get_active_by_bot_and_owner`` — an ``ac_bots``⋈``binding`` JOIN on
          ``ac_bots.binding_id == binding.id``. So ``ctx.binding_id`` *is* that
          same column of that same row, draft/published split included.

        Threading it also makes the delivery internally consistent: the
        container is now addressed by the binding the rest of this service's
        state (conn_info, tenant, port) was built from, rather than by a second
        lookup that could, in principle, answer for a different row.
        """
        conn = self._conn_info
        info = self._baas_service.get_http_info(
            bind_id=self._binding_id,
            port=conn.get("engine_port", 20003),
            path=TECLAW_BOT_APPLY_PATH,
            tenant=conn.get("tenant"),
            device_affinity=self._user_id,
        )

        teclaw_bot_id = extract_teclaw_bot_id(info.target)
        body = build_bot_apply_body(teclaw_bot_id, artifact)
        # The teclaw container is reached through the agentclawproxy gateway
        # (``{base}/proxypass/{target}{path}`` — the URL get_http_info resolves),
        # which authenticates with ``x-proxypass-token`` carrying the proxy_token,
        # the same gateway/header ARCA uses (NOT the secbaas invoke-http tunnel's
        # ``openclawToken``).
        headers = {
            "Content-Type": "application/json",
            "x-proxypass-token": info.token,
        }

        logger.info(
            "[TeclawDeviceSyncService] POST %s for bot=%s teclaw_bot_id=%s",
            info.http_url, self._bot_id, teclaw_bot_id,
        )
        response = self._http_client.post(
            info.http_url, json=body, headers=headers, timeout=30
        )
        response.raise_for_status()
