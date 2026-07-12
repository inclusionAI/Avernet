"""End-to-end tests for /api/internal/dormant/* via FastAPI TestClient."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, InstanceProvider, singleton

from agentclaw.community.adapters.http.bot_dormant import router as dormant_router_module
from agentclaw.community.adapters.http.bot_dormant.auth import verify_dormant_internal_token
from agentclaw.community.core.bot_dormant.activate_service import ActivateBotService, InvalidBotStateError
from agentclaw.community.core.bot_dormant.internal_service import DormantInternalService
from agentclaw.community.core.bot_dormant.ops_service import DormantOpsService
from agentclaw.community.core.bot_dormant.service import DormantBotService


def _build_app(
    svc: DormantInternalService | None = None,
    bot_svc: DormantBotService | None = None,
    ops_svc: DormantOpsService | None = None,
    activate_svc: ActivateBotService | None = None,
) -> TestClient:
    """Build a minimal FastAPI app with only the internal router mounted.

    - Auth is bypassed via dependency_overrides on verify_dormant_internal_token.
    - DormantInternalService / DormantBotService / ops services are supplied
      via an Injector bound to the mocks.
    """
    app = FastAPI()
    app.include_router(dormant_router_module.internal_router)

    # Bypass auth: replace the async dep with a no-op lambda.
    app.dependency_overrides[verify_dormant_internal_token] = (
        lambda authorization=None: None
    )

    # Wire mock services into fastapi_injector so Injected(...) resolves.
    injector = Injector()
    if svc is None:
        svc = MagicMock(spec=DormantInternalService)
    injector.binder.bind(
        DormantInternalService, InstanceProvider(svc), scope=singleton
    )
    if bot_svc is None:
        bot_svc = MagicMock(spec=DormantBotService)
        bot_svc.is_dry_run = MagicMock(return_value=True)
        bot_svc.process_run = AsyncMock()
    injector.binder.bind(
        DormantBotService, InstanceProvider(bot_svc), scope=singleton
    )
    if ops_svc is None:
        ops_svc = MagicMock(spec=DormantOpsService)
    injector.binder.bind(
        DormantOpsService, InstanceProvider(ops_svc), scope=singleton
    )
    if activate_svc is None:
        activate_svc = MagicMock(spec=ActivateBotService)
    injector.binder.bind(
        ActivateBotService, InstanceProvider(activate_svc), scope=singleton
    )
    attach_injector(app, injector)

    return TestClient(app)


@pytest.mark.unit
def test_get_pending_returns_data():
    svc = MagicMock(spec=DormantInternalService)
    svc.list_pending.return_value = [
        {
            "id": 7, "bot_id": "b", "entity_id": "e",
            "notify_target": "staff1", "notify_type": "warn",
            "notify_source": "internal_scan",
            "content": "hello", "enqueued_at": "2026-06-23T03:00:00",
        }
    ]
    client = _build_app(svc)
    r = client.get(
        "/api/internal/dormant/pending-notifications",
        headers={"Authorization": "Bearer test-tok"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["data"][0]["id"] == 7
    svc.list_pending.assert_called_once_with(limit=200, dt=None, include_dry_run=False)


@pytest.mark.unit
def test_get_pending_passes_query_params():
    svc = MagicMock(spec=DormantInternalService)
    svc.list_pending.return_value = []
    client = _build_app(svc)
    r = client.get(
        "/api/internal/dormant/pending-notifications?limit=5&dt=20260623",
        headers={"Authorization": "Bearer test-tok"},
    )
    assert r.status_code == 200
    svc.list_pending.assert_called_once_with(limit=5, dt="20260623", include_dry_run=False)


@pytest.mark.unit
def test_get_pending_include_dry_run_param():
    """?include_dry_run=true forwards through to the service (pre/dev only)."""
    svc = MagicMock(spec=DormantInternalService)
    svc.list_pending.return_value = []
    client = _build_app(svc)
    r = client.get(
        "/api/internal/dormant/pending-notifications?include_dry_run=true",
        headers={"Authorization": "Bearer test-tok"},
    )
    assert r.status_code == 200
    svc.list_pending.assert_called_once_with(limit=200, dt=None, include_dry_run=True)


@pytest.mark.unit
def test_post_mark_sent_success():
    svc = MagicMock(spec=DormantInternalService)
    svc.mark_sent.return_value = "sent"
    client = _build_app(svc)
    r = client.post(
        "/api/internal/dormant/mark-sent",
        json={"id": 5, "success": True},
        headers={"Authorization": "Bearer test-tok"},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "status": "sent"}


@pytest.mark.unit
def test_post_mark_sent_failure_needs_error_msg():
    svc = MagicMock(spec=DormantInternalService)
    svc.mark_sent.side_effect = ValueError("error_msg is required when success=False")
    client = _build_app(svc)
    r = client.post(
        "/api/internal/dormant/mark-sent",
        json={"id": 5, "success": False},
        headers={"Authorization": "Bearer test-tok"},
    )
    assert r.status_code == 400


@pytest.mark.unit
def test_post_mark_sent_not_found_returns_404():
    svc = MagicMock(spec=DormantInternalService)
    svc.mark_sent.return_value = "not_found"
    client = _build_app(svc)
    r = client.post(
        "/api/internal/dormant/mark-sent",
        json={"id": 999, "success": True},
        headers={"Authorization": "Bearer test-tok"},
    )
    assert r.status_code == 404


@pytest.mark.unit
def test_post_mark_sent_already_resolved_is_idempotent():
    svc = MagicMock(spec=DormantInternalService)
    svc.mark_sent.return_value = "already_resolved"
    client = _build_app(svc)
    r = client.post(
        "/api/internal/dormant/mark-sent",
        json={"id": 5, "success": True},
        headers={"Authorization": "Bearer test-tok"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "already_resolved"


# ---------------------------------------------------------------------------
# /trigger-scan — fire-and-forget
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_trigger_scan_returns_immediately_with_run_id():
    """trigger-scan must return immediately, NOT await process_run, so
    Tengine's 60s gateway timeout doesn't fire on long scans (5000+ candidates)."""
    bot_svc = MagicMock(spec=DormantBotService)
    bot_svc.is_dry_run = MagicMock(return_value=True)
    bot_svc.process_run = AsyncMock()

    client = _build_app(bot_svc=bot_svc)
    r = client.post(
        "/api/internal/dormant/trigger-scan",
        headers={"Authorization": "Bearer test-tok"},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["dry_run"] is True
    # run_id is a UUID; verify shape only
    assert isinstance(body["run_id"], str) and len(body["run_id"]) == 36
    # Message hints how to track progress
    assert "dormant.run=" in body["message"]


@pytest.mark.unit
def test_trigger_scan_explicit_dry_run_override():
    """Query param dry_run=false should override service.is_dry_run() default."""
    bot_svc = MagicMock(spec=DormantBotService)
    bot_svc.is_dry_run = MagicMock(return_value=True)  # config says dry_run=True
    bot_svc.process_run = AsyncMock()

    client = _build_app(bot_svc=bot_svc)
    r = client.post(
        "/api/internal/dormant/trigger-scan?dry_run=false",
        headers={"Authorization": "Bearer test-tok"},
    )

    assert r.status_code == 200
    assert r.json()["dry_run"] is False


@pytest.mark.unit
def test_trigger_scan_background_crash_does_not_propagate():
    """If process_run blows up in the background task, the user-facing
    response was already returned — the worker must not crash. Outer
    _run_in_background swallows + logs the exception."""
    bot_svc = MagicMock(spec=DormantBotService)
    bot_svc.is_dry_run = MagicMock(return_value=True)
    bot_svc.process_run = AsyncMock(side_effect=RuntimeError("simulated"))

    client = _build_app(bot_svc=bot_svc)
    r = client.post(
        "/api/internal/dormant/trigger-scan",
        headers={"Authorization": "Bearer test-tok"},
    )

    # The HTTP response is sent before process_run is awaited, so even
    # though the task will crash, the user still gets 200.
    assert r.status_code == 200


@pytest.mark.unit
def test_recycle_one_forwards_to_manual_recycle_service():
    """ops recycle-one should run one bot through the dormant recycle chain."""
    ops_svc = MagicMock(spec=DormantOpsService)
    ops_svc.recycle_one.return_value = {
        "run_id": "ops-recycle-1",
        "bot_id": "b1",
        "owner_id": "u1",
        "dry_run": False,
        "status": "recycled",
    }

    client = _build_app(ops_svc=ops_svc)
    r = client.post(
        "/api/internal/dormant/recycle-one",
        json={
            "bot_id": "b1",
            "owner_id": "u1",
            "dry_run": False,
            "reason": "prepub regression",
        },
        headers={"Authorization": "Bearer test-tok"},
    )

    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["data"]["status"] == "recycled"
    ops_svc.recycle_one.assert_called_once_with(
        bot_id="b1",
        owner_id="u1",
        dry_run=False,
        reason="prepub regression",
    )


@pytest.mark.unit
def test_recycle_one_returns_400_for_rejected_bot():
    """ops recycle-one should surface domain validation as a 400."""
    ops_svc = MagicMock(spec=DormantOpsService)
    ops_svc.recycle_one.side_effect = ValueError("only ACTIVE bot can be manually recycled")

    client = _build_app(ops_svc=ops_svc)
    r = client.post(
        "/api/internal/dormant/recycle-one",
        json={"bot_id": "b1", "owner_id": "u1"},
        headers={"Authorization": "Bearer test-tok"},
    )

    assert r.status_code == 400
    assert "only ACTIVE" in r.json()["detail"]


@pytest.mark.unit
def test_recycle_one_returns_500_for_unexpected_error():
    """Unexpected ops failures should be logged and returned as a 500."""
    ops_svc = MagicMock(spec=DormantOpsService)
    ops_svc.recycle_one.side_effect = RuntimeError("passport unavailable")

    client = _build_app(ops_svc=ops_svc)
    r = client.post(
        "/api/internal/dormant/recycle-one",
        json={"bot_id": "b1", "owner_id": "u1"},
        headers={"Authorization": "Bearer test-tok"},
    )

    assert r.status_code == 500
    assert "passport unavailable" in r.json()["detail"]


@pytest.mark.unit
def test_unfreeze_passport_one_forwards_audit_reason():
    """Passport-only ops must not enter the full bot activation flow."""
    ops_svc = MagicMock(spec=DormantOpsService)
    ops_svc.unfreeze_passport_one.return_value = {
        "bot_id": "default",
        "owner_id": "37565",
        "status": "passport_online",
    }
    activate_svc = MagicMock(spec=ActivateBotService)
    client = _build_app(ops_svc=ops_svc, activate_svc=activate_svc)

    response = client.post(
        "/api/internal/dormant/unfreeze-passport-one",
        json={
            "bot_id": "default",
            "owner_id": "37565",
            "reason": "recover license",
        },
        headers={"Authorization": "Bearer test-tok"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "data": {
            "bot_id": "default",
            "owner_id": "37565",
            "status": "passport_online",
        },
    }
    ops_svc.unfreeze_passport_one.assert_called_once_with(
        bot_id="default",
        owner_id="37565",
        reason="recover license",
    )
    activate_svc.activate.assert_not_called()


@pytest.mark.unit
def test_unfreeze_passport_one_returns_500_for_passport_error():
    ops_svc = MagicMock(spec=DormantOpsService)
    ops_svc.unfreeze_passport_one.side_effect = RuntimeError(
        "passport unavailable"
    )
    client = _build_app(ops_svc=ops_svc)

    response = client.post(
        "/api/internal/dormant/unfreeze-passport-one",
        json={
            "bot_id": "default",
            "owner_id": "37565",
            "reason": "recover",
        },
        headers={"Authorization": "Bearer test-tok"},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "passport unavailable"


@pytest.mark.unit
@pytest.mark.parametrize("field", ["bot_id", "owner_id", "reason"])
def test_unfreeze_passport_one_rejects_blank_fields(field: str):
    payload = {
        "bot_id": "default",
        "owner_id": "37565",
        "reason": "recover",
    }
    payload[field] = "   "
    client = _build_app()

    response = client.post(
        "/api/internal/dormant/unfreeze-passport-one",
        json=payload,
        headers={"Authorization": "Bearer test-tok"},
    )

    assert response.status_code == 422


@pytest.mark.unit
def test_activate_one_forwards_to_activate_service():
    """ops activate-one should reuse the normal RECYCLED bot activation path."""
    activate_svc = MagicMock(spec=ActivateBotService)
    activate_svc.activate.return_value = {
        "status": "REACTIVATING",
        "message": "激活中",
    }

    client = _build_app(activate_svc=activate_svc)
    r = client.post(
        "/api/internal/dormant/activate-one",
        json={"bot_id": "b1", "owner_id": "u1", "nick_name": "ops"},
        headers={"Authorization": "Bearer test-tok"},
    )

    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["data"]["status"] == "REACTIVATING"
    activate_svc.activate.assert_called_once_with(
        bot_id="b1",
        user_id="u1",
        nick_name="ops",
    )


@pytest.mark.unit
def test_activate_one_returns_400_for_invalid_state():
    """ops activate-one should reuse ActivateBotService's state validation."""
    activate_svc = MagicMock(spec=ActivateBotService)
    activate_svc.activate.side_effect = InvalidBotStateError("仅回收状态的 Bot 可激活")

    client = _build_app(activate_svc=activate_svc)
    r = client.post(
        "/api/internal/dormant/activate-one",
        json={"bot_id": "b1", "owner_id": "u1"},
        headers={"Authorization": "Bearer test-tok"},
    )

    assert r.status_code == 400
    assert "回收状态" in r.json()["detail"]


@pytest.mark.unit
def test_activate_one_returns_500_for_unexpected_error():
    """Unexpected activation failures should be logged and returned as a 500."""
    activate_svc = MagicMock(spec=ActivateBotService)
    activate_svc.activate.side_effect = RuntimeError("baas unavailable")

    client = _build_app(activate_svc=activate_svc)
    r = client.post(
        "/api/internal/dormant/activate-one",
        json={"bot_id": "b1", "owner_id": "u1"},
        headers={"Authorization": "Bearer test-tok"},
    )

    assert r.status_code == 500
    assert "baas unavailable" in r.json()["detail"]
