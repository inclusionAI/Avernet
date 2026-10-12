"""Over-the-wire e2e (task 12.8 surface; ported from the POC's
tests/test_e2e_api.py battery).

Boots the REAL app under uvicorn on a local port with a fresh profile
injector whose agentcompute binding carries a kind-scripted stub LLM
(orchestrator decisions wired, subagent answers canned), then drives the
public surface exactly the way an operator would: submit over HTTP,
advance the durable job with the real worker, poll status, stream SSE to
the terminal event, and re-check persistence through the repositories.

The worker component has no auto-start in the app lifecycle — this test
owns it explicitly (a thread ticking run_once()), matching production
topology where the worker loop is a separate actor.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest
import uvicorn

from agentclaw.community.adapters.http.app import app
from agentclaw.community.core.agentcompute.config.settings import (
    AgentComputeConfig, AgentComputeOrchestrationConfig, LlmConfig,
)
from agentclaw.community.core.agentcompute.domain import models as dm
from agentclaw.community.core.agentcompute.plugins.llm.stub import StubLLMProvider
from agentclaw.community.core.agentcompute.runtime.worker import (
    AgentComputeWorker,
)
from agentclaw.community.core.agentcompute.service import AgentComputeService
from agentclaw.community.core.agentcompute.spi.llm import LLMProvider
from agentclaw.community.di import DeployProfile, build_injector
from agentclaw.community.di.modules.agentcompute_module import (
    AgentComputeModule,
)

HOST = "127.0.0.1"
# The fixture binds an ephemeral port (0) and rewrites BASE once uvicorn
# reports the bound socket, so parallel runs never collide on a fixed port.
BASE = ""
AUTH = {"x-user-id": "wireshark", "X-Tenant-Id": "tenant-wire"}
TENANT = "tenant-wire"

_ORCHESTRATOR_SCRIPT = [
    '{"kind": "spawn_agent", "agents": [{"name": "a1", "objective": "work"}]}',
    '{"kind": "wait", "refs": ["a1"], "mode": "all"}',
    '{"kind": "finish", "answer": "done over the wire"}',
]
_SUBAGENT_ANSWER = '{"did": 1}'


class _KindScriptLLM:
    """StubLLMProvider, but persona-split by CompletionRequest.kind:
    the orchestrator walks its exact script; subagents always answer."""

    def __init__(self) -> None:
        self._orchestrator = StubLLMProvider(script=list(_ORCHESTRATOR_SCRIPT))
        self._subagent = StubLLMProvider(script=[_SUBAGENT_ANSWER])

    async def complete(self, request):
        provider = (
            self._subagent
            if request.kind is dm.LlmCallKind.SUBAGENT
            else self._orchestrator
        )
        return await provider.complete(request)

    def reset(self) -> None:
        # The fixture is module-scoped (one server for the whole battery),
        # so each test must restart the decision script at entry 1 — a run
        # from a previous test would otherwise burn the script and every
        # later submit would degrade through the stub's finish fallback.
        self._orchestrator.set_script(list(_ORCHESTRATOR_SCRIPT))
        self._subagent.set_script([_SUBAGENT_ANSWER])


def _get(path: str, headers: dict | None = None,
         timeout: float = 10.0) -> tuple[int, dict | str, dict]:
    req = urllib.request.Request(
        BASE + path,
        headers={"Accept": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
            headers_out = dict(resp.headers)
            return (
                resp.status,
                json.loads(body) if body.startswith(("{", "[")) else body,
                headers_out,
            )
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = body
        return exc.code, parsed, dict(exc.headers)


def _post(path: str, payload: str | dict,
          headers: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(payload) if isinstance(payload, dict) else payload
    req = urllib.request.Request(
        BASE + path,
        data=body.encode(),
        headers={"Content-Type": "application/json", **(headers or AUTH)},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60.0) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def _submit_body(goal: str = "wire goal",
                 idem: str | None = None) -> dict:
    body = {
        "goal": goal,
        "agents": [{"name": "a1", "role": "work"}],
    }
    if idem:
        body["idempotency_key"] = idem
    return body


@pytest.fixture(scope="module")
def wire():
    injector = build_injector(profile=DeployProfile.TEST)
    injector.binder.bind(
        AgentComputeConfig,
        to=AgentComputeConfig(
            llm=LlmConfig(base_url="stub", api_key="stub",
                          default_model="stub"),
            orchestration=AgentComputeOrchestrationConfig(
                poll_interval=0.01, poll_jitter=0.0, slice_interval=0.0,
            ),
        ),
    )
    injector.binder.bind(LLMProvider, to=_KindScriptLLM())

    previous = app.state.injector
    app.state.injector = injector
    config = uvicorn.Config(app, host=HOST, port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    else:
        pytest.fail("uvicorn did not start")
    global BASE
    BASE = f"http://{HOST}:{server.servers[0].sockets[0].getsockname()[1]}"

    worker = injector.get(AgentComputeWorker)
    stop = threading.Event()

    def tick_worker(done: threading.Event) -> None:
        import asyncio

        while not stop.is_set():
            try:
                asyncio.run(worker.run_once())
            except Exception as exc:
                print(f"[wire-worker] tick error: {exc!r}", flush=True)
            time.sleep(0.05)
        done.set()

    worker_done = threading.Event()
    worker_thread = threading.Thread(
        target=tick_worker, args=(worker_done,), daemon=True,
    )
    worker_thread.start()

    yield injector
    stop.set()
    assert worker_done.wait(timeout=10)
    server.should_exit = True
    thread.join(timeout=10)
    app.state.injector = previous


@pytest.fixture(autouse=True)
def _fresh_llm_script(wire):
    # Autouse + module-scope server: restart the scripted LLM per test so
    # every test's run walks the same spawn/wait/finish script from the top.
    wire.get(LLMProvider).reset()
    yield


class TestOverTheWire:

    def test_health_reports_ok_with_version(self, wire):
        status, body, _ = _get("/agentcompute/v1/health")
        assert status == 200
        assert body["status"] == "ok"
        assert body["version"] == "v1"

    def test_ready(self, wire):
        status, body, _ = _get("/agentcompute/v1/ready")
        assert status == 200
        assert body == {"ready": True}

    def test_anonymous_request_is_401(self, wire):
        status, _, _ = _get("/agentcompute/v1/runs")
        assert status == 401

    def test_unknown_run_is_404(self, wire):
        status, body, _ = _get("/agentcompute/v1/runs/does-not-exist",
                              headers=AUTH)
        assert status == 404
        assert body["detail"] == "run not found"

    def test_submit_without_agents_is_422(self, wire):
        status, body = _post("/agentcompute/v1/runs", {"goal": "no agents"})
        assert status == 422

    def test_async_job_full_cycle_over_wire(self, wire):
        from agentclaw.community.core.agentcompute.repository.job import JobRepository
        svc = wire.get(AgentComputeService)
        jobs = wire.get(JobRepository)
        status, submit = _post("/agentcompute/v1/runs",
                               _submit_body(goal="async cycle"))
        assert status == 200
        run_id = submit["run_id"]

        deadline = time.time() + 30
        while time.time() < deadline:
            status, body, _ = _get(f"/agentcompute/v1/runs/{run_id}",
                                   headers=AUTH)
            if status != 200:
                break
            if body["status"] != "RUNNING":
                assert body["status"] == "COMPLETED"
                assert body["final_answer"] == "done over the wire"
                break
            time.sleep(0.1)
        else:
            from agentclaw.community.core.agentcompute.repository.agent import AgentRepository
            from agentclaw.community.core.agentcompute.repository.guard import GuardEventRepository
            from agentclaw.community.core.agentcompute.repository.message import MessageRepository
            from agentclaw.community.core.agentcompute.repository.shard import ResultShardRepository
            from agentclaw.community.core.agentcompute.repository.round import RoundRepository
            rid = dm.RunId(run_id)
            agents = wire.get(AgentRepository).list_by_run(rid, dm.TenantId(TENANT))
            shards = wire.get(ResultShardRepository).list_by_run(rid, dm.TenantId(TENANT))
            rounds = wire.get(RoundRepository).list_by_run(rid, dm.TenantId(TENANT))
            guards = wire.get(GuardEventRepository).list_by_run(rid, dm.TenantId(TENANT))
            agents_s = [(a.name, a.status.value, a.error) for a in agents]
            guard_s = [(g.kind.value, g.details) for g in guards]
            pytest.fail(
                f"no terminal: pending={jobs.count_pending_jobs()} "
                f"agents={agents_s} shards={len(shards)} "
                f"rounds={len(rounds)} guards={guard_s}"
            )
        stored = svc.get_run(run_id, TENANT)
        assert stored is not None
        assert stored.status is dm.RunStatus.COMPLETED
        assert stored.final_answer == "done over the wire"

    def test_sync_run_over_wire(self, wire):
        status, body = _post(
            "/agentcompute/v1/runs?wait=true",
            _submit_body(goal="sync cycle", idem="wire-sync"),
        )
        assert status == 200
        assert body["status"] == "COMPLETED"
        assert body["final_answer"] == "done over the wire"

    def test_idempotency_join_over_wire(self, wire):
        body = _submit_body(goal="dedup", idem="wire-dedup")
        status, first = _post("/agentcompute/v1/runs", body)
        assert status == 200
        status, second = _post("/agentcompute/v1/runs", body)
        assert status == 200
        assert first["run_id"] == second["run_id"]
        # Join the run to its terminal state before handing control to the
        # next test: a leftover in-flight run would consume orchestrator
        # script entries across the script reset and desync the battery.
        run_id = first["run_id"]
        deadline = time.time() + 30
        while time.time() < deadline:
            status, body, _ = _get(f"/agentcompute/v1/runs/{run_id}",
                                   headers=AUTH)
            if status != 200:
                break
            if body["status"] != "RUNNING":
                assert body["status"] == "COMPLETED"
                break
            time.sleep(0.1)

    def test_sse_stream_emits_status_and_terminal_events(self, wire):
        status, submit = _post("/agentcompute/v1/runs",
                               _submit_body(goal="streamed"))
        assert status == 200
        run_id = submit["run_id"]

        req = urllib.request.Request(
            f"{BASE}/agentcompute/v1/runs/{run_id}/events",
            headers={"Accept": "text/event-stream", **AUTH},
        )
        events: list[tuple[str, dict]] = []
        with urllib.request.urlopen(req, timeout=30) as resp:
            assert resp.headers["Content-Type"].startswith("text/event-stream")
            current_event = None
            for raw in resp:
                line = raw.decode().rstrip("\n")
                if line.startswith("event:"):
                    current_event = line.split(":", 1)[1].strip()
                elif line.startswith("data:") and current_event:
                    payload = json.loads(line.split(":", 1)[1].strip())
                    events.append((current_event, payload))
                    current_event = None
                    if events[-1][0] == "terminal":
                        break

        kinds = [event for event, _ in events]
        assert kinds[0] == "status"
        assert "terminal" in kinds
        terminal = [payload for event, payload in events
                    if event == "terminal"][0]
        # SSE data lines carry StreamEventDTO JSON: {event, run_id, data}.
        assert terminal["data"]["status"] == "COMPLETED"

    def test_report_renders_html_over_wire(self, wire):
        status, submit = _post("/agentcompute/v1/runs",
                               _submit_body(goal="reported"))
        assert status == 200
        run_id = submit["run_id"]

        deadline = time.time() + 30
        status = 200
        while time.time() < deadline:
            status, body, _ = _get(f"/agentcompute/v1/runs/{run_id}",
                                   headers=AUTH)
            if status != 200:
                break
            if body["status"] != "RUNNING":
                break
            time.sleep(0.1)

        req = urllib.request.Request(
            f"{BASE}/agentcompute/v1/runs/{run_id}/report",
            headers=AUTH,
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert resp.status == 200
            assert "Agent Compute" in resp.read().decode()
