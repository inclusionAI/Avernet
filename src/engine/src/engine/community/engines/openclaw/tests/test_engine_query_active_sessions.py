"""Tests for OpenClawEngine.query_active_sessions().

Exercises the dual-axis response projection on the real `OpenClawEngine`
against a freshly-injected `ActiveRunRegistry`, covering:

- clean empty registry → ok / clear
- mid-flight runs → ok / active (with sessions[])
- stale / incomplete entries → error / unknown
- timeout → timeout / unknown
- runtime exception during enumeration → error / unknown
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from engine.community.engines.openclaw.engine import OpenClawEngine
from engine.community.plugins.openclaw.active_run_registry import ActiveRunRegistry


def _fake_client():
    """A MagicMock-free fake — only `connected` is read by the impl during the
    port construction. We don't drive chat_stream here; the registry is poked
    directly."""
    class _C:
        connected = False

    return _C()


def _engine_with_registry(registry: ActiveRunRegistry) -> OpenClawEngine:
    return OpenClawEngine(
        client=_fake_client(),
        active_run_registry=registry,
    )


class TestClear:
    async def test_empty_registry_is_clear(self):
        engine = _engine_with_registry(ActiveRunRegistry())
        result = await engine.query_active_sessions()
        assert result["query_status"] == "ok"
        assert result["verdict"] == "clear"
        assert result["engine"] == "openclaw"
        assert result["active_session_count"] == 0
        assert result["sessions"] == []
        assert "checked_at" in result


class TestActive:
    async def test_register_one_run_yields_active(self):
        reg = ActiveRunRegistry()
        engine = _engine_with_registry(reg)
        reg.register("c1", "session:abc:user:1", run_id="run-1")
        result = await engine.query_active_sessions()
        assert result["query_status"] == "ok"
        assert result["verdict"] == "active"
        assert result["active_session_count"] == 1
        entry = result["sessions"][0]
        assert entry["session_id"] == "abc"
        assert entry["run_id"] == "run-1"
        assert entry["agent_id"] is None
        assert entry["last_state"] == "running"

    async def test_register_with_agent_alias_propagates_agent_id(self):
        reg = ActiveRunRegistry()
        engine = _engine_with_registry(reg)
        reg.register("c2", "agent:main:session:abc:user:1", run_id="run-1")
        result = await engine.query_active_sessions()
        assert result["verdict"] == "active"
        entry = result["sessions"][0]
        assert entry["session_id"] == "abc"
        assert entry["agent_id"] == "main"


class TestIncomplete:
    async def test_stale_entry_yields_error_unknown(self):
        reg = ActiveRunRegistry(stale_after_seconds=0.0)
        engine = _engine_with_registry(reg)
        reg.register("c1", "session:abc:user:1", run_id="run-1")
        result = await engine.query_active_sessions()
        assert result["query_status"] == "error"
        assert result["verdict"] == "unknown"
        assert result.get("incomplete") is True


class TestTimeout:
    async def test_query_timeout_yields_timeout_unknown(self):
        reg = ActiveRunRegistry()
        engine = _engine_with_registry(reg)
        reg.register("c1", "session:abc:user:1", run_id="run-1")

        async def _hang(*a, **k):
            await asyncio.sleep(5)  # never returns within the timeout
            return [], 0

        with patch.object(reg, "list_active_sessions_async", _hang):
            result = await engine.query_active_sessions(timeout_ms=20)
        assert result["query_status"] == "timeout"
        assert result["verdict"] == "unknown"
        assert "timeout" in (result.get("error_message") or "").lower()


class TestException:
    async def test_exception_during_enumeration_yields_error_unknown(self):
        reg = ActiveRunRegistry()
        engine = _engine_with_registry(reg)
        reg.register("c1", "session:abc:user:1", run_id="run-1")

        async def _boom(*a, **k):
            raise RuntimeError("boom")

        with patch.object(reg, "list_active_sessions_async", _boom):
            result = await engine.query_active_sessions(timeout_ms=1000)
        assert result["query_status"] == "error"
        assert result["verdict"] == "unknown"
        assert "boom" in (result.get("error_message") or "")


class TestTimeoutNone:
    async def test_timeout_none_disables_cutoff(self):
        reg = ActiveRunRegistry()
        engine = _engine_with_registry(reg)
        # Should fall back to default and complete immediately.
        result = await engine.query_active_sessions(timeout_ms=None)
        assert result["query_status"] == "ok"
        assert result["verdict"] == "clear"


class TestNegativeTimeout:
    async def test_negative_timeout_falls_back_to_default(self):
        reg = ActiveRunRegistry()
        engine = _engine_with_registry(reg)
        result = await engine.query_active_sessions(timeout_ms=-5)
        assert result["query_status"] == "ok"
        assert result["verdict"] == "clear"
