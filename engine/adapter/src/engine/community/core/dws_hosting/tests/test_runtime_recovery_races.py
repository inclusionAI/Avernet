"""Recovery races must neither crash the scheduler nor replay uncertain turns."""

import pytest

from engine.community.core.dws_hosting.execution import invocation_for

from .test_runtime import runtime_case
from .test_state_contract import message


@pytest.mark.parametrize("new_claim", [False, True])
async def test_intake_barrier_between_claim_and_reserve(tmp_path, new_claim):
    runtime, state, ledger, turn, _, sender, engine, _, clock = runtime_case(tmp_path)
    clock.now = 12000
    if new_claim:
        # Existing claimed work is revoked; the next turn enters via claim_ready.
        state.set_enabled("binding", False, now_ms=6001)
        state.set_enabled("binding", True, now_ms=6002)
        state.ingest("binding", message("later", created=7000), now_ms=7000)
    original = runtime.executor.execute

    async def blocked(binding_id, turn_id):
        runtime.recovery.block_intake(binding_id)
        return await original(binding_id, turn_id)

    runtime.executor.execute = blocked
    await runtime._execution_cycle()
    assert not engine.calls and not sender.sent
    with pytest.raises(KeyError):
        ledger.get("binding", turn.turn_id)
    runtime.executor.execute = original
    window = runtime.recovery.recovery_window(
        "binding", now_ms=clock(), overlap_ms=1000, window_ms=60000
    )
    assert runtime.recovery.complete_recovery(window)
    await runtime._execution_cycle()
    assert len(engine.calls) == 1
    assert not sender.sent


@pytest.mark.parametrize("ended", [False, True])
async def test_unknown_reconciliation_never_redispatches(tmp_path, ended):
    runtime, state, ledger, turn, _, sender, engine, _, _ = runtime_case(tmp_path)
    ledger.reserve(turn, invocation_for(turn))
    ledger.mark_unknown("binding", turn.turn_id, "engine_result_unknown")
    engine.ended = ended
    await runtime._reconcile_unknown()
    assert ledger.get("binding", turn.turn_id).state == ("manual" if ended else "unknown")
    assert len(engine.inspections) == 1
    assert not engine.calls and not sender.sent
    state.ingest("binding", message("next", created=7000), now_ms=7000)
    assert (state.claim_ready("binding", now_ms=11000) is not None) == ended
