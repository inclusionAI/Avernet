"""通过当前 Bot binding 调用容器内 Skills Pool 激活端点。"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from injector import inject

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
    MappingApplyMode,
    MappingApplyResult,
    MappingItemResult,
    MappingProjectionStatus,
    MappingPublishResult,
    MappingVerificationResult,
    PoolCutoverResult,
    PoolCutoverStatus,
    PoolSkillMapping,
    SkillMappingSourceLayout,
)
from agentclaw.community.core.skills_pool.quarantine import (
    RuntimeQuarantineCleanupResult,
    RuntimeQuarantineCleanupStatus,
)
from agentclaw.community.core.skills_pool.ports import LegacyMappingApplyRequired
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
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

    async def apply_mappings(
        self,
        *,
        bot_id: str,
        user_id: str,
        engine: str,
        mappings: list[PoolSkillMapping],
        retired_mappings: Sequence[PoolSkillMapping] = (),
        source_layout: SkillMappingSourceLayout = SkillMappingSourceLayout.POOL,
    ) -> MappingApplyResult:
        """Apply one logical snapshot, falling back only after bounded proof."""

        context = self._resolver.resolve_for_bot(bot_id, user_id)
        body = {
            "mappings": [mapping.to_dict() for mapping in mappings],
            "retired_mappings": [mapping.to_dict() for mapping in retired_mappings],
            "source_layout": source_layout.value,
        }
        try:
            response = await self._transport.invoke(
                context.conn_info,
                "POST",
                "/api/skills/mappings/apply",
                body=body,
                timeout=30.0,
            )
        except DeviceAdapterEndpointNotFoundError as error:
            if not error.standard_route_missing:
                return self._unavailable_apply_result("runtime_mapping_apply_nonstandard_404")
            try:
                health = await self._transport.invoke(
                    context.conn_info,
                    "GET",
                    "/health",
                    timeout=10.0,
                )
            except Exception:
                return self._unavailable_apply_result("runtime_mapping_apply_health_unavailable")
            if health.get("status") == "ok" and health.get("engine") == engine:
                raise LegacyMappingApplyRequired() from error
            return self._unavailable_apply_result("runtime_mapping_apply_health_mismatch")
        except Exception as error:
            logger.exception(
                "[skills_pool.runtime] logical mapping apply unavailable bot_id=%s",
                bot_id,
            )
            return self._unavailable_apply_result(
                "runtime_mapping_apply_outcome_unknown",
                error_type=type(error).__name__,
            )
        return self._mapping_apply_result(response)

    @staticmethod
    def _unavailable_apply_result(
        reason: str, *, error_type: str | None = None
    ) -> MappingApplyResult:
        evidence: dict[str, object] = {"reason": reason}
        if error_type is not None:
            evidence["error_type"] = error_type
        return MappingApplyResult(
            status=MappingProjectionStatus.PENDING,
            evidence=evidence,
        )

    def _mapping_apply_result(self, response: dict[str, Any]) -> MappingApplyResult:
        data = response.get("data")
        if not isinstance(data, dict):
            return self._unavailable_apply_result("invalid_runtime_response")
        raw_status = data.get("status")
        try:
            status = MappingProjectionStatus(str(raw_status))
        except ValueError:
            return self._unavailable_apply_result("invalid_runtime_status")
        success = response.get("success")
        if not isinstance(success, bool):
            return self._unavailable_apply_result("invalid_runtime_response")
        if success is False and status is MappingProjectionStatus.CONVERGED:
            return self._unavailable_apply_result("contradictory_runtime_response")
        raw_items = data.get("items")
        raw_issues = data.get("issues")
        if not isinstance(raw_items, list) or not isinstance(raw_issues, list):
            return self._unavailable_apply_result("invalid_runtime_response")
        items = tuple(
            item
            for raw in raw_items
            if isinstance(raw, dict)
            and (item := self._logical_mapping_item(raw)) is not None
        )
        issues = tuple(
            item
            for raw in raw_issues
            if isinstance(raw, dict)
            and (item := self._logical_mapping_item(raw)) is not None
        )
        if len(items) != len(raw_items) or len(issues) != len(raw_issues):
            return self._unavailable_apply_result("invalid_runtime_items")
        return MappingApplyResult(
            status=status,
            items=items,
            issues=issues,
            evidence=dict(data.get("evidence") or {}),
        )

    @staticmethod
    def _logical_mapping_item(raw: dict[str, Any]) -> MappingItemResult | None:
        required_fields = {"target", "status", "retryable", "action"}
        allowed_fields = required_fields | {"source", "code", "mapping"}
        if (
            not required_fields.issubset(raw)
            or not set(raw).issubset(allowed_fields)
            or not isinstance(raw["target"], str)
            or not isinstance(raw["status"], str)
            or not isinstance(raw["retryable"], bool)
            or not isinstance(raw["action"], str)
            or (
                raw.get("source") is not None
                and not isinstance(raw["source"], str)
            )
            or (
                raw.get("code") is not None
                and not isinstance(raw["code"], str)
            )
        ):
            return None
        raw_mapping = raw.get("mapping")
        mapping: PoolSkillMapping | None = None
        if raw_mapping is not None:
            if not isinstance(raw_mapping, dict):
                return None
            corpus = raw_mapping.get("corpus")
            expected_fields = (
                {"corpus", "skill_uuid", "sc_version_number", "link_name"}
                if corpus == "center"
                else {"corpus", "relative_path", "link_name"}
            )
            if (
                corpus not in {"local", "repo", "center"}
                or set(raw_mapping) != expected_fields
                or any(
                    not isinstance(raw_mapping[field], str)
                    or not raw_mapping[field]
                    for field in expected_fields
                )
            ):
                return None
            try:
                mapping = PoolSkillMapping(
                    corpus=str(corpus),
                    relative_path=(
                        str(raw_mapping["relative_path"])
                        if raw_mapping.get("relative_path") is not None
                        else None
                    ),
                    link_name=str(raw_mapping["link_name"]),
                    skill_uuid=(
                        str(raw_mapping["skill_uuid"])
                        if raw_mapping.get("skill_uuid") is not None
                        else None
                    ),
                    sc_version_number=(
                        str(raw_mapping["sc_version_number"])
                        if raw_mapping.get("sc_version_number") is not None
                        else None
                    ),
                )
                mapping.to_dict()
            except (KeyError, TypeError, ValueError):
                return None
            if any(not value for value in mapping.to_dict().values()):
                return None
        try:
            status = MappingProjectionStatus(str(raw["status"]))
        except (KeyError, ValueError):
            return None
        action = raw["action"]
        if action not in {"APPLY", "RETIRE", "RUNTIME"}:
            return None
        return MappingItemResult(
            target=raw["target"],
            source=raw.get("source"),
            status=status,
            code=raw.get("code"),
            retryable=raw["retryable"],
            action=action,
            mapping=mapping,
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
        apply_mode: MappingApplyMode = MappingApplyMode.STRICT,
    ) -> MappingPublishResult:
        if not await self._ensure_center_mappings(
            bot_id=bot_id,
            user_id=user_id,
            mappings=mappings,
            mapping_contract_version=mapping_contract_version,
        ):
            return MappingPublishResult(
                published=False,
                status=MappingProjectionStatus.PENDING,
                evidence={"reason": "center_ensure_failed_before_mapping_publish"},
            )
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
                    "apply_mode": apply_mode.value,
                },
            )
        except Exception:
            logger.exception(
                "[skills_pool.runtime] mapping publish failed bot_id=%s",
                bot_id,
            )
            return MappingPublishResult(
                published=False,
                status=MappingProjectionStatus.PENDING,
                evidence={"reason": "runtime_mapping_publish_unavailable"},
            )
        data = response.get("data")
        result = self._mapping_publish_result(response=response, data=data)
        if not result.published:
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
        return result

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
        apply_mode: MappingApplyMode = MappingApplyMode.STRICT,
    ) -> MappingVerificationResult:
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
                    "apply_mode": apply_mode.value,
                },
            )
        except Exception:
            logger.exception(
                "[skills_pool.runtime] mapping verify failed bot_id=%s",
                bot_id,
            )
            return MappingVerificationResult(
                valid=False,
                status=MappingProjectionStatus.PENDING,
                evidence={"reason": "runtime_mapping_verify_unavailable"},
            )
        data = response.get("data")
        result = self._mapping_verification_result(response=response, data=data)
        if not result.valid:
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
        return result

    @staticmethod
    def _mapping_status(value: object, *, fallback: MappingProjectionStatus) -> MappingProjectionStatus:
        try:
            return MappingProjectionStatus(str(value))
        except ValueError:
            return fallback

    @staticmethod
    def _mapping_items(data: dict[str, Any]) -> tuple[MappingItemResult, ...]:
        items: list[MappingItemResult] = []
        for raw in data.get("items", []):
            if not isinstance(raw, dict):
                continue
            status = SkillsPoolRuntime._mapping_status(
                raw.get("status"),
                fallback=MappingProjectionStatus.DEGRADED,
            )
            items.append(
                MappingItemResult(
                    target=str(raw.get("target") or ""),
                    source=(
                        str(raw["source"])
                        if raw.get("source") is not None
                        else None
                    ),
                    status=status,
                    code=str(raw["code"]) if raw.get("code") is not None else None,
                    retryable=bool(raw.get("retryable")),
                    action=str(raw.get("action") or "APPLY"),
                )
            )
        return tuple(items)

    def _mapping_publish_result(
        self, *, response: dict[str, Any], data: object
    ) -> MappingPublishResult:
        if not isinstance(data, dict):
            return MappingPublishResult(
                published=False,
                status=MappingProjectionStatus.PENDING,
                evidence={"reason": "invalid_runtime_response"},
            )
        published = data.get("published") is True
        return MappingPublishResult(
            published=published,
            status=self._mapping_status(
                data.get("status"),
                fallback=(
                    MappingProjectionStatus.CONVERGED
                    if published
                    else MappingProjectionStatus.PENDING
                ),
            ),
            items=self._mapping_items(data),
            evidence=dict(data.get("evidence") or {}),
        )

    def _mapping_verification_result(
        self, *, response: dict[str, Any], data: object
    ) -> MappingVerificationResult:
        if not isinstance(data, dict):
            return MappingVerificationResult(
                valid=False,
                status=MappingProjectionStatus.PENDING,
                evidence={"reason": "invalid_runtime_response"},
            )
        valid = data.get("valid") is True
        return MappingVerificationResult(
            valid=valid,
            status=self._mapping_status(
                data.get("status"),
                fallback=(
                    MappingProjectionStatus.CONVERGED
                    if valid
                    else MappingProjectionStatus.PENDING
                ),
            ),
            items=self._mapping_items(data),
            evidence=dict(data.get("evidence") or {}),
        )

    async def _ensure_center_mappings(
        self,
        *,
        bot_id: str,
        user_id: str,
        mappings: Sequence[PoolSkillMapping],
        mapping_contract_version: str,
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
            )
        except Exception:
            logger.exception(
                "[skills_pool.runtime] center ensure failed bot_id=%s", bot_id
            )
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
    ) -> dict[str, Any]:
        context_started_at = time.perf_counter()
        try:
            context = self._resolver.resolve_for_bot(bot_id, user_id)
        except Exception:
            logger.info(
                "[skills_pool.runtime] timing stage=resolve_device_context "
                "bot_id=%s path=%s duration_ms=%.3f outcome=error",
                bot_id,
                path,
                (time.perf_counter() - context_started_at) * 1000,
            )
            raise
        logger.info(
            "[skills_pool.runtime] timing stage=resolve_device_context "
            "bot_id=%s path=%s duration_ms=%.3f outcome=success",
            bot_id,
            path,
            (time.perf_counter() - context_started_at) * 1000,
        )
        return await self._transport.invoke(
            context.conn_info,
            "POST",
            path,
            body=body,
            timeout=30.0,
        )


# Compatibility for callers introduced by the initial OpenClaw rollout.
OpenClawSkillsPoolRuntime = SkillsPoolRuntime


__all__ = [
    "OpenClawSkillsPoolRuntime",
    "SkillsPoolRuntime",
]
