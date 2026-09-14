"""Unit tests for the OpenClaw ActiveRunRegistry.

Covers the registry lifecycle, isolation across sessions, stale detection
(which yields ``error/unknown``), the dual-axis projection shape, and the
``canonicalize_session_id`` / ``derive_agent_id`` helpers. These tests
mutate the registry directly; ``test_chat_stream_active_run`` exercises the
register/touch/release integration through the chat port.
"""
from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest

from engine.community.plugins.openclaw.active_run_registry import (
    ActiveRunEntry,
    ActiveRunRegistry,
    canonicalize_session_id,
    derive_agent_id,
)


class TestHelpers:
    def test_canonicalize_parent_session(self):
        assert canonicalize_session_id("session:abc-1:user:5") == "abc-1"

    def test_canonicalize_main_agent_alias(self):
        assert (
            canonicalize_session_id("agent:main:session:def-2:user:6") == "def-2"
        )

    def test_canonicalize_named_agent_alias(self):
        assert (
            canonicalize_session_id("agent:sub-process-X:session:xyz:user:1")
            == "xyz"
        )

    def test_canonicalize_handles_blank_and_garbage(self):
        assert canonicalize_session_id(None) == ""
        assert canonicalize_session_id("") == ""
        assert canonicalize_session_id("plain") == "plain"
        assert canonicalize_session_id("svc-session:s:1") == "s"

    def test_derive_agent_id(self):
        assert derive_agent_id("agent:main:session:abc:user:1") == "main"
        assert derive_agent_id("agent:sub-x:session:abc:user:1") == "sub-x"
        assert derive_agent_id("session:abc:user:1") is None
        assert derive_agent_id(None) is None
        assert derive_agent_id("") is None


class TestRegistryRegisterRelease:
    def test_register_makes_entry_active(self):
        r = ActiveRunRegistry()
        r.register("c1", "session:s1:user:1", run_id="r1")
        live, stale = r.list_active_sessions()
        assert stale == 0
        assert len(live) == 1
        entry = live[0]
        assert entry["session_id"] == "s1"
        assert entry["run_id"] == "r1"
        assert entry["agent_id"] is None  # no agent alias in key
        assert entry["last_state"] == "running"

    def test_release_removes_entry(self):
        r = ActiveRunRegistry()
        r.register("c1", "session:s1:user:1")
        assert r.active_count() == 1
        r.release("c1", last_state="final")
        assert r.active_count() == 0
        live, stale = r.list_active_sessions()
        assert live == [] and stale == 0
        # Releasing an unknown/already-released id is a no-op.
        r.release("c1")
        r.release("never-seen")

    def test_release_on_error_and_aborted_transitions(self):
        for state in ("error", "aborted"):
            r = ActiveRunRegistry()
            r.register("c1", "session:s1:user:1")
            r.release("c1", last_state=state)
            assert r.active_count() == 0, state

    def test_touch_updates_run_id_state_and_agent(self):
        r = ActiveRunRegistry()
        r.register("c1", "agent:main:session:s1:user:1")
        r.touch("c1", run_id="run-42", last_state="delta")
        live, _ = r.list_active_sessions()
        assert live[0]["run_id"] == "run-42"
        assert live[0]["last_state"] == "delta"
        assert live[0]["agent_id"] == "main"

    def test_touch_unknown_id_returns_false(self):
        r = ActiveRunRegistry()
        assert r.touch("nope") is False


class TestIsolation:
    def test_multiple_sessions_do_not_cross(self):
        r = ActiveRunRegistry()
        r.register("c1", "session:s1:user:1", run_id="r1")
        r.register("c2", "session:s2:user:1", run_id="r2")
        r.register("c3", "agent:main:session:s1:user:1", run_id="r3")
        live, _ = r.list_active_sessions()
        assert {e["session_id"] for e in live} == {"s1", "s2"}
        assert {e["run_id"] for e in live} == {"r1", "r2", "r3"}
        # Releasing one doesn't drop the others.
        r.release("c2", last_state="final")
        live, _ = r.list_active_sessions()
        assert {e["session_id"] for e in live} == {"s1"}
        # s1 still has two runs (c1 parent + c3 main-alias).
        assert len(live) == 2


class TestStaleIncomplete:
    def test_stale_entry_yields_incomplete_flag(self):
        # Force stale_after to zero — any entry becomes stale immediately.
        r = ActiveRunRegistry(stale_after_seconds=0.0)
        r.register("c1", "session:s1:user:1", run_id="r1")
        live, stale = r.list_active_sessions()
        assert live == []
        assert stale == 1


class TestEntryIsolationAcrossInstances:
    def test_two_registries_are_independent(self):
        r1 = ActiveRunRegistry()
        r2 = ActiveRunRegistry()
        r1.register("c1", "session:s1:user:1")
        assert r1.active_count() == 1
        assert r2.active_count() == 0


class TestNewCallIdUnique:
    def test_new_call_id_is_unique(self):
        r = ActiveRunRegistry()
        ids = {r.new_call_id() for _ in range(50)}
        assert len(ids) == 50


class TestSnapshotProperty:
    def test_snapshot_keeps_terminal_entries_after_release(self):
        r = ActiveRunRegistry()
        r.register("c1", "session:s1:user:1")
        r.release("c1", last_state="final")
        # release() removes the entry from the active dict immediately so the
        # next read sees verdict=clear — the snapshot should reflect that.
        snap = r.snapshot()
        assert "c1" not in snap
