"""Tests for desktop live-status merge in list_bots_by_owner_or_collaborator.

The frontend bot list calls /by-owner-or-collaborator. Desktop bots' DB status
lags, so the list directly consumes BaaS live status (read-only, no DB write).
Only desktop bots with a device_id are touched; non-desktop bots pass through;
per-bot failure degrades to the DB status; total failure leaves items unchanged.
"""
from unittest.mock import MagicMock, Mock

from agentclaw.community.core.bot_management.services.bot_service import BotService


def _make_bot_service(repository, device_status_client, teclaw_provision=None) -> BotService:
    teclaw_provision = teclaw_provision or MagicMock()
    return BotService(
        drm_reader=MagicMock(),
        repository=repository,
        allocation_config=MagicMock(),
        device_binding_repo=MagicMock(),
        skill_set_factory=MagicMock(),
        cleanup_service=MagicMock(),
        bcn_service=MagicMock(),
        bot_publish_repo=MagicMock(),
        passport_plugin=MagicMock(),
        oss_record_repo=MagicMock(),
        bot_publish_service_provider=lambda: MagicMock(),
        device_service_provider=lambda: MagicMock(),
        path_factory=MagicMock(),
        template_service=MagicMock(),
        workspace_hosting_service=MagicMock(),
        collaborator_repo=MagicMock(),
        restart_lock_repo=MagicMock(),
        teclaw_provision_service_provider=lambda: teclaw_provision,
        device_status_client=device_status_client,
        cron_auto_setup_service_provider=lambda: MagicMock(),
    )


def _repo_returning(items):
    repo = Mock()
    repo.list_by_owner_or_collaborator.return_value = (len(items), items)
    return repo


def _client_returning(**baas_data):
    c = MagicMock()
    c.query_device_status.return_value = baas_data
    return c


def _desktop(bot_id, status="OFFLINE", device_id="dev"):
    return {"bot_id": bot_id, "bot_type": "desktop", "device_id": device_id,
            "owner_id": "u", "status": status, "entity_id": "e"}


def _personal(bot_id, status="ACTIVE"):
    return {"bot_id": bot_id, "bot_type": "personal", "status": status,
            "owner_id": "u", "entity_id": "e"}


def _teclaw(bot_id, status="PENDING", binding_id=5):
    return {"bot_id": bot_id, "bot_type": "personal", "active_engine": "teclaw",
            "binding_id": binding_id, "owner_id": "u", "status": status, "entity_id": "e"}


class TestDesktopLiveStatusMerge:
    def test_desktop_status_overwritten_with_baas_live(self):
        items = [_desktop("d1", status="OFFLINE")]
        client = _client_returning(bot_status="ACTIVE", device_status="ALL_ONLINE")
        svc = _make_bot_service(_repo_returning(items), client)
        result = svc.list_bots_by_owner_or_collaborator(owner_id="u")
        assert result["items"][0]["status"] == "ACTIVE"

    def test_pending_trusts_backend_not_queried(self):
        # Process state: a PENDING bot (creating/restarting) is NOT yet reliably
        # reflected by BaaS — the container may be up while the process hasn't
        # reconnected (BaaS would say ALL_OFFLINE). Trust the backend status and
        # don't even query BaaS.
        items = [_desktop("d1", status="PENDING")]
        client = _client_returning(bot_status="ACTIVE", device_status="ALL_OFFLINE")
        svc = _make_bot_service(_repo_returning(items), client)
        result = svc.list_bots_by_owner_or_collaborator(owner_id="u")
        assert result["items"][0]["status"] == "PENDING"
        client.query_device_status.assert_not_called()

    def test_releasing_trusts_backend_not_queried(self):
        items = [_desktop("d1", status="RELEASING")]
        client = _client_returning(bot_status="ACTIVE", device_status="ALL_ONLINE")
        svc = _make_bot_service(_repo_returning(items), client)
        result = svc.list_bots_by_owner_or_collaborator(owner_id="u")
        assert result["items"][0]["status"] == "RELEASING"
        client.query_device_status.assert_not_called()

    def test_offline_steady_state_consumes_baas(self):
        # Steady state OFFLINE → consume BaaS live value (may flip to ACTIVE).
        items = [_desktop("d1", status="OFFLINE")]
        client = _client_returning(bot_status="ACTIVE", device_status="ALL_ONLINE")
        svc = _make_bot_service(_repo_returning(items), client)
        result = svc.list_bots_by_owner_or_collaborator(owner_id="u")
        assert result["items"][0]["status"] == "ACTIVE"

    def test_non_desktop_not_touched(self):
        items = [_personal("p1", status="ACTIVE")]
        client = _client_returning(bot_status="ACTIVE", device_status="ALL_OFFLINE")
        svc = _make_bot_service(_repo_returning(items), client)
        result = svc.list_bots_by_owner_or_collaborator(owner_id="u")
        assert result["items"][0]["status"] == "ACTIVE"
        client.query_device_status.assert_not_called()

    def test_desktop_without_device_id_skipped(self):
        items = [_desktop("d1", status="OFFLINE", device_id="")]
        client = _client_returning(bot_status="ACTIVE", device_status="ALL_ONLINE")
        svc = _make_bot_service(_repo_returning(items), client)
        result = svc.list_bots_by_owner_or_collaborator(owner_id="u")
        assert result["items"][0]["status"] == "OFFLINE"
        client.query_device_status.assert_not_called()

    def test_none_mapping_keeps_db_status(self):
        # BaaS PENDING → map_baas_to_display returns None → keep DB status.
        items = [_desktop("d1", status="ACTIVE")]
        client = _client_returning(bot_status="PENDING", device_status="ALL_OFFLINE")
        svc = _make_bot_service(_repo_returning(items), client)
        result = svc.list_bots_by_owner_or_collaborator(owner_id="u")
        assert result["items"][0]["status"] == "ACTIVE"

    def test_per_bot_failure_keeps_db_status(self):
        items = [_desktop("d1", status="OFFLINE")]
        client = MagicMock()
        client.query_device_status.side_effect = RuntimeError("boom")
        svc = _make_bot_service(_repo_returning(items), client)
        result = svc.list_bots_by_owner_or_collaborator(owner_id="u")
        assert result["items"][0]["status"] == "OFFLINE"


