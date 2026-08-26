"""Unit tests for ArcaScheduleAwareDeviceService — D-05 deep-mock rewrite.

The former integration suite (tests/integration/core/arca_ttl_renewal/
test_hooks.py) depended on the community bootstrap_init fixture plus a
real schedule table. Per RESEARCH.md Open Question 3, that suite is
replaced by these hermetic mock unit tests: the wrapper is built with a
mocked schedule repository and MagicMock parent dependencies; the
community super() methods are patched with canned DeviceResponse /
DestroyDeviceResponse results; get_current_env is patched to "test".
No bootstrap_init import, no database, no container.

Pins the exact INTG-01/INTG-02 semantics replicated by the wrapper:
- create: register(env, sandbox_id, source_table="baas_device",
  source_id, next_renew_at) once, next_renew_at = expiration - lead
  window (WR-02: half of default_ttl_minutes, 12h at the 1440 default),
  defensive try/except (failure never blocks device creation, CRITICAL
  + [arca_ttl_metrics] register_error=1 logged), ARCA/ttl guards.
- stop / destroy: set_status(env, source_table="baas_device",
  source_id, "STOPPED") for ARCA records, for_restart skip on destroy,
  no-op on missing / non-ARCA records, return values pass through.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from secbaas.community.api.device_manage import (
    ArcaCreationResult,
    DestroyDeviceResponse,
    DeviceResponse,
)
from secbaas.community.core.repository.arca_ttl import TtlRenewalScheduleRepository
from secbaas.community.core.service.device_manage import DefaultDeviceService
from secbaas.community.core.service.device_manage._device_service_arca_ttl import (
    ArcaScheduleAwareDeviceService,
)

LIFECYCLE_MODULE = (
    "secbaas.community.core.service.device_manage._device_service_arca_ttl"
)

_TTL_MS = 1750000000000


@pytest.fixture(autouse=True)
def _mock_env(monkeypatch):
    """Pin get_current_env to 'test' for every wrapper call.

    The wrapper binds the function at module import time, so the patch
    target is the wrapper module's own attribute (not env_utils).
    """
    monkeypatch.setattr(f"{LIFECYCLE_MODULE}.get_current_env", lambda: "test")


# ── Helpers ────────────────────────────────────────────────────────────


def _expiration_dt() -> datetime:
    # CR-01: the wrapper computes the register margin in fixed
    # Asia/Shanghai (+08:00) — the test expectation must share the +08:00
    # wall clock, not the host-local one.
    return datetime.fromtimestamp(_TTL_MS / 1000, tz=ZoneInfo("Asia/Shanghai")).replace(
        tzinfo=None
    )


def _make_service(
    default_ttl_minutes: int = 1440,
) -> tuple[ArcaScheduleAwareDeviceService, MagicMock, MagicMock]:
    """Build the wrapper with fully mocked schedule repo + parent deps.

    default_ttl_minutes feeds the config-derived register lead window
    (WR-02); the 1440 default keeps the former 12h semantics.

    Returns (service, mock_schedule_repo, mock_device_repository).
    """
    mock_schedule_repo = MagicMock(spec=TtlRenewalScheduleRepository)
    mock_device_repo = MagicMock()
    svc = ArcaScheduleAwareDeviceService(
        schedule_repo=mock_schedule_repo,
        default_ttl_minutes=default_ttl_minutes,
        paas_facade=MagicMock(),
        repository=mock_device_repo,
        device_template_service=MagicMock(),
        secret_plugin=MagicMock(),
        callback_handler=MagicMock(),
    )
    return svc, mock_schedule_repo, mock_device_repo


def _response(
    *,
    provider_type: str | None = "ARCA",
    ttl: int | None = _TTL_MS,
    provider_device_id: str = "sandbox-abc123",
    props_override: dict | None = None,
    status: str = "ACTIVE",
) -> DeviceResponse:
    """Canned DeviceResponse for a super() lifecycle call.

    provider_device_props is built from a real ArcaCreationResult.model_dump()
    — the exact payload shape the production chain persists (D-06: the
    community device service stores creation_result.model_dump() verbatim,
    with props.sandbox_id mirroring provider_device_id, and the restored
    field pair: the fixed +08:00 formatted string plus the ms-epoch
    integer). Use props_override
    to simulate a props dict that lacks the ttl_expiration_timestamp key
    entirely. status defaults to ACTIVE; pass "FAILED"/"PENDING" to
    simulate the destroy+create update outcomes.
    """
    if props_override is None:
        props: dict = ArcaCreationResult(
            platform="arca",
            status="ACTIVE",
            template_id="tpl-test-001",
            sandbox_id=provider_device_id,
            ttl_expiration_time="2025-06-15 23:06:40",
            ttl_expiration_timestamp=ttl,
        ).model_dump()
    else:
        props = props_override
    return DeviceResponse(
        id=1,
        device_uuid="DEVICE-test-001",
        tenant="test-tenant",
        env="test",
        domain="default",
        status=status,
        provider_type=provider_type,
        provider_device_id=provider_device_id,
        provider_device_props=props,
        creator="test-user",
        modifier="test-user",
        gmt_create=datetime(2026, 1, 1),
        gmt_modified=datetime(2026, 1, 1),
    )


def _destroy_response() -> DestroyDeviceResponse:
    return DestroyDeviceResponse(success=True)


def _record(*, provider_type: str = "ARCA", identity: int = 7) -> SimpleNamespace:
    """Record stub as returned by the device repository lookups."""
    return SimpleNamespace(
        id=identity,
        provider_type=provider_type,
        provider_device_id="sandbox-abc123",
    )


# ── start_device (INTG-01 register) ────────────────────────────────────


class TestPersistedTtlPairContract:
    def test_creation_result_dump_locks_persisted_ttl_pair_shape(self):
        """WR-01: contract lock for the provider_device_props payload the
        creation chain persists verbatim (creation_result.model_dump()).

        The persisted shape is the formatted '%Y-%m-%d %H:%M:%S' string in
        ttl_expiration_time plus the ms-epoch integer in
        ttl_expiration_timestamp. Both the legacy readers (string compare /
        log-only) and the dormant deadline dual-key reader depend on this
        exact pair; a regression back to the pre-Phase-5 int-only shape (or
        dropping either key from the dump) must fail here."""
        result = ArcaCreationResult(
            platform="arca",
            status="ACTIVE",
            template_id="tpl-test-001",
            sandbox_id="sandbox-abc123",
            ttl_expiration_time="2025-06-15 23:06:40",
            ttl_expiration_timestamp=_TTL_MS,
        )

        props = result.model_dump()

        assert "ttl_expiration_time" in props
        assert "ttl_expiration_timestamp" in props
        assert isinstance(props["ttl_expiration_time"], str)
        assert isinstance(props["ttl_expiration_timestamp"], int)
        assert props["ttl_expiration_time"] == "2025-06-15 23:06:40"
        assert props["ttl_expiration_timestamp"] == _TTL_MS


class TestStartDeviceHook:
    @pytest.mark.asyncio
    async def test_arca_create_registers_and_returns_same_response(self):
        svc, mock_schedule_repo, _ = _make_service()
        response = _response()

        with patch.object(
            DefaultDeviceService, "start_device", new=AsyncMock(return_value=response)
        ):
            result = await svc.start_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )

        # Pass-through by identity — never re-mapped or swallowed
        assert result is response
        expected_next = _expiration_dt() - timedelta(hours=12)
        mock_schedule_repo.register.assert_called_once_with(
            "test",
            sandbox_id="sandbox-abc123",
            source_table="baas_device",
            source_id=response.id,
            next_renew_at=expected_next,
        )

    @pytest.mark.asyncio
    async def test_register_window_derives_from_configured_ttl(self):
        """WR-02: the register lead window is half the configured TTL period
        (default_ttl_minutes=2880 -> expiry-24h), matching the scheduler's
        config-derived rule instead of a hardcoded 12h."""
        svc, mock_schedule_repo, _ = _make_service(default_ttl_minutes=2880)
        response = _response()

        with patch.object(
            DefaultDeviceService, "start_device", new=AsyncMock(return_value=response)
        ):
            result = await svc.start_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )

        assert result is response
        expected_next = _expiration_dt() - timedelta(hours=24)
        mock_schedule_repo.register.assert_called_once_with(
            "test",
            sandbox_id="sandbox-abc123",
            source_table="baas_device",
            source_id=response.id,
            next_renew_at=expected_next,
        )

    @pytest.mark.asyncio
    async def test_arca_create_ttl_none_skips_register(self):
        svc, mock_schedule_repo, _ = _make_service()
        response = _response(ttl=None)

        with patch.object(
            DefaultDeviceService, "start_device", new=AsyncMock(return_value=response)
        ):
            result = await svc.start_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )

        assert result is response
        mock_schedule_repo.register.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_arca_create_skips_register(self):
        svc, mock_schedule_repo, _ = _make_service()
        response = _response(provider_type="LOCAL")

        with patch.object(
            DefaultDeviceService, "start_device", new=AsyncMock(return_value=response)
        ):
            result = await svc.start_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )

        assert result is response
        mock_schedule_repo.register.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_register_failure_is_defensive(self, caplog):
        """INTG-01: register() raising must never block device creation."""
        svc, mock_schedule_repo, _ = _make_service()
        mock_schedule_repo.register.side_effect = RuntimeError("DB down")
        response = _response()

        with patch.object(
            DefaultDeviceService, "start_device", new=AsyncMock(return_value=response)
        ):
            with caplog.at_level(logging.INFO, logger="core-scheduler"):
                result = await svc.start_device(
                    tenant="test-tenant", device_uuid="DEVICE-test-001"
                )

        assert result is response
        critical = [
            r
            for r in caplog.records
            if r.levelno == logging.CRITICAL and "[arca_ttl]" in r.message
        ]
        metrics = [
            r
            for r in caplog.records
            if "[arca_ttl_metrics]" in r.message and "register_error=1" in r.message
        ]
        assert len(critical) == 1
        assert len(metrics) == 1

    @pytest.mark.asyncio
    async def test_arca_create_ttl_key_missing_skips_register_and_logs_warning(
        self, caplog
    ):
        """WR-03: a props dict missing the ttl_expiration_timestamp KEY (not just a
        None value) must skip register and log a warning — never silently."""
        svc, mock_schedule_repo, _ = _make_service()
        response = _response(props_override={"platform": "arca", "status": "ACTIVE"})

        with patch.object(
            DefaultDeviceService, "start_device", new=AsyncMock(return_value=response)
        ):
            with caplog.at_level(logging.WARNING, logger="core-scheduler"):
                result = await svc.start_device(
                    tenant="test-tenant", device_uuid="DEVICE-test-001"
                )

        assert result is response
        mock_schedule_repo.register.assert_not_called()
        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and "missing ttl_expiration_timestamp" in r.message
        ]
        assert len(warnings) == 1

    @pytest.mark.asyncio
    async def test_arca_create_ttl_zero_skips_register_and_logs_warning(self, caplog):
        """WR-02: ttl_expiration_timestamp == 0 is treated as missing — skip
        register and warn, never anchor the schedule at epoch 0 (1970 loop)."""
        svc, mock_schedule_repo, _ = _make_service()
        response = _response(
            props_override={
                "platform": "arca",
                "status": "ACTIVE",
                "ttl_expiration_timestamp": 0,
            }
        )

        with patch.object(
            DefaultDeviceService, "start_device", new=AsyncMock(return_value=response)
        ):
            with caplog.at_level(logging.WARNING, logger="core-scheduler"):
                result = await svc.start_device(
                    tenant="test-tenant", device_uuid="DEVICE-test-001"
                )

        assert result is response
        mock_schedule_repo.register.assert_not_called()
        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and "missing ttl_expiration_timestamp" in r.message
        ]
        assert len(warnings) == 1

    @pytest.mark.asyncio
    async def test_arca_create_legacy_ttl_time_fallback_registers(self):
        """WR-02: pre-release props persisted only the legacy integer-ms
        ttl_expiration_time key — the dual-key fallback reads it and
        registers with the real expiry instead of deferring to the
        discovery scan."""
        svc, mock_schedule_repo, _ = _make_service()
        response = _response(
            props_override={
                "platform": "arca",
                "status": "ACTIVE",
                "ttl_expiration_time": _TTL_MS,
            }
        )

        with patch.object(
            DefaultDeviceService, "start_device", new=AsyncMock(return_value=response)
        ):
            result = await svc.start_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )

        assert result is response
        expected_next = _expiration_dt() - timedelta(hours=12)
        mock_schedule_repo.register.assert_called_once_with(
            "test",
            sandbox_id="sandbox-abc123",
            source_table="baas_device",
            source_id=response.id,
            next_renew_at=expected_next,
        )


# ── stop_device_by_uuid (INTG-02 set_status) ───────────────────────────


class TestStopDeviceHook:
    @pytest.mark.asyncio
    async def test_stop_arca_sets_stopped_and_returns_same_result(self):
        svc, mock_schedule_repo, mock_device_repo = _make_service()
        mock_device_repo.get_active_or_updating_by_device_uuid.return_value = _record()
        destroy_response = _destroy_response()

        with patch.object(
            DefaultDeviceService,
            "stop_device_by_uuid",
            new=AsyncMock(return_value=destroy_response),
        ):
            result = await svc.stop_device_by_uuid(
                tenant="test-tenant", device_uuid="DEVICE-test-001", modifier="test"
            )

        assert result is destroy_response
        mock_schedule_repo.set_status.assert_called_once_with(
            "test", source_table="baas_device", source_id=7, status="STOPPED"
        )

    @pytest.mark.asyncio
    async def test_stop_record_not_found_skips_set_status(self):
        svc, mock_schedule_repo, mock_device_repo = _make_service()
        mock_device_repo.get_active_or_updating_by_device_uuid.return_value = None
        mock_device_repo.get_by_device_uuid_only.return_value = None
        destroy_response = _destroy_response()

        with patch.object(
            DefaultDeviceService,
            "stop_device_by_uuid",
            new=AsyncMock(return_value=destroy_response),
        ):
            result = await svc.stop_device_by_uuid(
                tenant="test-tenant", device_uuid="DEVICE-test-001", modifier="test"
            )

        assert result is destroy_response
        mock_schedule_repo.set_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_non_arca_skips_set_status(self):
        svc, mock_schedule_repo, mock_device_repo = _make_service()
        mock_device_repo.get_active_or_updating_by_device_uuid.return_value = _record(
            provider_type="LOCAL"
        )
        destroy_response = _destroy_response()

        with patch.object(
            DefaultDeviceService,
            "stop_device_by_uuid",
            new=AsyncMock(return_value=destroy_response),
        ):
            result = await svc.stop_device_by_uuid(
                tenant="test-tenant", device_uuid="DEVICE-test-001", modifier="test"
            )

        assert result is destroy_response
        mock_schedule_repo.set_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_set_status_failure_is_defensive(self, caplog):
        """WR-01: a cold-table failure after a successful stop must not raise —
        CRITICAL + [arca_ttl_metrics] set_status_error=1 only."""
        svc, mock_schedule_repo, mock_device_repo = _make_service()
        mock_device_repo.get_active_or_updating_by_device_uuid.return_value = _record()
        mock_schedule_repo.set_status.side_effect = RuntimeError("DB down")
        destroy_response = _destroy_response()

        with patch.object(
            DefaultDeviceService,
            "stop_device_by_uuid",
            new=AsyncMock(return_value=destroy_response),
        ):
            with caplog.at_level(logging.INFO, logger="core-scheduler"):
                result = await svc.stop_device_by_uuid(
                    tenant="test-tenant", device_uuid="DEVICE-test-001", modifier="test"
                )

        assert result is destroy_response
        critical = [
            r
            for r in caplog.records
            if r.levelno == logging.CRITICAL and "[arca_ttl]" in r.message
        ]
        metrics = [
            r
            for r in caplog.records
            if "[arca_ttl_metrics]" in r.message and "set_status_error=1" in r.message
        ]
        assert len(critical) == 1
        assert len(metrics) == 1

    @pytest.mark.asyncio
    async def test_stop_failed_result_skips_set_status(self):
        """WR-03: when the underlying stop FAILED (success=False) the row
        must stay ACTIVE — a still-live container keeps renewing."""
        svc, mock_schedule_repo, mock_device_repo = _make_service()
        mock_device_repo.get_active_or_updating_by_device_uuid.return_value = _record()
        destroy_response = DestroyDeviceResponse(success=False)

        with patch.object(
            DefaultDeviceService,
            "stop_device_by_uuid",
            new=AsyncMock(return_value=destroy_response),
        ):
            result = await svc.stop_device_by_uuid(
                tenant="test-tenant", device_uuid="DEVICE-test-001", modifier="test"
            )

        assert result is destroy_response
        mock_schedule_repo.set_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_none_result_skips_set_status(self):
        """WR-03: a None result (no success signal) must not mark STOPPED."""
        svc, mock_schedule_repo, mock_device_repo = _make_service()
        mock_device_repo.get_active_or_updating_by_device_uuid.return_value = _record()

        with patch.object(
            DefaultDeviceService,
            "stop_device_by_uuid",
            new=AsyncMock(return_value=None),
        ):
            result = await svc.stop_device_by_uuid(
                tenant="test-tenant", device_uuid="DEVICE-test-001", modifier="test"
            )

        assert result is None
        mock_schedule_repo.set_status.assert_not_called()


# ── destroy_device_by_uuid (INTG-02 set_status, for_restart guard) ─────


class TestDestroyDeviceHook:
    @pytest.mark.asyncio
    async def test_destroy_for_restart_skips_set_status(self):
        svc, mock_schedule_repo, mock_device_repo = _make_service()
        mock_device_repo.get_active_or_updating_by_device_uuid.return_value = _record()
        destroy_response = _destroy_response()

        with patch.object(
            DefaultDeviceService,
            "destroy_device_by_uuid",
            new=AsyncMock(return_value=destroy_response),
        ):
            result = await svc.destroy_device_by_uuid(
                tenant="test-tenant",
                device_uuid="DEVICE-test-001",
                modifier="test",
                for_restart=True,
            )

        assert result is destroy_response
        mock_schedule_repo.set_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_destroy_arca_sets_stopped_and_returns_same_result(self):
        svc, mock_schedule_repo, mock_device_repo = _make_service()
        mock_device_repo.get_active_or_updating_by_device_uuid.return_value = _record()
        destroy_response = _destroy_response()

        with patch.object(
            DefaultDeviceService,
            "destroy_device_by_uuid",
            new=AsyncMock(return_value=destroy_response),
        ):
            result = await svc.destroy_device_by_uuid(
                tenant="test-tenant",
                device_uuid="DEVICE-test-001",
                modifier="test",
                for_restart=False,
            )

        assert result is destroy_response
        mock_schedule_repo.set_status.assert_called_once_with(
            "test", source_table="baas_device", source_id=7, status="STOPPED"
        )

    @pytest.mark.asyncio
    async def test_destroy_failed_result_skips_set_status(self):
        """WR-03: a failed (success=False) destroy leaves the row ACTIVE —
        do not kill renewal for a container the destroy did not remove."""
        svc, mock_schedule_repo, mock_device_repo = _make_service()
        mock_device_repo.get_active_or_updating_by_device_uuid.return_value = _record()
        destroy_response = DestroyDeviceResponse(success=False)

        with patch.object(
            DefaultDeviceService,
            "destroy_device_by_uuid",
            new=AsyncMock(return_value=destroy_response),
        ):
            result = await svc.destroy_device_by_uuid(
                tenant="test-tenant",
                device_uuid="DEVICE-test-001",
                modifier="test",
                for_restart=False,
            )

        assert result is destroy_response
        mock_schedule_repo.set_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_destroy_set_status_failure_is_defensive(self, caplog):
        """WR-01: a cold-table failure after a successful destroy must not
        raise — CRITICAL + set_status_error=1 metric only."""
        svc, mock_schedule_repo, mock_device_repo = _make_service()
        mock_device_repo.get_active_or_updating_by_device_uuid.return_value = _record()
        mock_schedule_repo.set_status.side_effect = RuntimeError("DB down")
        destroy_response = _destroy_response()

        with patch.object(
            DefaultDeviceService,
            "destroy_device_by_uuid",
            new=AsyncMock(return_value=destroy_response),
        ):
            with caplog.at_level(logging.INFO, logger="core-scheduler"):
                result = await svc.destroy_device_by_uuid(
                    tenant="test-tenant",
                    device_uuid="DEVICE-test-001",
                    modifier="test",
                    for_restart=False,
                )

        assert result is destroy_response
        critical = [
            r
            for r in caplog.records
            if r.levelno == logging.CRITICAL and "[arca_ttl]" in r.message
        ]
        metrics = [
            r
            for r in caplog.records
            if "[arca_ttl_metrics]" in r.message and "set_status_error=1" in r.message
        ]
        assert len(critical) == 1
        assert len(metrics) == 1


# ── update_device (WR-02: ARCA destroy+create sandbox swap re-sync) ─────


class TestUpdateDeviceHook:
    @pytest.mark.asyncio
    async def test_update_arca_registers_new_sandbox(self):
        """WR-02: ARCA update (destroy+create) with a swapped sandbox_id
        re-registers the schedule row with the new sandbox immediately."""
        svc, mock_schedule_repo, _ = _make_service()
        response = _response(provider_device_id="sandbox-new-456")

        with patch.object(
            DefaultDeviceService, "update_device", new=AsyncMock(return_value=response)
        ):
            result = await svc.update_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )

        assert result is response
        expected_next = _expiration_dt() - timedelta(hours=12)
        mock_schedule_repo.register.assert_called_once_with(
            "test",
            sandbox_id="sandbox-new-456",
            source_table="baas_device",
            source_id=response.id,
            next_renew_at=expected_next,
        )

    @pytest.mark.asyncio
    async def test_restart_arca_registers_via_update_delegation(self):
        """WR-02: community restart_device delegates to update_device, so a
        restart registers exactly once (no double register)."""
        svc, mock_schedule_repo, _ = _make_service()
        response = _response(provider_device_id="sandbox-new-456")

        async def _delegate(*args, **kwargs):
            # Simulate the community restart → update dispatch (self.update_device).
            return await svc.update_device(*args, **kwargs)

        with patch.object(
            DefaultDeviceService, "update_device", new=AsyncMock(return_value=response)
        ):
            with patch.object(
                DefaultDeviceService,
                "restart_device",
                new=AsyncMock(side_effect=_delegate),
            ):
                result = await svc.restart_device(
                    tenant="test-tenant", device_uuid="DEVICE-test-001", modifier="test"
                )

        assert result is response
        expected_next = _expiration_dt() - timedelta(hours=12)
        mock_schedule_repo.register.assert_called_once_with(
            "test",
            sandbox_id="sandbox-new-456",
            source_table="baas_device",
            source_id=response.id,
            next_renew_at=expected_next,
        )

    @pytest.mark.asyncio
    async def test_update_register_failure_is_defensive(self, caplog):
        """WR-02: register failure after a successful update never raises."""
        svc, mock_schedule_repo, _ = _make_service()
        mock_schedule_repo.register.side_effect = RuntimeError("DB down")
        response = _response()

        with patch.object(
            DefaultDeviceService, "update_device", new=AsyncMock(return_value=response)
        ):
            with caplog.at_level(logging.INFO, logger="core-scheduler"):
                result = await svc.update_device(
                    tenant="test-tenant", device_uuid="DEVICE-test-001"
                )

        assert result is response
        critical = [
            r
            for r in caplog.records
            if r.levelno == logging.CRITICAL and "[arca_ttl]" in r.message
        ]
        metrics = [
            r
            for r in caplog.records
            if "[arca_ttl_metrics]" in r.message and "register_error=1" in r.message
        ]
        assert len(critical) == 1
        assert len(metrics) == 1

    @pytest.mark.asyncio
    async def test_update_non_arca_skips_register(self):
        svc, mock_schedule_repo, _ = _make_service()
        response = _response(provider_type="LOCAL")

        with patch.object(
            DefaultDeviceService, "update_device", new=AsyncMock(return_value=response)
        ):
            result = await svc.update_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )

        assert result is response
        mock_schedule_repo.register.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_failed_status_skips_register(self):
        """WR-03: when the ARCA destroy+create update's create phase FAILED,
        the community service returns status='FAILED' with the OLD (destroyed)
        provider_device_id. The wrapper must NOT re-register — the register
        upsert would resurrect a stale ACTIVE cold row for a dead sandbox."""
        svc, mock_schedule_repo, _ = _make_service()
        response = _response(
            status="FAILED", provider_device_id="sandbox-old-destroyed"
        )

        with patch.object(
            DefaultDeviceService, "update_device", new=AsyncMock(return_value=response)
        ):
            result = await svc.update_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )

        assert result is response
        mock_schedule_repo.register.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_pending_status_still_registers(self):
        """WR-03 guard must not regress the WR-02 rotation hardening: a
        successful ARCA destroy+create returns status='PENDING' (DB written
        as PENDING right after create) and MUST still re-register the new
        sandbox."""
        svc, mock_schedule_repo, _ = _make_service()
        response = _response(status="PENDING", provider_device_id="sandbox-new-456")

        with patch.object(
            DefaultDeviceService, "update_device", new=AsyncMock(return_value=response)
        ):
            result = await svc.update_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )

        assert result is response
        expected_next = _expiration_dt() - timedelta(hours=12)
        mock_schedule_repo.register.assert_called_once_with(
            "test",
            sandbox_id="sandbox-new-456",
            source_table="baas_device",
            source_id=response.id,
            next_renew_at=expected_next,
        )
