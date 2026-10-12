"""Tests for the AvernetSubagent plugin (backend-bot-backed subagent, D24).

Covers: create/poll/lifecycle against an in-file httpx.MockTransport gateway
(no network, no real bot endpoints), deterministic idempotency keys,
terminal-failure handling, teardown guarantees, and poll-deadline expiry.

Conventions follow test_orchestrator.py: sync test defs driving async code
through asyncio.run, hand-written fakes (no MagicMock).
"""
from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from agentclaw.community.core.agentcompute.config.settings import (
    AgentComputeAvernetConfig,
)
from agentclaw.community.core.agentcompute.domain.models import (
    AgentSpecRaw, EpochMillis, ProviderId, RequestMetadata, Run, RunId,
    RunStatus, SubagentResult, TaskEnvelope, TenantId,
)
from agentclaw.community.core.agentcompute.plugins.agents.bot import (
    AvernetSubagent,
)

CREATE_PATH = "/openapi/v1/bots/with-manifest"
STATUS_PATH_TEMPLATE = "/openapi/v1/bots/{bot_id}/status"
DELETE_PATH_TEMPLATE = "/openapi/v1/bots/{bot_id}"
CHAT_PATH = "/openapi/v1/chat/stream"


def _now_ms():
    return int(time.time() * 1000)


def _mk_run(run_id="run-42") -> Run:
    return Run(
        run_id=RunId(run_id),
        tenant=TenantId("tenant-a"),
        goal="goal",
        agent_catalog=(),
        provider=ProviderId("avernet"),
        request_metadata=RequestMetadata(),
        status=RunStatus.RUNNING,
        started_at=EpochMillis(_now_ms()),
    )


def _mk_spec(name="writer", instructions="be brief") -> AgentSpecRaw:
    return AgentSpecRaw(name=name, role="write", instructions=instructions)


def _mk_envelope() -> TaskEnvelope:
    return TaskEnvelope(
        name="writer",
        objective="do a thing",
        output_schema={"type": "object"},
        boundaries="stay-on-topic",
        parent_goal="goal",
    )


class FakeAvernetGateway:
    """httpx-compatible fake of the backend bot endpoints.

    Used as a MockTransport handler; simulates create/status/delete plus the
    chat/stream call the subagent performs.
    """

    def __init__(self, *, initial_state="READY", poll_script=None,
                 create_payload=None, chat=None) -> None:
        self.initial_state = initial_state
        self.bots = {}
        self.create_requests = []
        self.status_requests = []
        self.observed_states = []
        self.delete_requests = []
        self.chat_requests = []
        self.poll_script = dict(poll_script or {})
        self.create_payload = create_payload
        self.chat = chat if chat is not None else {
            "status": 200, "json": {"answer": "bot says hi"},
        }
        self._bot_seq = 0

    def _first_state(self, bot_id) -> str:
        pending = self.poll_script.get(bot_id)
        return pending[0] if pending else self.initial_state

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == CREATE_PATH:
            body = json.loads(request.content)
            self.create_requests.append(body)
            bot_id = f"bot-{self._bot_seq}"
            self._bot_seq += 1
            self.bots[bot_id] = self._first_state(bot_id)
            payload = self.create_payload or {"data": {"bot_id": bot_id}}
            return httpx.Response(200, json=payload)
        if request.method == "GET" and path.endswith("/status"):
            bot_id = path[len("/openapi/v1/bots/"):-len("/status")]
            self.status_requests.append(bot_id)
            pending = self.poll_script.get(bot_id)
            if pending:
                state = pending.pop(0)
                self.bots[bot_id] = state
            else:
                state = self.bots.get(bot_id, "UNKNOWN")
            self.observed_states.append(state)
            return httpx.Response(200, json={"data": {"state": state}})
        if request.method == "DELETE":
            bot_id = path[len("/openapi/v1/bots/"):]
            self.delete_requests.append(bot_id)
            self.bots.pop(bot_id, None)
            return httpx.Response(200, json={"data": {"deleted": True}})
        if request.method == "POST" and path == CHAT_PATH:
            body = json.loads(request.content)
            self.chat_requests.append(body)
            if self.chat["status"] == 200:
                if "json" in self.chat:
                    return httpx.Response(200, json=self.chat["json"])
                return httpx.Response(200, text=self.chat["text"])
            return httpx.Response(self.chat["status"], text="chat exploded")
        return httpx.Response(404, json={"error": "no such endpoint"})


def _make_subagent(gateway: FakeAvernetGateway, run, spec) -> AvernetSubagent:
    config = AgentComputeAvernetConfig(backend_base_url="http://fake.local")
    sub = AvernetSubagent(spec=spec, run=run, llm=None, config=config)
    # standard httpx testing seam: mount the in-file gateway as the transport
    sub._client = httpx.AsyncClient(
        transport=httpx.MockTransport(gateway),
        base_url=config.backend_base_url,
        timeout=config.http_timeout,
    )
    return sub


