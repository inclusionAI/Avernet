"""Unit tests for OpenClaw's process-local ActiveRunRegistry — the dual-axis
Active Session query model and lifecycle/隔离 semantics.

Covers the behaviour-rule matrix:
  - empty success → ok/clear
  - active success → ok/active
  - query timeout → timeout/unknown
  - query exception → error/unknown
  - incomplete result → ok/unknown
  - terminal run no longer active (completed/failed/aborted)
  - multi-session / multi-run isolation and session_id/agent_id filtering
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from engine.community.core.adapters.openclaw.active_run_registry import (
    ActiveRunRegistry,
)


def _reg() -> ActiveRunRegistry:
    return ActiveRunRegistry()


class TestRunIdentity:
    def test_rebind_provisional_run_id(self):
        reg = _reg()
        reg.register_run("idempotency-key", "session:a:user:u")

        assert reg.rebind_run("idempotency-key", "gateway-run") is True
        assert [run.run_id for run in reg.all_runs()] == ["gateway-run"]
        assert reg.snapshot()[0].run_id == "gateway-run"


class TestQueryVerdict:
    async def test_empty_returns_ok_clear(self):
        reg = _reg()
        r = await reg.query(engine="openclaw")
        assert r.query_status == "ok"
        assert r.verdict == "clear"
        assert r.count == 0
        assert r.sessions == []
        assert r.engine == "openclaw"

    async def test_active_run_returns_ok_active(self):
        reg = _reg()
        reg.register_run("run-1", "session:a:user:u")
        r = await reg.query(engine="openclaw")
        assert r.query_status == "ok"
        assert r.verdict == "active"
        assert r.count == 1
        entry = r.sessions[0]
        assert entry.session_id == "session:a:user:u"
        assert entry.run_id == "run-1"
        assert entry.state == "running"
        assert isinstance(entry.started_at, datetime)
        assert isinstance(entry.updated_at, datetime)
        assert entry.agent_id is None

    async def test_timeout_returns_unknown(self):
        reg = _reg()
        reg.register_run("run-1", "session:a:user:u")

        async def _hang(*a, **k):  # noqa: ANN002
            await asyncio.sleep(30)
            return []

        reg.snapshot = _hang  # type: ignore[assignment]
        r = await reg.query(engine="openclaw", timeout=0.01)
        assert r.query_status == "timeout"
        assert r.verdict == "unknown"
        assert r.count == 0

    async def test_error_returns_unknown(self):
        reg = _reg()

        def _boom(*a, **k):  # noqa: ANN002
            raise RuntimeError("boom")

        reg.snapshot = _boom  # type: ignore[assignment]
        r = await reg.query(engine="openclaw")
        assert r.query_status == "error"
        assert r.verdict == "unknown"

    async def test_incomplete_returns_unknown(self):
        reg = _reg()
        # Directly construct an incomplete record (missing session_key) to
        # exercise the incomplete-guard path the spec calls out.
        from engine.community.core.adapters.openclaw.active_run_registry import (
            ActiveRun,
            RUNNING_STATE,
        )

        now = datetime.now(UTC)
        reg._runs["run-bad"] = ActiveRun(
            run_id="run-bad",
            session_key="",  # incomplete: cannot associate to a session
            agent_id=None,
            state=RUNNING_STATE,
            started_at=now,
            updated_at=now,
        )
        r = await reg.query(engine="openclaw")
        assert r.query_status == "ok"
        assert r.verdict == "unknown"
        assert r.reason == "incomplete"
        # Incomplete runs are NOT surfaced in the response body.
        assert r.sessions == []
        assert r.count == 0


class TestLifecycle:
    @pytest.mark.parametrize(
        "terminal",
        ["completed", "failed", "aborted"],
    )
    async def test_terminal_run_no_longer_active(self, terminal: str):
        reg = _reg()
        reg.register_run("run-1", "session:a:user:u")
        assert (await reg.query()).verdict == "active"
        reg.mark_terminal("run-1", terminal)  # type: ignore[arg-type]
        assert (await reg.query()).verdict == "clear"
        # The record is retained (not active) and reflects the terminal state.
        assert reg.all_runs()[0].state == terminal

    def test_mark_terminal_is_idempotent_first_state_wins(self):
        reg = _reg()
        reg.register_run("run-1", "session:a:user:u")
        reg.mark_terminal("run-1", "failed")
        reg.mark_terminal("run-1", "completed")  # must not overwrite a real terminal
        assert reg.all_runs()[0].state == "failed"

    def test_register_does_not_revive_terminal_run(self):
        reg = _reg()
        reg.register_run("run-1", "session:a:user:u")
        reg.mark_terminal("run-1", "completed")
        reg.register_run("run-1", "session:a:user:u")
        assert reg.all_runs()[0].state == "completed"
        assert reg.snapshot() == []

    def test_register_ignores_empty_identifiers(self):
        reg = _reg()
        reg.register_run("", "session:a:user:u")
        reg.register_run("run-x", "")
        assert reg.all_runs() == []

    def test_mark_terminal_ignores_unknown_run(self):
        reg = _reg()
        reg.mark_terminal("never-registered", "completed")
        assert reg.all_runs() == []


class TestIsolation:
    def test_multiple_sessions_dont_cross(self):
        reg = _reg()
        reg.register_run("run-1", "session:a:user:u")
        reg.register_run("run-2", "session:b:user:u")
        all_active = reg.snapshot()
        assert {r.run_id for r in all_active} == {"run-1", "run-2"}

    def test_filter_by_session_id(self):
        reg = _reg()
        reg.register_run("run-1", "session:a:user:u")
        reg.register_run("run-2", "session:b:user:u")
        only_a = reg.snapshot(session_id="session:a:user:u")
        assert [r.run_id for r in only_a] == ["run-1"]

    def test_filter_by_agent_id_exact_match(self):
        reg = _reg()
        reg.register_run("run-1", "session:a:user:u", agent_id="agent-7")
        reg.register_run("run-2", "session:b:user:u", agent_id="agent-9")
        only_7 = reg.snapshot(agent_id="agent-7")
        assert [r.run_id for r in only_7] == ["run-1"]
        # Runs without an agent never match an agent filter.
        reg.register_run("run-3", "session:c:user:u")
        assert {r.run_id for r in reg.snapshot(agent_id="agent-7")} == {"run-1"}

    async def test_query_with_session_filter_active_then_clear(self):
        reg = _reg()
        reg.register_run("run-1", "session:a:user:u")
        reg.register_run("run-2", "session:b:user:u")
        r = await reg.query(session_id="session:a:user:u")
        assert r.verdict == "active"
        assert r.count == 1
        reg.mark_terminal("run-1", "completed")
        r2 = await reg.query(session_id="session:a:user:u")
        assert r2.verdict == "clear"
        # Other session still active when queried unfiltered.
        assert (await reg.query()).verdict == "active"


class TestRetention:
    def test_terminal_retention_is_bounded(self):
        reg = ActiveRunRegistry(max_retained_terminal=2)
        for i in range(5):
            reg.register_run(f"run-{i}", "session:a:user:u")
            reg.mark_terminal(f"run-{i}", "completed")
        # Only the 2 most-recently terminal runs are retained.
        assert {r.run_id for r in reg.all_runs()} == {"run-3", "run-4"}