class TestResolveDesktopLiveStatus:
    """Unit tests for the shared single-bot resolver the list-merge and the
    upload gate both call. Covers every branch: who is queried, who is skipped,
    and how failures degrade."""

    def test_non_desktop_returns_none_without_query(self):
        # Cloud bot (personal/service) never consults BaaS — gate trusts DB.
        svc = _make_bot_service(MagicMock(), _client_returning(bot_status="ACTIVE"))
        assert svc.resolve_desktop_live_status(_personal("p1")) is None
        svc._device_status_client.query_device_status.assert_not_called()

    def test_no_device_id_returns_none_without_query(self):
        svc = _make_bot_service(MagicMock(), _client_returning(bot_status="ACTIVE"))
        assert svc.resolve_desktop_live_status(_desktop("d1", device_id="")) is None
        svc._device_status_client.query_device_status.assert_not_called()

    def test_pending_trusts_backend(self):
        svc = _make_bot_service(MagicMock(), _client_returning(bot_status="ACTIVE"))
        assert svc.resolve_desktop_live_status(_desktop("d1", status="PENDING")) is None
        svc._device_status_client.query_device_status.assert_not_called()

    def test_releasing_trusts_backend(self):
        svc = _make_bot_service(MagicMock(), _client_returning(bot_status="ACTIVE"))
        assert svc.resolve_desktop_live_status(_desktop("d1", status="RELEASING")) is None
        svc._device_status_client.query_device_status.assert_not_called()

    def test_steady_state_returns_baas_display(self):
        svc = _make_bot_service(
            MagicMock(), _client_returning(bot_status="ACTIVE", device_status="ALL_ONLINE")
        )
        assert svc.resolve_desktop_live_status(_desktop("d1", status="OFFLINE")) == "ACTIVE"
        svc._device_status_client.query_device_status.assert_called_once_with("dev")

    def test_baas_unmapped_status_returns_none(self):
        # BaaS PENDING → map returns None → caller keeps DB status.
        svc = _make_bot_service(
            MagicMock(), _client_returning(bot_status="PENDING", device_status="ALL_OFFLINE")
        )
        assert svc.resolve_desktop_live_status(_desktop("d1")) is None

    def test_baas_failure_returns_none(self):
        # Best-effort: any BaaS error → None, never propagates to the caller.
        client = MagicMock()
        client.query_device_status.side_effect = RuntimeError("boom")
        svc = _make_bot_service(MagicMock(), client)
        assert svc.resolve_desktop_live_status(_desktop("d1")) is None


class TestTeclawStatusNotMerged:
    """Teclaw status is no longer read through to baas on the list: the
    TeclawStatusReconciler persists the resolved status onto the stored column,
    so the DB value passes through unchanged and baas is never consulted here."""

    def test_teclaw_status_passes_through_unchanged(self):
        items = [_teclaw("t1", status="PENDING", binding_id=5)]
        teclaw = MagicMock()
        svc = _make_bot_service(_repo_returning(items), MagicMock(), teclaw_provision=teclaw)
        result = svc.list_bots_by_owner_or_collaborator(owner_id="u")
        # Stored status is authoritative — kept as-is, no overwrite.
        assert result["items"][0]["status"] == "PENDING"
        # No read-through: the list never probes baas for a teclaw bot.
        teclaw.get_live_status_by_binding_id.assert_not_called()

