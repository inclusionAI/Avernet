"""ArcaScheduleAwareDeviceService — ARCA schedule-table hooks for device lifecycle.

Wraps the community DefaultDeviceService and applies the INTG-01/INTG-02
hook semantics via a subclass instead of inline hooks in the community
_device_service.py core path (D-08'/D-09'):

- start_device() — after a successful create, register the ARCA container
  in the baas_bot_ttl_renewal_schedule cold table (defensive posture:
  never blocks device creation).
- stop_device_by_uuid() / destroy_device_by_uuid() — after a successful
  stop/destroy (DestroyDeviceResponse.success is True), mark the schedule
  row STOPPED. A failed destroy leaves the row ACTIVE so renewal is never
  killed for a container that may still be live (defensive posture, same
  as start: a cold-table failure never fails the already-completed device
  operation; it only emits CRITICAL + set_status_error=1 metrics).

The wrapper is injected by the community bootstrap as a Singleton override
for device_service only when ``config.renewal_scheduler.engine ==
"deadline"`` (D-08' engine switch); under the legacy engine the native
DefaultDeviceService runs and the cold table is never written.

Known divergence from the former inline hooks: register now happens after
start_device returns instead of before the final status write — the
discovery scan is the safety net for that window.
"""

from __future__ import annotations

from secbaas.community.core.repository.arca_ttl import TtlRenewalScheduleRepository
from secbaas.community.core.utils.env_utils import get_current_env
from secbaas.community.core.utils.time_utils import (
    naive_cst_fromtimestamp,
    renewal_window,
)
from secbaas.community.logger import get_logger

from ._device_service import (
    DefaultDeviceService,
)

log = get_logger("core-scheduler")