class TestBotLifecycle:
    def test_setup_creates_polls_ready_and_run_task_completes(self):
        async def main():
            gateway = FakeAvernetGateway()
            sub = _make_subagent(gateway, _mk_run(), _mk_spec())
            await sub.setup()
            result = await sub.run_task(_mk_envelope())
            await sub.teardown()

            manifest = json.loads(gateway.create_requests[0]["manifest"])
            assert manifest["name"] == "writer"
            assert manifest["instructions"] == "be brief"
            assert gateway.status_requests == ["bot-0"]
            env = _mk_envelope()
            expected_prompt = (
                f"objective={env.objective!r} boundaries={env.boundaries!r}"
            )
            assert gateway.chat_requests == [
                {"bot_id": "bot-0", "message": expected_prompt}
            ]
            assert isinstance(result, SubagentResult)
            assert result.output == {"answer": "bot says hi"}
            # NOTE: found-vs-expected — task 10.4 asks for bot_state recorded
            # in the result/provenance. SubagentResult carries only output and
            # token_usage; bot lifecycle state stays gateway-internal.
            assert result.token_usage is None
            assert gateway.delete_requests == ["bot-0"]

        asyncio.run(main())

    def test_create_manifest_idempotency_key_is_deterministic(self):
        async def main():
            gateway = FakeAvernetGateway()
            run = _mk_run("run-idem")
            sub = _make_subagent(gateway, run, _mk_spec())
            await sub.setup()
            await sub.teardown()

            manifest = json.loads(gateway.create_requests[0]["manifest"])
            assert manifest["idempotency_key"] == "agentcompute:run-idem:writer"

        asyncio.run(main())

    def test_repeat_create_with_same_run_and_spec_reuses_idempotency_key(self):
        async def main():
            gateway = FakeAvernetGateway()
            run = _mk_run("run-idem")
            spec = _mk_spec("writer")

            sub_a = _make_subagent(gateway, run, spec)
            await sub_a.setup()
            await sub_a.teardown()

            sub_b = _make_subagent(gateway, run, spec)
            await sub_b.setup()
            await sub_b.teardown()

            assert len(gateway.create_requests) == 2
            keys = [
                json.loads(req["manifest"])["idempotency_key"]
                for req in gateway.create_requests
            ]
            assert keys[0] == keys[1] == "agentcompute:run-idem:writer"

        asyncio.run(main())

    def test_different_spec_name_yields_different_idempotency_key(self):
        async def main():
            gateway = FakeAvernetGateway()
            run = _mk_run("run-idem")
            sub_writer = _make_subagent(gateway, run, _mk_spec("writer"))
            await sub_writer.setup()
            await sub_writer.teardown()
            sub_reviewer = _make_subagent(gateway, run, _mk_spec("reviewer"))
            await sub_reviewer.setup()
            await sub_reviewer.teardown()

            key_writer = json.loads(
                gateway.create_requests[0]["manifest"]
            )["idempotency_key"]
            key_reviewer = json.loads(
                gateway.create_requests[1]["manifest"]
            )["idempotency_key"]
            assert key_writer != key_reviewer

        asyncio.run(main())

    def test_empty_instructions_default_applied(self):
        async def main():
            gateway = FakeAvernetGateway()
            sub = _make_subagent(gateway, _mk_run(), _mk_spec(instructions=""))
            await sub.setup()
            await sub.teardown()

            manifest = json.loads(gateway.create_requests[0]["manifest"])
            assert manifest["instructions"] == "Complete the assigned task."

        asyncio.run(main())

    def test_provisioning_poll_progresses_until_ready(self):
        async def main():
            gateway = FakeAvernetGateway(
                poll_script={"bot-0": ["PROVISIONING", "READY"]}
            )
            sub = _make_subagent(gateway, _mk_run(), _mk_spec())
            await sub.setup()
            await sub.teardown()

            assert len(gateway.status_requests) == 2
            assert gateway.observed_states == ["PROVISIONING", "READY"]

        asyncio.run(main())

    def test_run_task_without_setup_rejected(self):
        async def main():
            gateway = FakeAvernetGateway()
            sub = _make_subagent(gateway, _mk_run(), _mk_spec())
            with pytest.raises(RuntimeError, match="setup not completed"):
                await sub.run_task(_mk_envelope())
            assert gateway.create_requests == []

        asyncio.run(main())


