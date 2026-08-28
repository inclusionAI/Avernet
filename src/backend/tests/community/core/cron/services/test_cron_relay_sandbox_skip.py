"""Binding-level negative cache for destroyed sandboxes in the cron fan-out.

Pre 2026-08-28 evidence: a bot's instances get reclaimed while the binding row
stays ACTIVE, and every listing/running fan-out then pays a 404 precheck round
trip per destroyed instance, forever (trace 0b446a1717878836076095311e05a8:
6 destroyed sandboxes of the caller's own bot per request). The relay is a
singleton, so a binding-keyed "sandbox destroyed" verdict with a short TTL
skips the prepare+invoke chain until it expires.

Covered: verdict recorded from both failure shapes (error dict / raised
exception), skip skips the transport, TTL expiry re-invokes, non-destroyed
failures are never recorded, and ``list_all_crons`` surfaces the skip as a
failed target with an explicit reason.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.cron.services import cron_runtime_targets
from agentclaw.community.core.cron.services.cron_relay import CronRelayService
from agentclaw.community.core.cron.services.cron_runtime_targets import (
    CronRuntimeTarget,
)


_DESTROYED_MSG = (
    'Adapter returned HTTP 404: {"message":"报错类型: 沙箱已销毁, err=sandbox destroyed"}'
)


class _FakeClock:
    def __init__(self) -> None:
        self.now = 2000.0

    def monotonic(self) -> float:
        return self.now


def _make_service(*, transport_invoke: AsyncMock) -> CronRelayService:
    svc = CronRelayService(
        bot_provider=MagicMock(),
        device_provider=MagicMock(),
        transport=MagicMock(),
        resolver=MagicMock(),
        template_repo=MagicMock(),
        publish_repo=MagicMock(),
    )
    ctx = SimpleNamespace(conn_info={"url": "http://adapter"})
    svc._prepare_runtime_query_async = AsyncMock(return_value=(ctx, None))
    svc._invoke_transport = transport_invoke
    return svc


def _target(binding_id: int) -> CronRuntimeTarget:
    return CronRuntimeTarget(
        bot_id="bot-1",
        bot_name="bot-1",
        owner_id="user-1",
        bot_type="service",
        runtime_stage="draft",
        binding_id=binding_id,
    )


@pytest.mark.asyncio
async def test_destroyed_sandbox_failure_marks_binding():
    invoke = AsyncMock(
        return_value={"success": False, "error": _DESTROYED_MSG}
    )
    svc = _make_service(transport_invoke=invoke)

    result = await svc._fetch_runtime_target_crons(_target(1), "user-1")

    assert result["reason"] == "cron_api_failed"
    assert 1 in svc._sandbox_down_until


@pytest.mark.asyncio
async def test_destroyed_sandbox_exception_marks_binding():
    invoke = AsyncMock(side_effect=ValueError(_DESTROYED_MSG))
    svc = _make_service(transport_invoke=invoke)

    result = await svc._fetch_runtime_target_crons(_target(2), "user-1")

    assert result["reason"] == "cron_api_failed"
    assert 2 in svc._sandbox_down_until


@pytest.mark.asyncio
async def test_second_fetch_within_ttl_skips_transport():
    invoke = AsyncMock(
        return_value={"success": True, "data": []}
    )
    svc = _make_service(transport_invoke=invoke)
    svc._sandbox_down_until[7] = (
        cron_runtime_targets.time.monotonic()
        + cron_runtime_targets.SANDBOX_DESTROYED_TTL_SECONDS
    )

    result = await svc._fetch_runtime_target_crons(_target(7), "user-1")

    assert result["success"] is False
    assert result["reason"] == "sandbox_destroyed_cached"
    invoke.assert_not_awaited()
    # prepare (device query + resolution) is skipped too — that is the win.
    svc._prepare_runtime_query_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_verdict_expires_after_ttl(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(cron_runtime_targets, "time", clock)
    invoke = AsyncMock(
        return_value={"success": False, "error": _DESTROYED_MSG}
    )
    svc = _make_service(transport_invoke=invoke)

    await svc._fetch_runtime_target_crons(_target(3), "user-1")
    clock.now += cron_runtime_targets.SANDBOX_DESTROYED_TTL_SECONDS + 1

    await svc._fetch_runtime_target_crons(_target(3), "user-1")

    assert invoke.await_count == 2


@pytest.mark.asyncio
async def test_plain_failures_are_never_recorded():
    invoke = AsyncMock(
        return_value={"success": False, "error": "Adapter returned HTTP 500"}
    )
    svc = _make_service(transport_invoke=invoke)

    await svc._fetch_runtime_target_crons(_target(4), "user-1")
    await svc._fetch_runtime_target_crons(_target(4), "user-1")

    assert invoke.await_count == 2
    assert not svc._sandbox_down_until


@pytest.mark.asyncio
async def test_list_all_crons_surfaces_skip_as_failed_target():
    invoke = AsyncMock(
        return_value={"success": False, "error": _DESTROYED_MSG}
    )
    svc = _make_service(transport_invoke=invoke)
    bot_provider = svc._bot_provider
    bot_provider.list_bots_by_owner_or_collaborator.return_value = {
        "total": 1,
        "items": [
            {
                "bot_id": "bot-1",
                "bot_name": "bot-1",
                "owner_id": "user-1",
                "bot_type": "service",
                "binding_id": 1,
            }
        ],
    }
    svc._build_runtime_targets = MagicMock(return_value=([_target(1)], []))

    first = await svc.list_all_crons(user_id="user-1", nick_name="user-1")
    second = await svc.list_all_crons(user_id="user-1", nick_name="user-1")

    assert invoke.await_count == 1
    reasons_first = [f["reason"] for f in first["failed_targets"]]
    reasons_second = [f["reason"] for f in second["failed_targets"]]
    assert reasons_first == ["cron_api_failed"]
    assert reasons_second == ["sandbox_destroyed_cached"]
