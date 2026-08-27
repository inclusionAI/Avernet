"""通过当前 Bot binding 调用容器内 Skills Pool 激活端点。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from injector import inject

from agentclaw.community.core.devices.services.device_context import DeviceContext
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.skill_center.services.runtime_layout_probe import (
    CurrentRuntimeLayoutProbeService,
    MAPPING_CONTRACT_VERSION,
    MAPPING_V3_CONTRACT_VERSION,
    RuntimeLayoutProbeResult,
)
from agentclaw.community.core.skills_pool.models import (
    MappingPublishOutcome,
    PoolCutoverResult,
    PoolCutoverStatus,
    PoolSkillMapping,
    SkillMappingSourceLayout,
)
from agentclaw.community.core.skills_pool.quarantine import RuntimeQuarantineCleanupResult, RuntimeQuarantineCleanupStatus
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterTransport,
)

logger = get_logger()


class SkillsPoolRuntime:
    """ARCA/BaaS 共用的 adapter transport 实现。"""

    @inject
    def __init__(
        self,
        *,
        resolver: DeviceContextResolver,
        adapter_transport: DeviceAdapterTransport,
        probe_service: CurrentRuntimeLayoutProbeService,
    ) -> None:
        self._resolver = resolver
        self._transport = adapter_transport
        self._probe = probe_service

    async def probe(
        self,
        *,
        bot_id: str,
        user_id: str,
        engine: str,
    ) -> RuntimeLayoutProbeResult:
        return await self._probe.probe_bot(
            bot_id=bot_id,
            user_id=user_id,
            engine=engine,
        )

    async def cutover(
        self,
        *,
        bot_id: str,
        user_id: str,
        migration_generation: str,
        preparation_id: str,
        registered_local_names: list[str],
        mappings: list[PoolSkillMapping],
        mapping_contract_version: str = MAPPING_CONTRACT_VERSION,
    ) -> PoolCutoverResult:
        if not await self._ensure_center_mappings(
            bot_id=bot_id,
            user_id=user_id,
            mappings=mappings,
            mapping_contract_version=mapping_contract_version,
            context=None,
        ):
            return PoolCutoverResult(
                committed=False,
                status=PoolCutoverStatus.TRANSIENT_ERROR,
                evidence={"reason": "center_ensure_failed_before_mapping_publish"},
            )
        try:
            response = await self._invoke(
                bot_id=bot_id,
                user_id=user_id,
                path="/api/skills/layout/activate",
                body={
                    "migration_generation": migration_generation,
                    "preparation_id": preparation_id,
                    "registered_local_names": registered_local_names,
                    "mapping_contract_version": mapping_contract_version,
                    "mappings": [mapping.to_dict() for mapping in mappings],
                },
            )
        except Exception as error:
            logger.exception(
                "[skills_pool.runtime] cutover failed bot_id=%s generation=%s",
                bot_id,
                migration_generation,
            )
            return PoolCutoverResult(
                committed=False,
                # The request may have reached the runtime and crossed the
                # atomic boundary before the response was lost. Retrying as a
                # normal pre-cutover error would guess at filesystem truth.
                status=PoolCutoverStatus.UNKNOWN,
                evidence={
                    "reason": "runtime_cutover_outcome_unknown",
                    "error_type": type(error).__name__,
                },
            )
        data = response.get("data")
        if not isinstance(data, dict):
            return PoolCutoverResult(
                committed=False,
                status=PoolCutoverStatus.UNKNOWN,
                evidence={"reason": "runtime_cutover_response_invalid"},
            )
        raw_status = str(data.get("status", ""))
        try:
            status = PoolCutoverStatus(raw_status)
        except ValueError:
            status = PoolCutoverStatus.UNKNOWN
        evidence = dict(data.get("evidence") or {})
        if status is PoolCutoverStatus.UNKNOWN:
            evidence["raw_status"] = raw_status
        committed = data.get("committed") is True and status in {
            PoolCutoverStatus.COMMITTED,
            PoolCutoverStatus.ALREADY_COMMITTED,
        }
        return PoolCutoverResult(
            committed=committed,
            status=status,
            evidence=evidence,
        )

    async def publish_mappings(
        self,
        *,
        bot_id: str,
        user_id: str,
        mappings: list[PoolSkillMapping],
        retired_mappings: Sequence[PoolSkillMapping] = (),
        source_layout: SkillMappingSourceLayout = SkillMappingSourceLayout.POOL,
        mapping_contract_version: str = MAPPING_CONTRACT_VERSION,
    ) -> bool:
        published, _, _ = await self._publish(
            bot_id=bot_id,
            user_id=user_id,
            mappings=mappings,
            retired_mappings=retired_mappings,
            source_layout=source_layout,
            mapping_contract_version=mapping_contract_version,
            context=None,
        )
        return published

    async def publish_and_verify_mappings(
        self,
        *,
        bot_id: str,
        user_id: str,
        mappings: list[PoolSkillMapping],
        retired_mappings: Sequence[PoolSkillMapping] = (),
        source_layout: SkillMappingSourceLayout = SkillMappingSourceLayout.POOL,
        mapping_contract_version: str = MAPPING_CONTRACT_VERSION,
    ) -> MappingPublishOutcome:
        """Publish one mapping set and establish that the runtime converged.

        Two reductions over calling ``publish_mappings`` then
        ``verify_mappings``, and both need to live here rather than at the call
        site, because this is where device resolution happens:

        *One device resolution.* Center-ensure, publish, and the fallback
        verify are up to three adapter calls; resolving per call re-reads the
        same Bot binding for each. The resolved context is a local, so nothing
        can outlive this call and serve a stale sandbox address to a later
        request. Within the call it is a snapshot: a device that re-binds
        between the publish and the fallback verify is checked at its old
        address, which normally resolves to nothing and reports unverified.
        It is not guaranteed to — a provider whose conn_info carries a
        concrete host:port could in principle reach a reassigned address. The
        window is one adapter round trip for an ordinary projection and up to
        two when a Center projection puts ``/center/ensure`` in front of the
        publish; a re-bind inside it triggers the runtime's own re-sync
        regardless, so this verdict was never the one that decided
        convergence.

        *No verify round trip when the runtime already did it.* A publish
        response carrying ``data.verified is True`` means the runtime ran the
        same verification inline, against the filesystem it had just written.
        Anything else — ``false``, or the key absent on a runtime that predates
        the signal — is not evidence of convergence, so absence falls back to
        the separate call and ``false`` is taken at its word.
        """
        try:
            context = self._resolver.resolve_for_bot(bot_id, user_id)
        except Exception:
            logger.exception(
                "[skills_pool.runtime] device resolution failed before publish "
                "bot_id=%s",
                bot_id,
            )
            return MappingPublishOutcome(published=False, verified=False)

        published, inline_verified, response = await self._publish(
            bot_id=bot_id,
            user_id=user_id,
            mappings=mappings,
            retired_mappings=retired_mappings,
            source_layout=source_layout,
            mapping_contract_version=mapping_contract_version,
            context=context,
        )
        if not published:
            return MappingPublishOutcome(published=False, verified=False)
        if inline_verified is not None:
            if inline_verified:
                logger.info(
                    "[skills_pool.runtime] mapping publish verified inline, no "
                    "verify round trip bot_id=%s user_id=%s contract=%s",
                    bot_id,
                    user_id,
                    mapping_contract_version,
                )
            else:
                # Final here and only here: this method does not re-ask the
                # device after a reported failure, so the response carrying the
                # reason has to be logged now or it is lost. The
                # ``publish_mappings`` path makes no such claim — a separate
                # verify still follows it — which is why this cannot live in
                # ``_publish``.
                logger.warning(
                    "[skills_pool.runtime] mapping publish reported verification "
                    "inline as failed bot_id=%s user_id=%s contract=%s response=%s",
                    bot_id,
                    user_id,
                    mapping_contract_version,
                    response,
                )
            return MappingPublishOutcome(
                published=True,
                verified=inline_verified,
                reported_inline=True,
            )
        verified = await self._verify(
            bot_id=bot_id,
            user_id=user_id,
            mappings=mappings,
            retired_mappings=retired_mappings,
            source_layout=source_layout,
            mapping_contract_version=mapping_contract_version,
            context=context,
        )
        return MappingPublishOutcome(published=True, verified=verified)

    async def _publish(
        self,
        *,
        bot_id: str,
        user_id: str,
        mappings: list[PoolSkillMapping],
        retired_mappings: Sequence[PoolSkillMapping],
        source_layout: SkillMappingSourceLayout,
        mapping_contract_version: str,
        context: DeviceContext | None,
    ) -> tuple[bool, bool | None, dict[str, Any] | None]:
        """``(published, inline verdict or None if unreported, raw response)``.

        The response comes back whole rather than narrowed to ``data``: an
        inline verdict is final for the caller that acts on it, so that caller
        needs the runtime's ``message`` and status alongside its evidence, and
        only it knows whether the verdict is final.
        """
        if not await self._ensure_center_mappings(
            bot_id=bot_id,
            user_id=user_id,
            mappings=mappings,
            mapping_contract_version=mapping_contract_version,
            context=context,
        ):
            return False, None, None
        try:
            response = await self._invoke(
                bot_id=bot_id,
                user_id=user_id,
                path="/api/skills/layout/mappings/publish",
                body={
                    "mapping_contract_version": mapping_contract_version,
                    "mappings": [mapping.to_dict() for mapping in mappings],
                    "retired_mappings": [
                        mapping.to_dict() for mapping in retired_mappings
                    ],
                    "source_layout": source_layout.value,
                },
                context=context,
            )
        except Exception:
            logger.exception(
                "[skills_pool.runtime] mapping publish failed bot_id=%s",
                bot_id,
            )
            return False, None, None
        success = response.get("success") is True
        if not success:
            logger.warning(
                "[skills_pool.runtime] mapping publish returned non-success "
                "bot_id=%s user_id=%s contract=%s success=%s response=%s",
                bot_id,
                user_id,
                mapping_contract_version,
                response.get("success"),
                response,
            )
        else:
            logger.info(
                "[skills_pool.runtime] mapping publish succeeded bot_id=%s "
                "user_id=%s contract=%s response_keys=%s",
                bot_id,
                user_id,
                mapping_contract_version,
                sorted(response.keys()),
            )
        return success, _inline_verification(response), response

    async def rollback_to_legacy(
        self,
        *,
        bot_id: str,
        user_id: str,
        rollback_generation: str,
        registered_local_names: list[str],
    ) -> PoolCutoverResult:
        try:
            response = await self._invoke(
                bot_id=bot_id,
                user_id=user_id,
                path="/api/skills/layout/rollback",
                body={
                    "rollback_generation": rollback_generation,
                    "registered_local_names": registered_local_names,
                },
            )
        except Exception as error:
            logger.exception(
                "[skills_pool.runtime] rollback outcome unknown bot_id=%s "
                "generation=%s",
                bot_id,
                rollback_generation,
            )
            return PoolCutoverResult(
                committed=False,
                status=PoolCutoverStatus.UNKNOWN,
                evidence={
                    "reason": "runtime_rollback_outcome_unknown",
                    "error_type": type(error).__name__,
                },
            )
        data = response.get("data")
        if not isinstance(data, dict):
            return PoolCutoverResult(
                committed=False,
                status=PoolCutoverStatus.UNKNOWN,
                evidence={"reason": "runtime_rollback_response_invalid"},
            )
        raw_status = str(data.get("status", ""))
        try:
            status = PoolCutoverStatus(raw_status)
        except ValueError:
            status = PoolCutoverStatus.UNKNOWN
        evidence = dict(data.get("evidence") or {})
        if status is PoolCutoverStatus.UNKNOWN:
            evidence["raw_status"] = raw_status
        return PoolCutoverResult(
            committed=(
                data.get("committed") is True
                and status
                in {
                    PoolCutoverStatus.COMMITTED,
                    PoolCutoverStatus.ALREADY_COMMITTED,
                }
            ),
            status=status,
            evidence=evidence,
        )

    async def cleanup_quarantine(
        self,
        *,
        bot_id: str,
        user_id: str,
        engine: str,
        migration_generation: str,
    ) -> RuntimeQuarantineCleanupResult:
        try:
            response = await self._invoke(
                bot_id=bot_id,
                user_id=user_id,
                path="/api/skills/layout/quarantine/cleanup",
                body={"migration_generation": migration_generation},
            )
        except Exception as error:
            logger.exception(
                "[skills_pool.runtime] quarantine cleanup failed "
                "bot_id=%s generation=%s",
                bot_id,
                migration_generation,
            )
            return RuntimeQuarantineCleanupResult(
                status=RuntimeQuarantineCleanupStatus.TRANSIENT_ERROR,
                evidence={
                    "reason": "runtime_cleanup_outcome_unknown",
                    "error_type": type(error).__name__,
                },
            )
        data = response.get("data")
        if not isinstance(data, dict):
            return RuntimeQuarantineCleanupResult(
                status=RuntimeQuarantineCleanupStatus.TRANSIENT_ERROR,
                evidence={"reason": "invalid_runtime_response"},
            )
        raw_status = str(data.get("status", ""))
        try:
            status = RuntimeQuarantineCleanupStatus(raw_status)
        except ValueError:
            return RuntimeQuarantineCleanupResult(
                status=RuntimeQuarantineCleanupStatus.INVALID,
                evidence={
                    **dict(data.get("evidence") or {}),
                    "reason": "invalid_runtime_response",
                    "raw_status": raw_status,
                },
            )
        return RuntimeQuarantineCleanupResult(
            status=status,
            evidence=dict(data.get("evidence") or {}),
        )

    async def verify_mappings(
        self,
        *,
        bot_id: str,
        user_id: str,
        mappings: list[PoolSkillMapping],
        retired_mappings: Sequence[PoolSkillMapping] = (),
        source_layout: SkillMappingSourceLayout = SkillMappingSourceLayout.POOL,
        mapping_contract_version: str = MAPPING_CONTRACT_VERSION,
    ) -> bool:
        return await self._verify(
            bot_id=bot_id,
            user_id=user_id,
            mappings=mappings,
            retired_mappings=retired_mappings,
            source_layout=source_layout,
            mapping_contract_version=mapping_contract_version,
            context=None,
        )

    async def _verify(
        self,
        *,
        bot_id: str,
        user_id: str,
        mappings: list[PoolSkillMapping],
        retired_mappings: Sequence[PoolSkillMapping],
        source_layout: SkillMappingSourceLayout,
        mapping_contract_version: str,
        context: DeviceContext | None,
    ) -> bool:
        """The verify body, reachable with an already-resolved device.

        Private and default-free on purpose: ``context`` is a devices-layer
        concept, and putting it on ``verify_mappings`` would put it on
        ``SkillsPoolRuntimeProtocol`` too, where a second implementation would
        have to grow a parameter that means nothing to it. The public method
        owns the defaults; this one takes everything explicitly.
        """
        try:
            response = await self._invoke(
                bot_id=bot_id,
                user_id=user_id,
                path="/api/skills/layout/mappings/verify",
                body={
                    "mapping_contract_version": mapping_contract_version,
                    "mappings": [mapping.to_dict() for mapping in mappings],
                    "retired_mappings": [
                        mapping.to_dict() for mapping in retired_mappings
                    ],
                    "source_layout": source_layout.value,
                },
                context=context,
            )
        except Exception:
            logger.exception(
                "[skills_pool.runtime] mapping verify failed bot_id=%s",
                bot_id,
            )
            return False
        data = response.get("data")
        verified = (
            response.get("success") is True
            and isinstance(data, dict)
            and data.get("valid") is True
        )
        if not verified:
            logger.warning(
                "[skills_pool.runtime] mapping verify returned non-verified "
                "bot_id=%s user_id=%s contract=%s success=%s valid=%s response=%s",
                bot_id,
                user_id,
                mapping_contract_version,
                response.get("success"),
                data.get("valid") if isinstance(data, dict) else None,
                response,
            )
        else:
            logger.info(
                "[skills_pool.runtime] mapping verify succeeded bot_id=%s "
                "user_id=%s contract=%s response_keys=%s",
                bot_id,
                user_id,
                mapping_contract_version,
                sorted(response.keys()),
            )
        return verified

    async def _ensure_center_mappings(
        self,
        *,
        bot_id: str,
        user_id: str,
        mappings: Sequence[PoolSkillMapping],
        mapping_contract_version: str,
        context: DeviceContext | None,
    ) -> bool:
        center = [mapping for mapping in mappings if mapping.corpus == "center"]
        if not center:
            return True
        if mapping_contract_version != MAPPING_V3_CONTRACT_VERSION:
            logger.error("[skills_pool.runtime] center mapping requires v3")
            return False
        items = [
            {"skill_uuid": mapping.skill_uuid, "version": mapping.sc_version_number}
            for mapping in center
            if mapping.skill_uuid and mapping.sc_version_number
        ]
        if len(items) != len(center):
            return False
        try:
            response = await self._invoke(
                bot_id=bot_id,
                user_id=user_id,
                path="/api/skills/center/ensure",
                body={"items": items},
                context=context,
            )
        except Exception:
            logger.exception("[skills_pool.runtime] center ensure failed bot_id=%s", bot_id)
            return False
        data = response.get("data")
        return (
            response.get("success") is True
            and isinstance(data, dict)
            and data.get("failed") == []
            and isinstance(data.get("ok"), list)
            and len(data["ok"]) == len(items)
        )

    async def _invoke(
        self,
        *,
        bot_id: str,
        user_id: str,
        path: str,
        body: dict[str, Any],
        context: DeviceContext | None = None,
    ) -> dict[str, Any]:
        """Issue one adapter call, resolving the device unless already given.

        ``context`` lets a caller making several calls in a row resolve once
        and reuse the answer. It is a parameter rather than a memo on this
        service deliberately: the service is a singleton shared across
        requests, so anything cached on it would outlive the request and could
        serve a dead sandbox address after a re-bind.
        """
        if context is None:
            context = self._resolver.resolve_for_bot(bot_id, user_id)
        return await self._transport.invoke(
            context.conn_info,
            "POST",
            path,
            body=body,
            timeout=30.0,
        )


def _inline_verification(response: dict[str, Any]) -> bool | None:
    """The publish response's own verification verdict, or ``None``.

    ``None`` means the runtime said nothing — it predates the signal — and is
    deliberately distinct from ``False``. Reading a missing key as "verified"
    would let an old runtime silently skip verification altogether, so absence
    must route to the separate verify call instead.
    """
    data = response.get("data")
    if not isinstance(data, dict):
        return None
    verified = data.get("verified")
    return verified if isinstance(verified, bool) else None


# Compatibility for callers introduced by the initial OpenClaw rollout.
OpenClawSkillsPoolRuntime = SkillsPoolRuntime


__all__ = ["OpenClawSkillsPoolRuntime", "SkillsPoolRuntime"]
