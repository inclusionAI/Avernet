"""AvernetSubagent park semantics at the human gate.

Covers the subagent's side of the human-authorization gate:
setup parks (raises ParkedAwaitingAuthorization) the moment
``bot_status`` reports ``AWAITING_AUTHORIZATION`` instead of sleeping
the poll budget toward ``setup_timeout``; the park continuation is the
SAME setup re-invoked on the SAME persona (create skipped, one cheap
status poll per recheck) and finishes with the same bot id on
issuance; a fresh slice's subagent re-derives the same idempotency key
(the D16 adopt input); the authorization's terminal answers raise the
non-retryable marker; platform-side terminal failures keep their
retryable classification; and a creation that never reports
``AWAITING_AUTHORIZATION`` (auto-issued fast path, D7) runs the
pre-change flow exactly.

Conventions follow test_bot_subagent.py: sync test defs driving async
code through asyncio.run, hand-written fakes (no MagicMock).
"""
from __future__ import annotations

import asyncio
import time

import pytest

from agentclaw.community.core.agentswarm.domain.models import (
    AgentId, AgentSpec, EpochMillis, NonRetryableProvisioningError,
    ProviderKind, RequestMetadata, Run, RunId, RunStatus, TenantId,
)
from agentclaw.community.plugin_api.agentswarm.subagent import (
    ParkedAwaitingAuthorization,
)
from agentclaw.community.plugins.community.agentswarm.bot_subagent import (
    AvernetSubagent,
)

_AWAITING = "AWAITING_AUTHORIZATION"
_AGENT_ID = AgentId("agent-1")
_KEY = "run-42:agent-1:1"


def _now_ms():
    return int(time.time() * 1000)


def _mk_run(run_id="run-42") -> Run:
    return Run(
        run_id=RunId(run_id),
        tenant=TenantId("tenant-a"),
        goal="goal",
        agent_catalog=(),
        provider=ProviderKind("avernet"),
        request_metadata=RequestMetadata(),
        status=RunStatus.RUNNING,
        started_at=EpochMillis(_now_ms()),
    )


def _mk_spec(name="scout") -> AgentSpec:
    return AgentSpec(name=name, role="worker")


class _Descriptor:
    """Mirrors the host's BotAuthorizationDescriptor facts the status
    answer carries on ``AWAITING_AUTHORIZATION``."""

    def __init__(self, deadline: str = "2026-10-23T12:10:00+00:00") -> None:
        self.state = _AWAITING
        self.iframe_url = "https://consent.example/iframe"
        self.redirect_url = "https://consent.example/redirect"
        self.deadline = deadline


class _StatusWithDescriptor(str):
    """``bot_status`` answer carrying the descriptor (host.py's
    BotStatusResult shape): a plain ``str`` for equality, plus the
    ``authorization`` attribute the subagent reads."""

    __slots__ = ("authorization",)

    def __new__(cls, state: str, authorization: _Descriptor):
        s = super().__new__(cls, state)
        s.authorization = authorization
        return s


class FakePlatform:
    """BotPlatform double: scripted statuses (last one holds), every
    call recorded, per-key create idempotency observable by the test."""

    def __init__(self, statuses=None, bot_id="bot-42"):
        self.statuses = list(statuses or ["READY"])
        self.bot_id = bot_id
        self.created: list[tuple] = []
        self.status_queries: list[str] = []
        self.deletes: list[str] = []

    def create_bot(self, *, spec, idempotency_key, owner_id=""):
        self.created.append((spec, idempotency_key))
        return self.bot_id

    def bot_status(self, bot_id, owner_id=""):
        self.status_queries.append(bot_id)
        if len(self.statuses) > 1:
            return self.statuses.pop(0)
        return self.statuses[0]

    def delete_bot(self, bot_id, owner_id=""):
        self.deletes.append(bot_id)
        return True

    def chat(self, bot_id, message, owner_id="", session_key=None,
             cancel_event=None):
        return '{"result": "done"}'


class TestParksAtAwaitingAuthorization:

    def test_awaiting_status_parks_immediately_without_burning_poll_budget(self):
        # One status call, then the park marker — no interval sleep, no
        # poll toward the ready deadline: the pre-change loop would have
        # slept 1s between polls until the whole window expired.
        async def main():
            gw = FakePlatform(statuses=[_AWAITING])
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw, _AGENT_ID)
            started = time.monotonic()
            try:
                await sub.setup()
            except ParkedAwaitingAuthorization as exc:
                return gw, sub, exc, time.monotonic() - started
            raise AssertionError("setup did not park")

        gw, sub, exc, elapsed = asyncio.run(main())
        assert "bot-42 parked awaiting human authorization" in str(exc)
        assert len(gw.status_queries) == 1
        assert elapsed < 0.5
        assert sub._bot_id == "bot-42"
        assert len(gw.created) == 1

    def test_park_after_issued_states_completes_with_the_same_bot_id(self):
        # The SAME setup continuation (same instance, identity already
        # minted) resumes on issuance: create is never re-run — one
        # create for the whole park→resume arc.
        async def main():
            gw = FakePlatform(statuses=[
                _AWAITING, _AWAITING, "PROVISIONING", "READY",
            ])
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw, _AGENT_ID)
            for _ in range(2):
                with pytest.raises(ParkedAwaitingAuthorization):
                    await sub.setup()
            await sub.setup()
            return gw, sub

        gw, sub = asyncio.run(main())
        assert [(s.name, k) for s, k in gw.created] == [("scout", _KEY)]
        assert sub._bot_id == "bot-42"
        assert sub.executor_id == "bot-42"

    def test_window_deadline_from_the_descriptor_rides_the_marker(self):
        async def main():
            gw = FakePlatform(statuses=[
                _StatusWithDescriptor(_AWAITING, _Descriptor(
                    deadline="2026-10-23T18:00:05+00:00")),
            ])
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw, _AGENT_ID)
            try:
                await sub.setup()
            except ParkedAwaitingAuthorization as exc:
                return exc
            raise AssertionError("setup did not park")

        exc = asyncio.run(main())
        assert "window deadline 2026-10-23T18:00:05+00:00" in str(exc)

    def test_plain_awaiting_string_parks_without_descriptor(self):
        # The descriptor is optional plugin surface (getattr): a platform
        # answer that is the bare state string parks with the plain
        # message — no deadline suffix, no crash.
        async def main():
            gw = FakePlatform(statuses=[_AWAITING])
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw, _AGENT_ID)
            try:
                await sub.setup()
            except ParkedAwaitingAuthorization as exc:
                return exc
            raise AssertionError("setup did not park")

        exc = asyncio.run(main())
        assert "window deadline" not in str(exc)


