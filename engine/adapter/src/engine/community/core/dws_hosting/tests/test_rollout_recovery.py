"""Unknown rollout eligibility suspends sending without changing owner consent."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from engine.community.kernel.dws_runtime import RolloutUnavailable
from engine.community.plugins.dws_hosting.verification import BackendManagementVerifier

from .test_rollout import build
from .test_runtime import runtime_case, stop
from .test_intake import wait_until
from .test_delivery import resolve


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503),
        httpx.Response(404),
        httpx.Response(302),
        httpx.Response(200, json={"allowed": "true"}),
        httpx.Response(200, text="not-json"),
        httpx.ConnectError("private-address"),
        httpx.ReadTimeout("private-proof"),
    ],
)
async def test_runtime_request_errors_are_not_revocations(response):
    def handler(_):
        if isinstance(response, Exception):
            raise response
        return response

    verifier = BackendManagementVerifier(
        "https://backend.test", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(RolloutUnavailable) as caught:
        await verifier.runtime_eligible("command", "proof")
    assert "private" not in str(caught.value)
    assert not await verifier.verify("command", "proof")


@pytest.mark.parametrize(
    "status,body", [(200, {"allowed": False}), (401, {}), (403, {})]
)
async def test_explicit_denial_still_revokes(status, body):
    verifier = BackendManagementVerifier(
        "https://backend.test",
        transport=httpx.MockTransport(lambda _: httpx.Response(status, json=body)),
    )
    assert not await verifier.runtime_eligible("command", "proof")


async def test_positive_cache_is_not_extended_on_failure_and_retries_are_bounded(
    tmp_path,
):
    rollout, verifier, clock, payload = build(tmp_path)
    await rollout.remember(payload, "proof")
    assert await rollout.allowed()
    clock[0] = 30
    verifier.runtime_eligible.side_effect = RolloutUnavailable("unavailable")
    with pytest.raises(RolloutUnavailable):
        await rollout.allowed()
    with pytest.raises(RolloutUnavailable):
        await rollout.allowed()
    assert verifier.runtime_eligible.await_count == 2
    clock[0] = 35
    verifier.runtime_eligible.side_effect = None
    assert await rollout.allowed()
    assert verifier.runtime_eligible.await_count == 3


async def test_slow_expired_allow_is_unknown_not_a_revocation(tmp_path):
    rollout, verifier, clock, payload = build(tmp_path)
    await rollout.remember(payload, "proof")

    async def slow(**_):
        clock[0] = 31
        return True

    verifier.runtime_eligible.side_effect = slow
    with pytest.raises(RolloutUnavailable, match="expired"):
        await rollout.allowed()


async def test_running_workers_pause_without_disabling_then_resume(tmp_path):
    runtime, _, _, _, _, sender, engine, intake, _ = runtime_case(tmp_path)
    available = True

    async def allowed():
        if not available:
            raise RolloutUnavailable("network_unavailable")
        return True

    runtime.rollout = SimpleNamespace(allowed=allowed)
    runtime.delivery.rollout_check = runtime.require_rollout
    task = asyncio.create_task(runtime.run())
    try:
        await intake.entered.wait()
        available = False
        intake.release.set()
        await wait_until(lambda: runtime._rollout_unavailable)
        await asyncio.sleep(0.04)
        assert (await runtime.status(runtime.binding.owner_id)).enabled
        assert not sender.sent and not engine.calls and not task.done()
        available = True
        await wait_until(lambda: len(sender.sent) == 1)
        assert (await runtime.status(runtime.binding.owner_id)).enabled
    finally:
        await stop(task)


async def test_unavailable_startup_waits_and_never_overwrites_manual_disable(tmp_path):
    runtime, state, _, _, _, sender, engine, intake, clock = runtime_case(tmp_path)
    runtime.rollout = SimpleNamespace(
        allowed=AsyncMock(side_effect=RolloutUnavailable("offline"))
    )
    task = asyncio.create_task(runtime.run())
    try:
        await wait_until(lambda: runtime._rollout_unavailable)
        assert (await runtime.status(runtime.binding.owner_id)).enabled
        assert not intake.entered.is_set() and not sender.sent and not engine.calls
        state.set_enabled("binding", False, now_ms=clock())
        runtime.rollout.allowed.side_effect = None
        runtime.rollout.allowed.return_value = True
        await asyncio.wait_for(asyncio.shield(task), 2)
        assert not (await runtime.status(runtime.binding.owner_id)).enabled
        assert not intake.entered.is_set()
    finally:
        if not task.done():
            await stop(task)


async def test_unavailable_during_preflight_blocks_send_without_revoking(tmp_path):
    runtime, _, ledger, turn, _, sender, _, _, clock = runtime_case(tmp_path)
    resolve(ledger, turn)
    runtime.outbox.enqueue("binding", turn.turn_id, now_ms=clock())
    runtime.rollout = SimpleNamespace(
        allowed=AsyncMock(side_effect=[True, RolloutUnavailable("timeout")])
    )
    runtime.delivery.rollout_check = runtime.require_rollout
    assert await runtime.delivery.run_once("binding") is None
    assert not sender.sent
    assert (await runtime.status(runtime.binding.owner_id)).enabled


async def test_startup_recovers_without_a_second_enable_command(tmp_path):
    runtime, _, _, _, _, sender, _, intake, _ = runtime_case(tmp_path)
    runtime.rollout = SimpleNamespace(
        allowed=AsyncMock(side_effect=RolloutUnavailable("offline"))
    )
    task = asyncio.create_task(runtime.run())
    try:
        await wait_until(lambda: runtime._rollout_unavailable)
        assert not intake.entered.is_set() and not sender.sent
        runtime.rollout.allowed.side_effect = None
        runtime.rollout.allowed.return_value = True
        intake.release.set()
        await wait_until(lambda: len(sender.sent) == 1)
        assert (await runtime.status(runtime.binding.owner_id)).enabled
    finally:
        await stop(task)
