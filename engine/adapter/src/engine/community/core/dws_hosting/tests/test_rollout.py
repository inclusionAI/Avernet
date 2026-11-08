import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from engine.community.plugins.dws_hosting.rollout import BackendHostingRollout
from engine.community.kernel.dws_runtime import RolloutUnavailable
from .test_runtime import runtime_case
from .test_delivery import resolve


def build(tmp_path):
    tmp_path.chmod(0o700)
    clock = [0.0]
    verifier = SimpleNamespace(runtime_eligible=AsyncMock(return_value=True))
    rollout = BackendHostingRollout(
        tmp_path,
        SimpleNamespace(bot_id="bot", owner_id="owner"),
        verifier,
        clock=lambda: clock[0],
    )
    payload = json.dumps(
        {
            "bot_id": "bot",
            "actor_id": "owner",
            "operation": "enabled",
            "data": {"enabled": True},
        }
    )
    return rollout, verifier, clock, payload


async def test_no_proof_denies_without_network(tmp_path):
    rollout, verifier, _, _ = build(tmp_path)
    assert not await rollout.allowed()
    verifier.runtime_eligible.assert_not_awaited()


async def test_positive_cache_expires_and_restart_revalidates(tmp_path):
    rollout, verifier, clock, payload = build(tmp_path)
    await rollout.remember(payload, "signature")
    assert rollout.path.stat().st_mode & 0o777 == 0o600
    assert await rollout.allowed()
    verifier.runtime_eligible.return_value = False
    clock[0] = 29
    assert await rollout.allowed()
    clock[0] = 30
    assert not await rollout.allowed()
    verifier.runtime_eligible.assert_awaited_with(
        payload=payload, signature="signature"
    )
    restarted = BackendHostingRollout(tmp_path, rollout.binding, verifier)
    assert not await restarted.allowed()
    verifier.runtime_eligible.side_effect = TimeoutError()
    with pytest.raises(RolloutUnavailable):
        await restarted.allowed()


async def test_new_proof_invalidates_cache_and_scope_checked(tmp_path):
    rollout, verifier, _, payload = build(tmp_path)
    await rollout.remember(payload, "first")
    assert await rollout.allowed()
    verifier.runtime_eligible.return_value = False
    await rollout.remember(payload, "second")
    assert not await rollout.allowed()
    with pytest.raises(ValueError):
        await rollout.remember(payload.replace('"owner"', '"other"'), "third")


async def test_symlink_proof_is_not_read(tmp_path):
    rollout, verifier, _, _ = build(tmp_path)
    rollout.path.symlink_to(tmp_path / "elsewhere")
    assert not await rollout.allowed()
    verifier.runtime_eligible.assert_not_awaited()


async def test_revocation_disables_runtime_without_sending(tmp_path):
    runtime, state, _, _, _, sender, engine, intake, _ = runtime_case(tmp_path)
    runtime.rollout = SimpleNamespace(allowed=AsyncMock(return_value=False))
    runtime.delivery.rollout_check = runtime.require_rollout
    await runtime.run()
    assert not (await runtime.status(runtime.binding.owner_id)).enabled
    assert not sender.sent and not engine.calls and not intake.entered.is_set()
    assert await runtime.delivery.run_once("binding") is None
    runtime.rollout.allowed.return_value = True
    await runtime.run()
    assert not sender.sent and not intake.entered.is_set()


async def test_revocation_during_preflight_blocks_send(tmp_path):
    runtime, _, ledger, turn, _, sender, _, _, clock = runtime_case(tmp_path)
    resolve(ledger, turn)
    runtime.outbox.enqueue("binding", turn.turn_id, now_ms=clock())
    runtime.rollout = SimpleNamespace(allowed=AsyncMock(side_effect=[True, False]))
    runtime.delivery.rollout_check = runtime.require_rollout
    assert await runtime.delivery.run_once("binding") is None
    assert not sender.sent
    assert not (await runtime.status(runtime.binding.owner_id)).enabled
