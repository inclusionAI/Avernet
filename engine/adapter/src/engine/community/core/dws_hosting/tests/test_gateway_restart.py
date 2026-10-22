"""A gateway restart loses volatile run caches, not the durable dispatch fence."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from engine.community.core.dws_hosting.execution import HostingExecutor, invocation_for
from engine.community.kernel.dws_execution import EngineUpdate, GatewayWitness
from engine.community.plugins.dws_hosting.executions import SqliteHostingExecutionStore
from engine.community.plugins.dws_hosting.openclaw import OpenClawHostingEngine
from engine.community.plugins.dws_hosting.openclaw_transport import HostingGatewayConfig, HostingGatewayTransport
from .test_execution import build_execution
from .test_state_contract import message
from .test_hosting_gateway import gateway, key_file as gateway_key_fixture, response
from .test_execution import result_json

key_file = gateway_key_fixture


def witness(lower=1000, upper=1100, boot="host-boot"):
    return GatewayWitness(host_boot_id=boot, started_lower_ms=lower, started_upper_ms=upper)


@pytest.mark.parametrize("current,ended", [
    (witness(5000, 5100), True),
    (witness(1001, 1101), False),
    (witness(500, 5200), False),
    (witness(5000, 5100, "other-host"), False),
    (None, False),
])
async def test_persisted_witness_releases_only_proven_restart(tmp_path, current, ended):
    state, ledger, turn, directory = build_execution(tmp_path)
    ledger.reserve(turn, invocation_for(turn))
    ledger.record_update("binding", turn.turn_id, EngineUpdate(
        run_id="dws-turn-" + turn.turn_id, status="prepared", gateway_witness=witness()))
    assert ledger.get("binding", turn.turn_id).state == "dispatching"
    ledger.mark_unknown("binding", turn.turn_id, "gateway_connection_closed")
    # Adapter restart must not discard the witness.
    ledger = SqliteHostingExecutionStore(directory)
    engine = OpenClawHostingEngine(HostingGatewayConfig(
        url="ws://localhost", device_key_file=Path('/unused'), token="test"))
    calls = []

    async def responses(rpc_id, method, params, **kwargs):
        calls.append(method)
        assert method == "agent.wait"
        if current is not None:
            yield {"type": "hosting.gateway", "witness": current.model_dump()}
        yield {"ok": True, "payload": {"runId": params["runId"], "status": "timeout"}}

    engine._transport.responses = responses
    result = await HostingExecutor(state, ledger, engine).resume("binding", turn.turn_id)
    assert result.state == ("manual" if ended else "unknown")
    assert result.decision is None
    state.ingest("binding", message("next", created=7000), now_ms=7000)
    assert (state.claim_ready("binding", now_ms=11000) is not None) == ended
    assert calls == ["agent.wait"]  # no redispatch, review or send


def test_witness_is_not_overwritten_by_late_ack_or_another_process(tmp_path):
    _, ledger, turn, _ = build_execution(tmp_path)
    request = invocation_for(turn)
    ledger.reserve(turn, request)
    ledger.record_update("binding", turn.turn_id, EngineUpdate(
        run_id=request.run_id, status="prepared", gateway_witness=witness()))
    ledger.record_update("binding", turn.turn_id, EngineUpdate(run_id=request.run_id, status="accepted"))
    with pytest.raises(ValueError, match="witness changed"):
        ledger.record_update("binding", turn.turn_id, EngineUpdate(
            run_id=request.run_id, status="prepared", gateway_witness=witness(5000, 5100)))
    assert ledger.get("binding", turn.turn_id).invocation.gateway_witness == witness()


@pytest.mark.parametrize("uptime", [None, -1, True, "100", 10001])
def test_bad_snapshot_cannot_prove_a_restart(uptime):
    config = HostingGatewayConfig(url="ws://localhost", device_key_file=Path('/unused'), token="test")
    assert HostingGatewayTransport(config)._witness({"snapshot": {"uptimeMs": uptime}}, 10000, 10100) is None


def test_start_interval_uses_monotonic_clock_and_local_authenticated_snapshot(monkeypatch):
    config = HostingGatewayConfig(url="ws://localhost", device_key_file=Path('/unused'), token="test")
    monkeypatch.setattr(Path, "read_text", lambda _: "12345678-1234-1234-1234-123456789abc")
    result = HostingGatewayTransport(config)._witness({"snapshot": {"uptimeMs": 1000}}, 10000, 10100)
    assert (result.started_lower_ms, result.started_upper_ms) == (9000, 9100)
    remote = HostingGatewayTransport(replace(config, url="wss://example.com"))
    assert remote._witness({"snapshot": {"uptimeMs": 1000}}, 10000, 10100) is None


@pytest.mark.parametrize("storage_fails", [False, True])
async def test_real_socket_persists_witness_before_dispatch(tmp_path, key_file, monkeypatch, storage_fails):
    state, ledger, turn, directory = build_execution(tmp_path)
    # Cross-platform fixture; production extracts it only after authenticated hello.
    monkeypatch.setattr(HostingGatewayTransport, "_witness", lambda *args: witness())
    if storage_fails:
        original = ledger.record_update
        def broken(binding_id, turn_id, update):
            if update.status == "prepared":
                raise OSError("disk full")
            original(binding_id, turn_id, update)
        ledger.record_update = broken

    async def handler(ws, req):
        stored = SqliteHostingExecutionStore(directory).get("binding", turn.turn_id)
        assert stored.invocation.gateway_witness == witness()
        text = '{"verdict":"approve"}' if req['params']['idempotencyKey'].endswith('-review') else None
        await response(ws, req, {"runId": req['params']['idempotencyKey'], "status": "ok",
                                 "summary": "completed", "result": json.loads(result_json(text))})

    async with gateway(key_file, handler) as (config, requests, closed):
        worker = HostingExecutor(state, ledger, OpenClawHostingEngine(config))
        if storage_fails:
            with pytest.raises(OSError, match="disk full"):
                await worker.execute("binding", turn.turn_id)
            assert not requests
        else:
            record = await worker.execute("binding", turn.turn_id)
            assert record.state == "resolved" and record.decision.action == "reply"
