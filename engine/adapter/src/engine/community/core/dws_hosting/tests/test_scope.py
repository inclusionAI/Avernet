"""Scope precedes generation; no test uses a real message sender."""

import json
from pathlib import Path

import httpx
import pytest

from engine.community.core.dws_hosting.execution import HostingExecutor
from engine.community.kernel.dws_execution import ExecutionError
from engine.community.plugins.dws_hosting.outbox import SqliteHostingOutbox
from engine.community.plugins.dws_hosting import llm_task
from engine.community.plugins.dws_hosting.openclaw_transport import HostingGatewayConfig
from .test_context import setup_turn, permitted_context
from .test_execution import Engine, ready_more


@pytest.mark.parametrize(
    "name,decision,reason",
    [
        ("郭欣", "allow", "inside_rule"),
        ("陈子君", "ignore", "outside_rule"),
        ("未知身份", "uncertain", "insufficient_facts"),
    ],
)
async def test_scope_contract_before_agent(tmp_path, name, decision, reason):
    state, ledger, turn, directory = setup_turn(
        tmp_path, rule="只托管郭欣发给我的消息", name=name
    )

    class Context:
        async def prepare(self, turn):
            c = permitted_context(turn)
            return c.model_copy(
                update={
                    "sender": c.sender.model_copy(
                        update={"names": (name,), "name_resolution": {name: "unique"}}
                    )
                }
            )

    class Scoped(Engine):
        async def classify_scope(self, data, prompt):
            assert data["confirmed_rules"]["rules"] == turn.rule_text
            assert data["directory_facts"]["sender"]["names"] == [name]
            assert "candidate_reply" not in data and "candidate_summary" not in data
            assert data["dialogue"][-1]["current_batch"] is True
            return {"decision": decision, "reason_code": reason}

    engine = Scoped()
    result = await HostingExecutor(state, ledger, engine, Context()).execute(
        "binding", turn.turn_id
    )
    assert result.invocation.scope.decision == decision
    assert len(engine.calls) == (1 if decision == "allow" else 0)
    outbox = SqliteHostingOutbox(directory)
    row = outbox.enqueue("binding", turn.turn_id, now_ms=10000)
    if decision != "allow":
        assert row is None and not engine.reviews
        assert ready_more(state) is not None
    else:
        assert row.delivery.purpose == "reply"


@pytest.mark.parametrize(
    "failure", [TimeoutError(), ExecutionError("scope_unavailable"), ValueError()]
)
async def test_failure_silent_no_generation_or_notice(tmp_path, failure):
    state, ledger, turn, directory = setup_turn(tmp_path)

    class Broken(Engine):
        async def classify_scope(self, data, prompt):
            raise failure

    engine = Broken()
    result = await HostingExecutor(state, ledger, engine).execute(
        "binding", turn.turn_id
    )
    assert result.reason == "scope_unavailable"
    assert result.invocation.scope.decision == "uncertain"
    assert not engine.calls
    assert (
        SqliteHostingOutbox(directory).enqueue("binding", turn.turn_id, now_ms=10000)
        is None
    )


async def test_disable_during_scope_prevents_agent(tmp_path):
    state, ledger, turn, _ = setup_turn(tmp_path)

    class Disabled(Engine):
        async def classify_scope(self, data, prompt):
            state.set_enabled("binding", False, now_ms=7000)
            return {"decision": "allow", "reason_code": "inside_rule"}

    engine = Disabled()
    result = await HostingExecutor(state, ledger, engine).execute(
        "binding", turn.turn_id
    )
    assert result.state == "cancelled" and not engine.calls


@pytest.mark.parametrize(
    "mode", ["ok", "redirect", "missing", "invalid", "error", "inconsistent", "oversized"]
)
async def test_real_llm_task_http_contract(monkeypatch, mode):
    original = httpx.AsyncClient
    calls = []

    def handle(request):
        calls.append(request)
        assert request.url.path == "/tools/invoke"
        body = json.loads(request.content)
        assert body["tool"] == "llm-task"
        assert body["args"]["thinking"] == "off"
        assert body["args"]["maxTokens"] == 2048
        assert "sessionKey" not in body
        assert body["args"]["schema"]["additionalProperties"] is False
        value = {"decision": "ignore", "reason_code": "outside_rule"}
        if mode == "redirect":
            return httpx.Response(
                302, headers={"Location": "https://untrusted.invalid"}
            )
        if mode == "missing":
            return httpx.Response(404)
        if mode == "invalid":
            return httpx.Response(200, text="not json")
        if mode == "oversized":
            return httpx.Response(200, text="x" * 65537)
        if mode == "inconsistent":
            value["decision"] = "allow"
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {"isError": mode == "error", "details": {"json": value}},
            },
        )

    def client(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return original(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(llm_task.httpx, "AsyncClient", client)
    cfg = HostingGatewayConfig(
        url="ws://localhost:18789", device_key_file=Path("/unused"), token="test"
    )
    if mode == "ok":
        assert (await llm_task.classify(cfg, {}, "scope only")).decision == "ignore"
    else:
        with pytest.raises(ExecutionError, match="scope_unavailable"):
            await llm_task.classify(cfg, {}, "scope only")
    assert len(calls) == 1
