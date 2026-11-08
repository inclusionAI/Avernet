"""agentswarm HTTP surface for the bot-create human gate (tasks 3.1/3.2).

Serializer pins: run detail serializes the additive ``authorization``
descriptor per parked agent (independently, one per agent) and keeps every
pre-existing field untouched for agents not parked (fast path
included). Stream pins: the SSE ``agent`` event kind carries the gate
deltas — one event per gate crossing, none for unchanged polls, none for
the auto-issued/normal provisioning path, and a stream attaching mid-park
surfaces the pending gate exactly once.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import injector

from agentclaw.community.adapters.http.agentswarm.router import (
    authorization_gate_deltas, register_exception_handlers, router,
)
from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.service.protocols import (
    AgentAuthorizationState, AgentDetailItem, AgentMessageDetail,
    AgentResultDetail, BotAuthorizationDescriptor,
    GuardEventDetailItem, RoundDetailItem, RunDetail, RunEvents,
    SubmitRunRequest,
)
from agentclaw.community.core.agentswarm.repository.audit.impl import (
    AgentSwarmAuditWriter, AuditAction,
)
from agentclaw.community.plugin_api.auth import AuthPlugin, AuthenticatedIdentity
from agentclaw.community.api.agentswarm_service import (
    AgentSwarmServiceProtocol,
)

TENANT = "tenant-a"
STAFF_ID = "staff-1"

GATE = dm.BotState.AWAITING_AUTHORIZATION.value
DEADLINE = "2026-11-20T10:10:00"
DEADLINE_2 = "2026-11-20T11:11:00"


class StubAgentSwarmService:

    def __init__(self) -> None:
        self.run = dm.Run(
            run_id=dm.RunId("run-1"),
            tenant=dm.TenantId(TENANT),
            goal="analyze the build logs",
            agent_catalog=(dm.AgentSpec(name="r1", role="research"),),
            provider=dm.ProviderKind("stub"),
            request_metadata=dm.RequestMetadata(),
            status=dm.RunStatus.RUNNING,
            started_at=dm.EpochMillis(1),
        )
        self.detail: RunDetail | None = None
        self.events_queue: list[RunEvents] = []
        self.run_events_calls: int = 0

    async def submit_async(self, req: SubmitRunRequest) -> str:
        raise NotImplementedError

    async def submit_sync(self, req: SubmitRunRequest) -> dm.Run:
        raise NotImplementedError

    def get_run(self, run_id: str, tenant: str) -> dm.Run | None:
        if run_id != self.run.run_id or tenant != TENANT:
            return None
        return self.run

    def list_runs(self, tenant: str, status: dm.RunStatus | None = None,
                  *, page: int = 1, page_size: int = 20) -> dm.RunPage:
        raise NotImplementedError

    def run_events(self, run_id: str, tenant: str, after_agent_id: int = 0,
                   after_round_id: int = 0, after_event: int = 0) -> RunEvents:
        self.run_events_calls += 1
        if self.events_queue:
            return self.events_queue.pop(0)
        # Drain: terminal so the SSE loop ends deterministically.
        return RunEvents(
            agents=(), rounds=(), guards=(),
            run_status=dm.RunStatus.COMPLETED,
            terminal=dm.RunStatus.COMPLETED,
        )

    def get_run_detail(self, run_id: str, tenant: str) -> RunDetail | None:
        if run_id != self.run.run_id or tenant != TENANT:
            return None
        return self.detail

    async def cancel(self, run_id: str, tenant: str) -> str:
        raise NotImplementedError


class StubAuthPlugin:

    def __init__(self) -> None:
        pass

    async def resolve_user_from_request(self, ctx) -> AuthenticatedIdentity:
        return AuthenticatedIdentity(
            id="auth-user-1",
            operatorName="auth-user-1",
            outUserNo=STAFF_ID,
            nickName="Auth User",
            tntInstId=TENANT,
        )

    async def get_login_user(self, cookie: str, referer: str | None = None):
        raise NotImplementedError

    def is_operator_allowed(self, staff_id: str) -> bool:
        return True

    async def authorize_entity_access(self, ctx, requested_entity_id,
                                      requested_entity_type):
        return requested_entity_id, requested_entity_type


class RecordingAuditWriter:

    def record(self, *, tenant: str, actor: str, action: AuditAction,
               reason: str | None = None, run_id: str | None = None,
               source: str = "http") -> None:
        pass


@pytest.fixture()
def stub():
    return StubAgentSwarmService()


@pytest.fixture()
def client(stub) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    register_exception_handlers(app)
    inj = injector.Injector()
    inj.binder.bind(AgentSwarmServiceProtocol, to=stub)
    inj.binder.bind(AuthPlugin, to=StubAuthPlugin())
    inj.binder.bind(AgentSwarmAuditWriter, to=RecordingAuditWriter())
    app.state.injector = inj
    return TestClient(app)


def _read_sse(resp) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    event_name = None
    for line in resp.iter_lines():
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            import json
            events.append((event_name, json.loads(line.split(":", 1)[1])))
    return events


def _snapshot(agent_id: str, bot_id: str, bot_state: str, *,
              name: str = "scout",
              authorization: BotAuthorizationDescriptor | None = None,
              ) -> AgentAuthorizationState:
    return AgentAuthorizationState(
        agent_id=agent_id, name=name, status="RUNNING", kind="bot",
        spawn_round=0, bot_id=bot_id, bot_state=bot_state,
        authorization=authorization,
    )


def _descriptor(iframe: str, redirect: str, deadline: str,
                application_id: str = "") -> BotAuthorizationDescriptor:
    return BotAuthorizationDescriptor(
        state=dm.BotState.AWAITING_AUTHORIZATION.value,
        iframe_url=iframe, redirect_url=redirect, deadline=deadline,
        application_id=application_id,
    )


def _parked_snapshot(agent_id: str, bot_id: str, iframe: str,
                     deadline: str) -> AgentAuthorizationState:
    return _snapshot(
        agent_id, bot_id, GATE,
        authorization=_descriptor(iframe, f"{iframe}/done", deadline),
    )


# ── run detail serialization ────────────────────────────────────────────────


def _mk_detail_item(name: str, *, agent_id: str, bot_id: str | None,
                    bot_state: str | None,
                    authorization: BotAuthorizationDescriptor | None,
                    ) -> AgentDetailItem:
    return AgentDetailItem(
        agent_id=agent_id, name=name, role="research", kind="bot",
        status="RUNNING", parent_agent_id=None, depth=0, spawn_round=0,
        idempotency_key=f"idem-{name}", attempts=1, bot_id=bot_id,
        bot_state=bot_state, error=None, output={"ok": True},
        started_at=1, ended_at=None,
        result=AgentResultDetail(payload=None, artifact_key=None,
                                 routed_at=1234),
        attempt_ledger=(), messages=(
            AgentMessageDetail(
                message_id="msg-1", agent_id=agent_id, seq=1,
                role="assistant", content="done", decision=None,
                token_usage=None, artifact_key=None, created_at=20,
            ),
        ),
        authorization=authorization,
    )


def _detail_with(agents: tuple[AgentDetailItem, ...]) -> RunDetail:
    return RunDetail(
        run_id="run-1", tenant=TENANT, goal="analyze the build logs",
        status="RUNNING", termination_reason=None,
        provider="stub", final_answer=None, error=None,
        started_at=1, ended_at=None,
        agent_catalog=(dm.AgentSpec(name="r1", role="research"),),
        agents=agents,
        rounds=(RoundDetailItem(
            round_id="round-1", stage_index=0, orchestrator_steps=2,
            slowest_attempt_steps=3, cohort_agent_ids=("agent-1",),
            started_at=10, ended_at=None,
        ),),
        guards=(GuardEventDetailItem(
            event_id="ev-1", agent_id=None, kind="width_exceeded",
            details={"max": 8}, created_at=30,
        ),),
    )


class TestRunDetailAuthorization:

    def test_parked_agents_serialize_independent_descriptors(
            self, client, stub):
        stub.detail = _detail_with((
            _mk_detail_item(
                "scout", agent_id="agent-1", bot_id="agent_swarm_a1",
                bot_state=GATE,
                authorization=_descriptor(
                    "https://tcauthmng.example/consent?app=p1",
                    "https://tcauthmng.example/done-p1",
                    DEADLINE,
                    "sim-passport-a1",
                ),
            ),
            _mk_detail_item(
                "writer", agent_id="agent-2", bot_id="agent_swarm_a2",
                bot_state=GATE,
                authorization=_descriptor(
                    "https://tcauthmng.example/consent?app=p2",
                    "https://tcauthmng.example/done-p2",
                    DEADLINE_2,
                    "sim-passport-a2",
                ),
            ),
        ))
        resp = client.get(
            "/openapi/v1/agentswarm/runs/run-1/detail",
            params={"tenant": TENANT},
        )
        assert resp.status_code == 200
        scout, writer = resp.json()["agents"]

        assert scout["bot_state"] == GATE
        assert scout["authorization"] == {
            "state": GATE,
            "iframe_url": "https://tcauthmng.example/consent?app=p1",
            "redirect_url": "https://tcauthmng.example/done-p1",
            "deadline": DEADLINE,
            "application_id": "sim-passport-a1",
        }
        assert writer["authorization"] == {
            "state": GATE,
            "iframe_url": "https://tcauthmng.example/consent?app=p2",
            "redirect_url": "https://tcauthmng.example/done-p2",
            "deadline": DEADLINE_2,
            "application_id": "sim-passport-a2",
        }
        # Independence: the two payloads never bleed into each other.
        assert (scout["authorization"]["iframe_url"]
                != writer["authorization"]["iframe_url"])
        assert (scout["authorization"]["deadline"]
                != writer["authorization"]["deadline"])

    def test_nonparked_agent_response_is_prechange_shape(
            self, client, stub):
        stub.detail = _detail_with((
            _mk_detail_item(
                "fast", agent_id="agent-1", bot_id="agent_swarm_a1",
                bot_state=dm.BotState.READY.value, authorization=None,
            ),
        ))
        resp = client.get(
            "/openapi/v1/agentswarm/runs/run-1/detail",
            params={"tenant": TENANT},
        )
        assert resp.status_code == 200
        agent = resp.json()["agents"][0]

        # Regression: every pre-existing field keeps its pre-change value,
        # and the additive gate field is null for a non-parked agent.
        assert agent["agent_id"] == "agent-1"
        assert agent["name"] == "fast"
        assert agent["role"] == "research"
        assert agent["kind"] == "bot"
        assert agent["status"] == "RUNNING"
        assert agent["parent_agent_id"] is None
        assert agent["depth"] == 0
        assert agent["spawn_round"] == 0
        assert agent["idempotency_key"] == "idem-fast"
        assert agent["attempts"] == 1
        assert agent["bot_id"] == "agent_swarm_a1"
        assert agent["bot_state"] == "READY"
        assert agent["error"] is None
        assert agent["output"] == {"ok": True}
        assert agent["started_at"] == 1
        assert agent["ended_at"] is None
        assert agent["result"]["routed_at"] == 1234
        assert agent["attempt_ledger"] == []
        assert len(agent["messages"]) == 1
        assert agent["authorization"] is None


# ── SSE gate deltas ──────────────────────────────────────────────────────────


def _events(snapshot: tuple[AgentAuthorizationState, ...],
            status: dm.RunStatus = dm.RunStatus.RUNNING) -> RunEvents:
    return RunEvents(
        agents=(), rounds=(), guards=(),
        run_status=status,
        terminal=None if status is dm.RunStatus.RUNNING else status,
        agent_authorizations=snapshot,
    )


class TestAuthorizationGateDeltasPure:

    def test_entering_the_gate_emits_with_the_descriptor(self):
        deltas = authorization_gate_deltas({}, (
            _parked_snapshot("agent-1", "agent_swarm_a1",
                             "https://tcauthmng.example/consent?app=p1",
                             DEADLINE),
        ))
        (delta,) = deltas
        assert delta == {
            "agent_id": "agent-1", "name": "scout", "status": "RUNNING",
            "kind": "bot", "spawn_round": 0,
            "bot_id": "agent_swarm_a1", "bot_state": GATE,
            "authorization": {
                "state": GATE,
                "iframe_url": "https://tcauthmng.example/consent?app=p1",
                "redirect_url":
                    "https://tcauthmng.example/consent?app=p1/done",
                "deadline": DEADLINE,
                "application_id": "",
            },
        }

    def test_unchanged_park_emits_nothing(self):
        parked = _parked_snapshot("agent-1", "agent_swarm_a1",
                                  "https://x.example/c", DEADLINE)
        assert authorization_gate_deltas(
            {"agent-1": GATE}, (parked,),
        ) == []

    def test_unchanged_provisioning_emits_nothing(self):
        fast = _snapshot("agent-1", "agent_swarm_a1", "PROVISIONING")
        assert authorization_gate_deltas(
            {"agent-1": "PROVISIONING"}, (fast,),
        ) == []

    def test_auto_issue_fast_path_provisioning_to_ready_emits_nothing(self):
        fast = _snapshot("agent-1", "agent_swarm_a1", "READY")
        assert authorization_gate_deltas(
            {"agent-1": "PROVISIONING"}, (fast,),
        ) == []

    def test_issued_exit_emits_once_without_descriptor_fields(self):
        resumed = _snapshot("agent-1", "agent_swarm_a1", "PROVISIONING")
        (delta,) = authorization_gate_deltas(
            {"agent-1": GATE}, (resumed,),
        )
        assert delta["bot_state"] == "PROVISIONING"
        assert "authorization" not in delta

    def test_rejected_and_expired_exits_describe_the_terminal_state(self):
        rejected = _snapshot("agent-1", "agent_swarm_a1",
                            dm.BotState.AUTHORIZATION_REJECTED.value)
        expired = _snapshot("agent-2", "agent_swarm_a2",
                            dm.BotState.AUTHORIZATION_EXPIRED.value)
        deltas = authorization_gate_deltas(
            {"agent-1": GATE, "agent-2": GATE}, (rejected, expired),
        )
        by_agent = {d["agent_id"]: d for d in deltas}
        assert by_agent["agent-1"]["bot_state"] == "AUTHORIZATION_REJECTED"
        assert by_agent["agent-2"]["bot_state"] == "AUTHORIZATION_EXPIRED"
        assert all("authorization" not in d for d in deltas)

    def test_two_agents_cross_independently(self):
        still_parked = _parked_snapshot("agent-1", "agent_swarm_a1",
                                        "https://x.example/1", 1)
        exiting = _snapshot("agent-2", "agent_swarm_a2", "PROVISIONING")
        deltas = authorization_gate_deltas(
            {"agent-2": GATE}, (still_parked, exiting),
        )
        assert [d["agent_id"] for d in deltas] == ["agent-1", "agent-2"]

    def test_issued_exit_to_ready_directly_emits_once(self):
        ready = _snapshot("agent-1", "agent_swarm_a1", "READY")
        deltas = authorization_gate_deltas({"agent-1": GATE}, (ready,))
        assert len(deltas) == 1
        assert deltas[0]["bot_state"] == "READY"


class TestStreamEmitsGateDeltas:

    def test_stream_park_then_unchanged_then_issued(self, client, stub):
        parked = _parked_snapshot(
            "agent-1", "agent_swarm_a1",
            "https://tcauthmng.example/consent?app=p1", DEADLINE,
        )
        stub.events_queue = [
            _events((parked,)),
            _events((parked,)),
            _events((_snapshot("agent-1", "agent_swarm_a1", "PROVISIONING"),)),
        ]
        with client.stream(
            "GET", "/openapi/v1/agentswarm/runs/run-1/events",
            params={"tenant": TENANT},
        ) as resp:
            events = _read_sse(resp)

        assert events[0][0] == "status"
        deltas = [data for name, data in events
                  if name == "agent" and "bot_state" in data["data"]]
        assert [name for name, _ in events] == [
            "status", "agent", "progress", "progress",
            "agent", "progress", "terminal",
        ]
        # Exactly one enter, one exit; the unchanged middle poll emitted
        # none.
        assert len(deltas) == 2
        assert deltas[0]["data"]["bot_state"] == GATE
        assert deltas[0]["data"]["authorization"]["iframe_url"] == \
            "https://tcauthmng.example/consent?app=p1"
        assert deltas[1]["data"]["bot_state"] == "PROVISIONING"
        assert "authorization" not in deltas[1]["data"]

    def test_stream_resolves_rejected_terminal_state(self, client, stub):
        parked = _parked_snapshot("agent-1", "agent_swarm_a1",
                                  "https://x.example/c", DEADLINE)
        rejected = _snapshot(
            "agent-1", "agent_swarm_a1",
            dm.BotState.AUTHORIZATION_REJECTED.value,
        )
        stub.events_queue = [
            _events((parked,)),
            _events((rejected,)),
        ]
        with client.stream(
            "GET", "/openapi/v1/agentswarm/runs/run-1/events",
            params={"tenant": TENANT},
        ) as resp:
            events = _read_sse(resp)

        deltas = [data["data"] for name, data in events
                  if name == "agent" and "bot_state" in data["data"]]
        assert len(deltas) == 2
        assert deltas[1]["bot_state"] == "AUTHORIZATION_REJECTED"
        assert "authorization" not in deltas[1]

    def test_stream_fast_path_never_emits_a_gate_delta(self, client, stub):
        stub.events_queue = [
            _events((_snapshot("agent-1", "agent_swarm_a1", "PROVISIONING"),)),
            _events((_snapshot("agent-1", "agent_swarm_a1", "READY"),)),
        ]
        with client.stream(
            "GET", "/openapi/v1/agentswarm/runs/run-1/events",
            params={"tenant": TENANT},
        ) as resp:
            events = _read_sse(resp)

        assert all(
            "bot_state" not in data["data"] for name, data in events
            if name == "agent"
        )

    def test_stream_attaching_mid_park_surfaces_the_gate_once(
            self, client, stub):
        parked = _parked_snapshot(
            "agent-1", "agent_swarm_a1",
            "https://tcauthmng.example/consent?app=p1", DEADLINE,
        )
        stub.events_queue = [
            _events((parked,)),
            _events((parked,)),
            _events((parked,)),
        ]
        with client.stream(
            "GET", "/openapi/v1/agentswarm/runs/run-1/events",
            params={"tenant": TENANT},
        ) as resp:
            events = _read_sse(resp)

        deltas = [data["data"] for name, data in events
                  if name == "agent" and "bot_state" in data["data"]]
        assert len(deltas) == 1
        assert deltas[0]["authorization"]["deadline"] == DEADLINE