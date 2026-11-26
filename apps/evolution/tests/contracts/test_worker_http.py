"""06 worker HTTP contracts: real requests, auth, grants and persisted effects.

No 07 verifier, gate or promotion is installed. External experience/models/agents
are fixture ports; the JobChannel, jobs, operations, budget and SQLite are real.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from avernet_evolution.adapters.jobs import build_job_app
from avernet_evolution.core.evolution_run import RunStatus
from avernet_evolution.core.evolution_run.job_wire import decode_files

from .worker_fixture import (
    BOT,
    CANDIDATE_ID,
    FENCE,
    FILES,
    GRANTED,
    JOB_ID,
    OPERATION_ID,
    PATCH,
    REVISION,
    RUN_ID,
    STRATEGY,
    TOKENS,
    seed_channel,
)


@dataclass(frozen=True)
class Expected:
    status: int = 200
    json_contains: dict | None = None
    json_equals: Any = None

    def check(self, response):
        assert response.status_code == self.status, response.text
        if self.json_contains is not None:
            for key, value in self.json_contains.items():
                assert response.json()[key] == value
        if self.json_equals is not None:
            assert response.json() == self.json_equals


PREFIX = "/openapi/v1/evolution/jobs"
AUTH_ERROR = {"error": {"code": "job_unauthorized"}}
CAPABILITY_ERROR = {"error": {"code": "capability_not_granted"}}


def _assert_effect(kind):
    def check(response, channel):
        store = channel.store
        run = store.get_run(RUN_ID)
        if kind == "claim":
            assert run.status is RunStatus.RUNNING
            assert response.json()["fencing_token"]
            assert response.json()["granted"] == GRANTED
        elif kind == "rejected-claim":
            assert run.status is RunStatus.QUEUED
            assert store.open_job_for_worker("endpoint-worker") is None
        elif kind == "heartbeat":
            assert response.json()["lease_expires_at"]
            assert response.json()["cancelled"] is False
        elif kind == "files":
            assert decode_files(response.json()["files"]) == FILES
        elif kind == "submit":
            candidate = store.find_candidate(response.json()["candidate_id"])
            assert candidate["revision"] == REVISION
            assert store.submission_complete(RUN_ID, response.json()["candidate_id"])
            assert run.candidate_count == 2  # one seeded, one submitted via HTTP
            assert len(channel.genome.commits) == 1
            assert channel.genome.commits[0][0] == BOT
        elif kind in ("sessions", "feedback"):
            factory = channel.experience_factory
            assert factory.bots == [BOT]
            method, kwargs = factory.provider.calls[0]
            assert method == kind and kwargs["days"] == 3 and kwargs["limit"] == 4
        elif kind == "model":
            assert float(run.budget_used.usd) == 0.25
            assert channel.models.calls == [
                {
                    "messages": [{"role": "user", "content": "hello"}],
                    "model": "fixture-model",
                    "max_tokens": 32,
                }
            ]
        elif kind == "agent":
            operation = store.get_operation(RUN_ID, response.json()["operation_id"])
            assert operation.idempotency_key == "start-agent"
            assert channel.agents.calls == [
                {
                    "definition": "fixture-agent",
                    "workspace": "ws-fixture",
                    "prompt": "inspect",
                    "timeout_s": 10,
                }
            ]
        elif kind == "complete":
            assert run.status is RunStatus.COMPLETED and run.summary == {"summary": "done"}
            assert store.get_operation(RUN_ID, OPERATION_ID).status.value == "cancelled"
        elif kind == "fail":
            assert run.status is RunStatus.QUEUED and run.attempt == 2
            assert store.queued_job([STRATEGY])[0].id != JOB_ID
        elif kind == "cancel":
            assert store.get_operation(RUN_ID, OPERATION_ID).status.value == "cancelled"
        elif kind == "log":
            events = store.events.for_run(RUN_ID)
            assert events[-1]["kind"] == "worker.log.received"
            assert events[-1]["authority"] == "worker-reported"
            assert "fixture log" not in str(events)
        elif kind == "artifact":
            assert store.get_artifact(RUN_ID, response.json()["artifact_id"]) == b"fixture artifact"

    return check


def _assert_rejected(response, channel, *, queued=False):
    run = channel.store.get_run(RUN_ID)
    assert run.status is (RunStatus.QUEUED if queued else RunStatus.RUNNING)
    if queued:
        assert channel.store.open_job_for_worker("endpoint-worker") is None
    assert not channel.store.events.for_run(RUN_ID)
    assert (
        channel.store.conn.execute(
            "SELECT COUNT(*) FROM evolution_artifact WHERE run_id=?", (RUN_ID,)
        ).fetchone()[0]
        == 0
    )
    assert run.candidate_count == 1 and float(run.budget_used.usd) == 0
    assert not channel.genome.commits
    assert not channel.models.calls and not channel.agents.calls
    assert not channel.experience_factory.bots
    assert channel.store.get_operation(RUN_ID, OPERATION_ID).status.value == "queued"


_ROUTES = [
    (
        "POST",
        ":heartbeat",
        {},
        "heartbeat",
        Expected(json_contains={"cancelled": False}),
    ),
    ("POST", "/files", {}, "files", Expected()),
    (
        "POST",
        "/candidates",
        {"patch": PATCH},
        "submit",
        Expected(json_contains={"revision": REVISION}),
    ),
    (
        "GET",
        "/candidates/{candidate_id}/verdict",
        {},
        "verdict",
        Expected(
            json_contains={
                "candidate": CANDIDATE_ID,
                "revision": REVISION,
                "status": "pending",
                "aggregates": {},
                "reasons": [],
            }
        ),
    ),
    (
        "POST",
        ":complete",
        {"summary": {"summary": "done"}},
        "complete",
        Expected(status=204),
    ),
    ("POST", ":fail", {"reason": "retry fixture"}, "fail", Expected(status=204)),
    (
        "POST",
        "/experience/sessions",
        {"days": 3, "limit": 4},
        "sessions",
        Expected(json_equals=[{"episode_id": "endpoint-episode"}]),
    ),
    (
        "POST",
        "/experience/feedback",
        {"days": 3, "limit": 4},
        "feedback",
        Expected(json_equals=[{"kind": "correction", "episode_id": "endpoint-episode"}]),
    ),
    (
        "POST",
        "/models:complete",
        {
            "messages": [{"role": "user", "content": "hello"}],
            "model": "fixture-model",
            "max_tokens": 32,
        },
        "model",
        Expected(
            json_equals={
                "text": "fixture answer",
                "model": "fixture-model",
                "cost_usd": 0.25,
            }
        ),
    ),
    (
        "POST",
        "/agents:start",
        {
            "definition": "fixture-agent",
            "workspace_id": "ws-fixture",
            "prompt": "inspect",
            "idempotency_key": "start-agent",
            "timeout_s": 10,
        },
        "agent",
        Expected(),
    ),
    (
        "GET",
        "/operations/{operation_id}",
        {},
        "operation",
        Expected(
            json_contains={
                "id": OPERATION_ID,
                "kind": "agent_session",
                "status": "queued",
            }
        ),
    ),
    (
        "POST",
        "/operations/{operation_id}:cancel",
        {},
        "cancel",
        Expected(json_equals={"id": OPERATION_ID, "status": "cancelled"}),
    ),
    (
        "POST",
        "/log",
        {"lines": [{"level": "info", "message": "fixture log"}]},
        "log",
        Expected(status=204),
    ),
    ("PUT", "/artifacts/{name}", {}, "artifact", Expected()),
]


@pytest.fixture
def channel_factory():
    channels = []

    def create(**kwargs):
        channel = seed_channel(**kwargs)
        channels.append(channel)
        return channel

    yield create
    for channel in channels:
        channel.store.conn.close()


async def request(channel, method, suffix, payload, fence):
    path = f"{PREFIX}/jobs/{JOB_ID}{suffix}".format(
        candidate_id=CANDIDATE_ID, operation_id=OPERATION_ID, name="trace.txt"
    )
    kwargs = (
        {"json": {**payload, "fencing_token": fence}}
        if method == "POST"
        else {"params": {"fencing_token": fence}}
    )
    if method == "PUT":
        kwargs["content"] = b"fixture artifact"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_job_app(channel, prefix=PREFIX)),
        base_url="http://worker.test",
    ) as client:
        return await client.request(method, path, **kwargs)


@pytest.mark.parametrize("method,suffix,payload,kind,expected", _ROUTES, ids=[r[3] for r in _ROUTES])
@pytest.mark.parametrize("valid", [True, False], ids=["current-fence", "stale-fence"])
async def test_worker_route(channel_factory, method, suffix, payload, kind, expected, valid):
    channel = channel_factory()
    response = await request(channel, method, suffix, payload, FENCE if valid else "stale-fence")
    if valid:
        expected.check(response)
        _assert_effect(kind)(response, channel)
    else:
        Expected(status=401, json_contains=AUTH_ERROR).check(response)
        _assert_rejected(response, channel)


@pytest.mark.parametrize("valid", [True, False], ids=["worker-token", "invalid-token"])
async def test_claim(channel_factory, valid):
    channel = channel_factory(queued=True)
    worker_id = "endpoint-worker"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_job_app(channel, prefix=PREFIX)),
        base_url="http://worker.test",
    ) as client:
        response = await client.post(
            f"{PREFIX}/jobs:claim",
            json={
                "worker_id": worker_id,
                "strategy_refs": [STRATEGY],
                "token": TOKENS.issue(worker_id) if valid else "invalid",
            },
        )
    expected = (
        Expected(json_contains={"job_id": JOB_ID, "run_id": RUN_ID, "strategy_ref": STRATEGY})
        if valid
        else Expected(status=401, json_contains=AUTH_ERROR)
    )
    expected.check(response)
    _assert_effect("claim" if valid else "rejected-claim")(response, channel)


_CAPABILITY_ROUTES = [
    r for r in _ROUTES if r[3] in {"submit", "verdict", "sessions", "feedback", "model", "agent"}
]


@pytest.mark.parametrize(
    "method,suffix,payload,kind,expected", _CAPABILITY_ROUTES, ids=[r[3] for r in _CAPABILITY_ROUTES]
)
async def test_missing_capability(channel_factory, method, suffix, payload, kind, expected):
    channel = channel_factory(granted=[])
    response = await request(channel, method, suffix, payload, FENCE)
    Expected(status=403, json_contains=CAPABILITY_ERROR).check(response)
    _assert_rejected(response, channel)


_POST_ROUTES = [r for r in _ROUTES if r[0] == "POST"]


@pytest.mark.parametrize(
    "method,suffix,payload,kind,expected", _POST_ROUTES, ids=[r[3] for r in _POST_ROUTES]
)
@pytest.mark.parametrize("empty_fence", [None, ""], ids=["null", "empty"])
async def test_preclaim_fence(channel_factory, method, suffix, payload, kind, expected, empty_fence):
    channel = channel_factory(queued=True)
    response = await request(channel, method, suffix, payload, empty_fence)
    Expected(status=401, json_contains=AUTH_ERROR).check(response)
    _assert_rejected(response, channel, queued=True)
