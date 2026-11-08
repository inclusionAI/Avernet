import asyncio
import sqlite3

import pytest

from engine.community.core.dws_hosting.execution import invocation_for
from engine.community.kernel.dws_execution import EngineUpdate
from engine.community.plugins.dws_hosting.runtime import SqliteHostingRuntimeStore
from .test_execution import build_execution, result_json
from .test_state_contract import message, identity
from .test_delivery import case as delivery_case, enqueue
from .test_hosting_gateway import gateway, key_file as gateway_key_fixture, response
from engine.community.plugins.dws_hosting.openclaw import OpenClawHostingEngine

case = delivery_case
key_file = gateway_key_fixture


@pytest.mark.parametrize("control", ["disable", "takeover"])
@pytest.mark.parametrize("status", ["accepted", "unknown", "dispatching"])
def test_revocation_detaches_only_old_execution_and_preserves_audit(
    tmp_path, control, status
):
    state, ledger, turn, directory = build_execution(tmp_path)
    request = invocation_for(turn)
    ledger.reserve(turn, request)
    if status == "accepted":
        ledger.record_update(
            "binding",
            turn.turn_id,
            EngineUpdate(run_id=request.run_id, status="accepted"),
        )
    elif status == "unknown":
        ledger.mark_unknown("binding", turn.turn_id, "worker_cancelled")
    if control == "disable":
        state.set_enabled("binding", False, now_ms=7000)
        state.set_enabled("binding", True, now_ms=8000)
    else:
        state.set_takeover("binding", "chat", True, now_ms=7000)
        state.set_takeover("binding", "chat", False, now_ms=8000)
    record = ledger.get("binding", turn.turn_id)
    assert record.state == "cancelled" and record.result is None
    assert record.invocation == request
    state.ingest("binding", message("new", created=9000), now_ms=9000)
    new = state.claim_ready("binding", now_ms=14000)
    assert new.session_id != turn.session_id
    ledger.record_update(
        "binding",
        turn.turn_id,
        EngineUpdate(
            run_id=request.run_id, status="completed", result_json=result_json()
        ),
    )
    ledger.mark_unknown("binding", turn.turn_id, "late_timeout")
    assert ledger.get("binding", turn.turn_id) == record
    SqliteHostingRuntimeStore(directory).maintain(identity(), now_ms=14000)
    assert state.get_turn("binding", new.turn_id) == new


def test_legacy_revoked_unknown_recovered_while_disabled_without_model(tmp_path):
    state, ledger, turn, directory = build_execution(tmp_path)
    ledger.reserve(turn, invocation_for(turn))
    ledger.mark_unknown("binding", turn.turn_id, "worker_cancelled")
    with sqlite3.connect(directory / "dws_hosting.sqlite3") as db:
        db.execute("UPDATE bindings SET enabled=0, epoch=epoch+1")
    state.set_enabled("binding", False, now_ms=10000)
    assert ledger.get("binding", turn.turn_id).state == "cancelled"
    with sqlite3.connect(directory / "dws_hosting.sqlite3") as db:
        assert (
            db.execute(
                "SELECT count(*) FROM batches WHERE state IN ('running','unknown')"
            ).fetchone()[0]
            == 0
        )
        assert db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0


def test_current_unknown_is_not_released_by_age_or_rule_change(tmp_path):
    state, ledger, turn, directory = build_execution(tmp_path)
    ledger.reserve(turn, invocation_for(turn))
    ledger.mark_unknown("binding", turn.turn_id, "disconnected")
    state.confirm_rule("binding", "new rule")
    SqliteHostingRuntimeStore(directory).maintain(identity(), now_ms=10**12)
    assert ledger.get("binding", turn.turn_id).state == "unknown"
    state.ingest("binding", message("new", created=9000), now_ms=9000)
    assert state.claim_ready("binding", now_ms=14000) is None


def test_revocation_never_relabels_an_uncertain_send_as_cancelled(case):
    enqueue(case)
    claimed = case[4].claim("binding", now_ms=10000)
    sending = case[4].start_send(claimed, now_ms=10000)
    case[0].set_enabled("binding", False, now_ms=11000)
    assert case[4].get("binding", sending.delivery.delivery_id).state == "sending"
    case[0].set_enabled("binding", True, now_ms=12000)
    case[0].ingest("binding", message("new", created=13000), now_ms=13000)
    assert case[0].claim_ready("binding", now_ms=18000) is None


async def test_cancel_sends_only_exact_hosting_run_abort(tmp_path, key_file):
    _, _, turn, _ = build_execution(tmp_path)
    request = invocation_for(turn)
    entered = asyncio.Event()

    async def handler(ws, frame):
        if frame["method"] == "agent":
            await response(ws, frame, {"runId": request.run_id, "status": "accepted"})
            entered.set()
            await ws.wait_closed()
        else:
            assert frame["method"] == "chat.abort"
            assert frame["params"] == {
                "sessionKey": request.session_key,
                "runId": request.run_id,
            }
            await response(
                ws, frame, {"ok": True, "aborted": True, "runIds": [request.run_id]}
            )

    async with gateway(key_file, handler) as (config, requests, _):
        engine = OpenClawHostingEngine(config)

        async def run():
            async for _ in engine.execute(request):
                pass

        task = asyncio.create_task(run())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert [r["method"] for r in requests] == ["agent", "chat.abort"]
        await engine._abort(
            request.model_copy(update={"session_key": "agent:main:main"})
        )
        assert len(requests) == 2