class ArcaScheduleAwareDeviceService(DefaultDeviceService):
    """Decorate community device lifecycle with ARCA schedule-table hooks.

    Mirrors the former INTG-01/INTG-02 hook semantics that lived in
    community _device_service.py before the enterprise namespace was
    withdrawn from the community tree (Route A). Guard replication:
    ARCA type guard, provider_device_id truthiness, for_restart skip on
    destroy, success guard on stop/destroy results, and no schedule
    action for non-ARCA devices.
    """

    def __init__(
        self,
        schedule_repo: TtlRenewalScheduleRepository,
        default_ttl_minutes: int = 1440,
        *args,
        **kwargs,
    ) -> None:
        self._schedule_repo = schedule_repo
        # WR-02: the register target's lead window follows the same
        # config-derived rule as the scheduler (half of the configured TTL
        # period) instead of a hardcoded 12h — the container wires this
        # from config.arca.default_ttl_minutes; the 1440 default keeps
        # direct construction (tests, non-DI callers) on the former 12h
        # semantics.
        self._renewal_window = renewal_window(default_ttl_minutes)
        super().__init__(*args, **kwargs)

    def _try_register_schedule(self, response, device_uuid) -> None:
        """Best-effort register/re-register of the ARCA schedule row.

        Reads ttl_expiration_timestamp (ms epoch) from provider_device_props — the exact
        creation_result.model_dump() payload the community device service
        persists (D-06). WR-02 dual-key fallback: devices created before
        the field-pair release persist only the legacy integer-ms
        ttl_expiration_time key, so that key is read as a fallback and
        pre-release creations register with their real expiry. A
        missing/zero/non-numeric value logs a warning and defers
        registration to the discovery scan; any repository failure only
        emits CRITICAL + [arca_ttl_metrics] register_error=1. Never raises
        (INTG-01 defensive posture: cold-table problems must never affect
        the device operation).
        """
        try:
            props = response.provider_device_props or {}
            ttl_ms = props.get("ttl_expiration_timestamp")
            if not ttl_ms:
                # WR-02: pre-release rows persisted only the legacy
                # integer-ms ttl_expiration_time key.
                ttl_ms = props.get("ttl_expiration_time")
            if not ttl_ms:
                log.warning(
                    "[arca_ttl] provider_device_props missing ttl_expiration_timestamp "
                    "for device %s — registration deferred to discovery scan",
                    device_uuid,
                )
                return
            # Numeric contract: the persisted value is an int ms epoch, but
            # numeric strings are possible — coerce with the same
            # int(float()) contract the scheduler consumers use; an
            # unparseable value defers to the discovery scan.
            try:
                ttl_ms = int(float(ttl_ms))
            except (TypeError, ValueError, OverflowError):
                log.warning(
                    "[arca_ttl] provider_device_props non-numeric TTL for "
                    "device %s — registration deferred to discovery scan",
                    device_uuid,
                )
                return
            expiration_dt = naive_cst_fromtimestamp(ttl_ms / 1000)
            self._schedule_repo.register(
                get_current_env(),
                sandbox_id=response.provider_device_id,
                source_table="baas_device",
                source_id=response.id,
                next_renew_at=expiration_dt - self._renewal_window,
            )
        except Exception:
            log.critical(
                "[arca_ttl] Failed to register schedule for device %s: "
                "sandbox_id=%s, source_id=%s",
                device_uuid,
                response.provider_device_id,
                response.id,
                exc_info=True,
            )
            log.info(
                "[arca_ttl_metrics] register_error=1 device_uuid=%s",
                device_uuid,
            )

    async def start_device(
        self, tenant, device_uuid, modifier="system", publish_id=None
    ):
        response = await super().start_device(tenant, device_uuid, modifier, publish_id)
        if response.provider_type == "ARCA" and response.provider_device_id:
            self._try_register_schedule(response, device_uuid)
        return response

    async def update_device(
        self, tenant, device_uuid, modifier="system", publish_id=None
    ):
        """Update device configuration and re-sync the ARCA schedule row.

        ARCA has no native restart API: community restart_device() delegates
        to this method (destroy + create), producing a NEW sandbox_id while
        keeping the same record.id (source_id). The register upsert on
        uk_source (env, source_table, source_id) overwrites sandbox_id with
        the new value and resurrects the row to ACTIVE, so one register call
        after super() fully re-syncs the cold table — the old-sandbox row
        cannot linger. restart_device is deliberately NOT overridden: it
        dispatches here dynamically, and overriding it as well would
        double-register.

        Status guard: when the destroy+create FAILS, the community service
        returns a DeviceResponse with status FAILED and the OLD, already
        destroyed provider_device_id. Re-registering that response would
        resurrect the cold row to ACTIVE with a dead sandbox_id and reset
        renew_fail_count, so registration only happens for a successful
        update (ACTIVE/PENDING).
        """
        response = await super().update_device(
            tenant, device_uuid, modifier, publish_id
        )
        if (
            response.provider_type == "ARCA"
            and response.provider_device_id
            and response.status in ("ACTIVE", "PENDING")
        ):
            self._try_register_schedule(response, device_uuid)
        return response

    async def stop_device_by_uuid(self, tenant, device_uuid, modifier):
        record = self._repository.get_active_or_updating_by_device_uuid(
            device_uuid, tenant=tenant, env=get_current_env()
        )
        if not record:
            record = self._repository.get_by_device_uuid_only(device_uuid=device_uuid)
        result = await super().stop_device_by_uuid(tenant, device_uuid, modifier)
        if (
            result
            and getattr(result, "success", True)
            and record
            and record.provider_type == "ARCA"
            and record.provider_device_id
        ):
            try:
                self._schedule_repo.set_status(
                    get_current_env(),
                    source_table="baas_device",
                    source_id=record.id,
                    status="STOPPED",
                    stop_reason="lifecycle",
                )
            except Exception:
                log.critical(
                    "[arca_ttl] Failed to set_status [STOPPED] for device %s: "
                    "source_id=%s",
                    device_uuid,
                    record.id,
                    exc_info=True,
                )
                log.info(
                    "[arca_ttl_metrics] set_status_error=1 device_uuid=%s",
                    device_uuid,
                )
        return result

    async def destroy_device_by_uuid(
        self, tenant, device_uuid, modifier, for_restart=False
    ):
        record = self._repository.get_active_or_updating_by_device_uuid(
            device_uuid, tenant=tenant, env=get_current_env()
        )
        result = await super().destroy_device_by_uuid(
            tenant, device_uuid, modifier, for_restart
        )
        if (
            not for_restart
            and result
            and getattr(result, "success", True)
            and record
            and record.provider_type == "ARCA"
            and record.provider_device_id
        ):
            try:
                self._schedule_repo.set_status(
                    get_current_env(),
                    source_table="baas_device",
                    source_id=record.id,
                    status="STOPPED",
                    stop_reason="lifecycle",
                )
            except Exception:
                log.critical(
                    "[arca_ttl] Failed to set_status [STOPPED] for device %s: "
                    "source_id=%s",
                    device_uuid,
                    record.id,
                    exc_info=True,
                )
                log.info(
                    "[arca_ttl_metrics] set_status_error=1 device_uuid=%s",
                    device_uuid,
                )
        return result