class TestTerminalFailurePaths:
    def test_terminal_bot_state_fails_setup_and_teardown_deletes(self):
        async def main():
            gateway = FakeAvernetGateway(initial_state="CREATE_FAILED")
            sub = _make_subagent(gateway, _mk_run(), _mk_spec())
            with pytest.raises(RuntimeError, match="CREATE_FAILED"):
                await sub.setup()
            await sub.teardown()

            assert gateway.delete_requests == ["bot-0"]
            await sub.teardown()
            assert gateway.delete_requests == ["bot-0"]

        asyncio.run(main())

    @pytest.mark.parametrize("state", [
        "AUTHORIZATION_REJECTED", "AUTHORIZATION_EXPIRED",
        "CREATE_FAILED", "APPLY_FAILED",
    ])
    def test_each_terminal_failure_state_aborts_setup(self, state):
        async def main():
            gateway = FakeAvernetGateway(initial_state=state)
            sub = _make_subagent(gateway, _mk_run(), _mk_spec())
            with pytest.raises(RuntimeError, match=state):
                await sub.setup()
            await sub.teardown()

        asyncio.run(main())

    def test_create_response_missing_bot_id_fails_setup(self):
        async def main():
            gateway = FakeAvernetGateway(create_payload={"data": {}})
            sub = _make_subagent(gateway, _mk_run(), _mk_spec())
            with pytest.raises(RuntimeError, match="did not return bot_id"):
                await sub.setup()
            await sub.teardown()
            assert gateway.delete_requests == []

        asyncio.run(main())

    def test_teardown_deletes_bot_after_run_task_failure(self):
        async def main():
            gateway = FakeAvernetGateway(chat={"status": 500})
            sub = _make_subagent(gateway, _mk_run(), _mk_spec())
            await sub.setup()
            with pytest.raises(httpx.HTTPStatusError):
                await sub.run_task(_mk_envelope())
            await sub.teardown()

            assert gateway.delete_requests == ["bot-0"]

        asyncio.run(main())

    def test_halt_deletes_bot_like_teardown(self):
        async def main():
            gateway = FakeAvernetGateway()
            sub = _make_subagent(gateway, _mk_run(), _mk_spec())
            await sub.setup()
            await sub.halt()

            assert gateway.delete_requests == ["bot-0"]

        asyncio.run(main())

    def test_teardown_without_setup_is_safe(self):
        async def main():
            gateway = FakeAvernetGateway()
            sub = _make_subagent(gateway, _mk_run(), _mk_spec())
            await sub.teardown()
            assert gateway.delete_requests == []

        asyncio.run(main())

    def test_bot_delete_http_error_swallowed_by_teardown(self):
        async def main():
            gateway = FakeAvernetGateway()

            def failing_delete(request: httpx.Request) -> httpx.Response:
                return httpx.Response(500, text="delete blew up")

            sub = _make_subagent(gateway, _mk_run(), _mk_spec())
            await sub.setup()
            sub._client = httpx.AsyncClient(
                transport=httpx.MockTransport(failing_delete),
                base_url="http://fake.local",
                timeout=5.0,
            )
            await sub.teardown()

        asyncio.run(main())


class TestPollDeadline:
    def test_poll_deadline_expiry_fails_setup_with_timeout(self, monkeypatch):
        async def main():
            gateway = FakeAvernetGateway(initial_state="PROVISIONING")
            sub = _make_subagent(gateway, _mk_run(), _mk_spec())

            ticks = []

            def fake_monotonic():
                ticks.append(1)
                # first call computes the 300s deadline; the next already
                # exceeds it, so no real poll-interval waiting happens
                return 0.0 if len(ticks) == 1 else 10_000.0

            # patched after loop start so the live event loop keeps its clock
            monkeypatch.setattr(time, "monotonic", fake_monotonic)
            with pytest.raises(RuntimeError, match="TIMEOUT"):
                await sub.setup()
            monkeypatch.undo()
            await sub.teardown()

            assert gateway.create_requests  # bot was created before polling
            assert gateway.status_requests == []

        asyncio.run(main())

    def test_run_task_text_response_wraps_in_text_key(self):
        async def main():
            gateway = FakeAvernetGateway(chat={"status": 200, "text": "plain words"})
            sub = _make_subagent(gateway, _mk_run(), _mk_spec())
            await sub.setup()
            result = await sub.run_task(_mk_envelope())
            await sub.teardown()

            assert result.output == {"text": "plain words"}

        asyncio.run(main())

    def test_run_task_json_response_parsed_verbatim(self):
        async def main():
            gateway = FakeAvernetGateway(
                chat={"status": 200, "json": {"answer": "structured"}}
            )
            sub = _make_subagent(gateway, _mk_run(), _mk_spec())
            await sub.setup()
            result = await sub.run_task(_mk_envelope())
            await sub.teardown()

            assert result.output == {"answer": "structured"}

        asyncio.run(main())