class TestParkSurvivesSliceRotationWithSameIdentity:

    def test_next_slice_subagent_rederives_the_same_key_and_resumes(self):
        # D16: a fresh slice's fresh subagent (what the factory builds)
        # re-derives the identical idempotency key — the host's adopt
        # input — never a replacement identity, and the resumed
        # continuation completes on the same bot id.
        async def main():
            gw = FakePlatform(statuses=[
                _AWAITING, _AWAITING, "PROVISIONING", "READY",
            ])
            first = AvernetSubagent(_mk_spec(), _mk_run(), gw, _AGENT_ID)
            with pytest.raises(ParkedAwaitingAuthorization):
                await first.setup()
            # Slice boundary: the pool is stopped (first's camping
            # recheck unwinds), the next slice respawns the same agent.
            # The fresh instance re-creates under the same key: adopt.
            second = AvernetSubagent(_mk_spec(), _mk_run(), gw, _AGENT_ID)
            with pytest.raises(ParkedAwaitingAuthorization):
                await second.setup()
            await second.setup()
            return gw, second

        gw, resumed = asyncio.run(main())
        keys = [key for _, key in gw.created]
        assert keys == [_KEY, _KEY]
        assert resumed._bot_id == "bot-42"
        assert resumed.executor_id == "bot-42"


class TestTerminalAuthorizationAnswers:

    def test_human_rejected_raises_non_retryable_with_state(self):
        async def main():
            gw = FakePlatform(statuses=["AUTHORIZATION_REJECTED"])
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw, _AGENT_ID)
            try:
                await sub.setup()
            except NonRetryableProvisioningError as exc:
                return exc
            raise AssertionError("setup did not fail")

        exc = asyncio.run(main())
        assert "AUTHORIZATION_REJECTED" in str(exc)

    def test_window_elapsed_raises_non_retryable_with_state(self):
        async def main():
            gw = FakePlatform(statuses=["AUTHORIZATION_EXPIRED"])
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw, _AGENT_ID)
            try:
                await sub.setup()
            except NonRetryableProvisioningError as exc:
                return exc
            raise AssertionError("setup did not fail")

        exc = asyncio.run(main())
        assert "AUTHORIZATION_EXPIRED" in str(exc)

    def test_platform_terminal_failures_stay_retryable(self):
        # Pre-change classification is untouched for the
        # platform-side terminal states: CREATE_FAILED is the pool's to
        # retry with a fresh persona, not the gate's to answer.
        async def main():
            gw = FakePlatform(statuses=["CREATE_FAILED"])
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw, _AGENT_ID)
            try:
                await sub.setup()
            except RuntimeError as exc:
                return exc
            raise AssertionError("setup did not fail")

        exc = asyncio.run(main())
        assert "CREATE_FAILED" in str(exc)
        assert not isinstance(exc, NonRetryableProvisioningError)


class TestIssuedFastPath:

    def test_never_awaiting_status_runs_the_pre_change_flow(self):
        # D7: a creation whose passport is issued inline (or approved
        # before any poll) never enters the gate — one create, one
        # poll-to-READY, no park marker, pre-change identity.
        async def main():
            gw = FakePlatform(statuses=["PROVISIONING", "READY"])
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw, _AGENT_ID)
            await sub.setup()
            return gw, sub

        gw, sub = asyncio.run(main())
        assert [(s.name, k) for s, k in gw.created] == [("scout", _KEY)]
        assert gw.status_queries == ["bot-42", "bot-42"]
        assert sub.executor_id == "bot-42"

    def test_descriptor_carrying_issued_status_does_not_park(self):
        # Consent URLs in an already-issued creation's status answer
        # must not trip the gate: the gate keys off the
        # application's PENDING status, never the presence of
        # descriptor facts.
        async def main():
            gw = FakePlatform(statuses=[
                _StatusWithDescriptor("PROVISIONING", _Descriptor()),
                "READY",
            ])
            sub = AvernetSubagent(_mk_spec(), _mk_run(), gw, _AGENT_ID)
            await sub.setup()
            return gw, sub

        gw, sub = asyncio.run(main())
        assert sub._bot_id == "bot-42"
        assert sub.executor_id == "bot-42"
