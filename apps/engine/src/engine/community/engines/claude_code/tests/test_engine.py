"""Unit tests for ClaudeCodeCommunityEngine assembly root.

Mirrors ``engines/openclaw/tests/test_engine.py`` — covers the ACL-assembled
community engine: adapter wiring, capability matrix, service-slot exposure,
and the ``initialize()`` / ``shutdown()`` lifecycle (injected-client early-return
seam + production-path connect/disconnect via a faked relay client).

Tests do NOT exercise the vendored Node relay. The test seam is the
``client=`` injection parameter (documented in ``engine.py``'s docstring):
when set, ``initialize()`` / ``shutdown()`` return early without touching the
relay, and the assembled adapters share the injected fake client.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from engine.community.core.bash.base import BaseBashService
from engine.community.core.engine.capability import Capability
from engine.community.core.engine.protocol import Engine
from engine.community.engines.claude_code.engine import (
    CLAUDE_CODE_COMMUNITY_CAPABILITIES,
    ClaudeCodeCommunityEngine,
)
from engine.community.core.adapters.claude_code.chat import ClaudeCodeChatAdapter
from engine.community.core.adapters.claude_code.cron import ClaudeCodeCronAdapter
from engine.community.core.adapters.claude_code.file import ClaudeCodeFileAdapter
from engine.community.core.adapters.claude_code.mcp import ClaudeCodeMcpAdapter
from engine.community.core.adapters.claude_code.models import ClaudeCodeModelsAdapter
from engine.community.core.adapters.claude_code.relay import ClaudeCodeRelayAdapter
from engine.community.core.adapters.claude_code.session import ClaudeCodeSessionAdapter
from engine.community.core.adapters.claude_code.skills import ClaudeCodeSkillsAdapter


def _fake_client() -> MagicMock:
    """A fake ClaudeCodeRelayClient. ``connected`` is read by the port base and
    by ``shutdown()``, so make it a plain attribute (MagicMock auto-attributes
    are MagicMock instances, not bools)."""
    client = MagicMock()
    client.connected = False
    return client


class TestClaudeCodeCommunityEngineMetadata:
    def test_name_is_claude_code(self):
        assert ClaudeCodeCommunityEngine.name == "claude_code"

    def test_version_is_string(self):
        assert isinstance(ClaudeCodeCommunityEngine.version, str)
        assert ClaudeCodeCommunityEngine.version

    def test_capabilities_declared_on_instance(self):
        engine = ClaudeCodeCommunityEngine(client=_fake_client())
        caps = engine.capabilities
        assert caps is CLAUDE_CODE_COMMUNITY_CAPABILITIES
        # Chat
        assert caps.supports(Capability.CHAT_STREAM)
        assert caps.supports(Capability.CHAT_HISTORY)
        assert caps.supports(Capability.CHAT_ABORT)
        assert caps.supports(Capability.CHAT_APPROVAL)
        assert caps.supports(Capability.CHAT_INTERACTION)
        assert caps.supports(Capability.CHAT_MODE_TRANSITION)
        # Session
        assert caps.supports(Capability.SESSION_LIST)
        assert caps.supports(Capability.SESSION_DELETE)
        assert caps.supports(Capability.SESSION_UPDATE)
        assert caps.supports(Capability.SESSION_HISTORY)
        # MCP
        assert caps.supports(Capability.MCP_LIST)
        assert caps.supports(Capability.MCP_TOOLS_LIST)
        assert caps.supports(Capability.MCP_FILTER_SERVERS)
        # Skills
        assert caps.supports(Capability.SKILLS_LIST)
        assert caps.supports(Capability.SKILLS_INSTALL)
        assert caps.supports(Capability.SKILLS_SYNC_SYMLINKS)
        # Cron
        assert caps.supports(Capability.CRON_LIST)
        assert caps.supports(Capability.CRON_RUN)
        # Model
        assert caps.supports(Capability.MODEL_LIST)
        assert caps.supports(Capability.MODEL_SWITCH)
        # File
        assert caps.supports(Capability.FILE_READ)
        assert caps.supports(Capability.FILE_WRITE)
        # Bash
        assert caps.supports(Capability.BASH_EXEC)

    def test_limited_capabilities_correct(self):
        caps = ClaudeCodeCommunityEngine(client=_fake_client()).capabilities
        # SESSION_CREATE is limited (relay has no explicit create)
        assert Capability.SESSION_CREATE in caps.limited
        # SKILLS_EXECUTE is limited (triggered via chat, not direct exec)
        assert Capability.SKILLS_EXECUTE in caps.limited
        # MCP_TOOLS_CALL is limited (SDK bridge only)
        assert Capability.MCP_TOOLS_CALL in caps.limited
        # A supported capability must not also be limited
        assert Capability.CHAT_STREAM not in caps.limited


class TestClaudeCodeCommunityEngineWiring:
    def test_constructs_all_adapters(self):
        engine = ClaudeCodeCommunityEngine(client=_fake_client())
        assert isinstance(engine.session, ClaudeCodeSessionAdapter)
        assert isinstance(engine.chat, ClaudeCodeChatAdapter)
        assert isinstance(engine.mcp, ClaudeCodeMcpAdapter)
        assert isinstance(engine.skills, ClaudeCodeSkillsAdapter)
        assert isinstance(engine.cron, ClaudeCodeCronAdapter)
        assert isinstance(engine.models, ClaudeCodeModelsAdapter)
        assert isinstance(engine.file, ClaudeCodeFileAdapter)
        assert isinstance(engine.relay, ClaudeCodeRelayAdapter)
        # bash reuses the core default (not a claude_code adapter)
        assert isinstance(engine.bash, BaseBashService)

    def test_adapters_share_one_plugin_impl(self):
        """All adapters delegate to a single ClaudeCodePluginImpl instance."""
        engine = ClaudeCodeCommunityEngine(client=_fake_client())
        assert engine._port is not None
        assert engine.session._port is engine._port
        assert engine.chat._port is engine._port
        assert engine.mcp._port is engine._port
        assert engine.skills._port is engine._port
        assert engine.cron._port is engine._port
        assert engine.models._port is engine._port
        assert engine.file._port is engine._port
        assert engine.relay._port is engine._port

    def test_injected_client_is_passed_to_port(self):
        """The injected client reaches the port base (the test seam)."""
        client = _fake_client()
        engine = ClaudeCodeCommunityEngine(client=client)
        assert engine._port._client is client

    def test_default_construction_builds_a_port(self):
        """Production path: no injected client → port lazily connects later."""
        engine = ClaudeCodeCommunityEngine()
        assert engine._port is not None
        assert engine._port._client is None  # not yet connected

    def test_chat_extras_is_chat_adapter(self):
        """chat_extras exposes the chat adapter (HITL resolve surface)."""
        engine = ClaudeCodeCommunityEngine(client=_fake_client())
        assert engine.chat_extras is engine.chat
        assert isinstance(engine.chat_extras, ClaudeCodeChatAdapter)

    def test_all_service_slots_are_non_none(self):
        engine = ClaudeCodeCommunityEngine(client=_fake_client())
        for slot in (
            engine.session,
            engine.chat,
            engine.mcp,
            engine.skills,
            engine.cron,
            engine.models,
            engine.file,
            engine.bash,
            engine.relay,
        ):
            assert slot is not None


class TestClaudeCodeCommunityEngineProtocolConformance:
    def test_is_instance_of_engine_protocol(self):
        """ClaudeCodeCommunityEngine satisfies the Engine Protocol structurally
        (Engine is @runtime_checkable)."""
        engine = ClaudeCodeCommunityEngine(client=_fake_client())
        assert isinstance(engine, Engine)

    def test_validate_capabilities_passes(self):
        """BaseEngine.validate_capabilities() detects declared-vs-assigned
        mismatches; it must not raise for the community engine."""
        engine = ClaudeCodeCommunityEngine(client=_fake_client())
        # Should not raise.
        engine.validate_capabilities()


class TestClaudeCodeCommunityEngineLifecycleInjectedClient:
    """Injected-client path: initialize()/shutdown() are no-ops (tests own the
    client lifecycle). These are the lines covered by the documented test seam."""

    @pytest.mark.asyncio
    async def test_initialize_with_injected_client_is_noop(self):
        engine = ClaudeCodeCommunityEngine(client=_fake_client())
        # Must not raise and must not touch the relay.
        await engine.initialize()

    @pytest.mark.asyncio
    async def test_shutdown_with_injected_client_is_noop(self):
        engine = ClaudeCodeCommunityEngine(client=_fake_client())
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_initialize_does_not_connect_injected_client(self):
        """The injected client's connect/disconnect must NOT be called when the
        engine owns it (test seam contract)."""
        client = _fake_client()
        client.connect = AsyncMock()
        engine = ClaudeCodeCommunityEngine(client=client)
        await engine.initialize()
        client.connect.assert_not_called()

    @pytest.mark.asyncio
    async def test_shutdown_does_not_disconnect_injected_client(self):
        client = _fake_client()
        client.disconnect = AsyncMock()
        engine = ClaudeCodeCommunityEngine(client=client)
        await engine.shutdown()
        client.disconnect.assert_not_called()


class TestClaudeCodeCommunityEngineLifecycleProductionPath:
    """Production path (no injected client): initialize() lazily connects the
    shared relay client; shutdown() disconnects it. We monkeypatch the port's
    ``_relay()`` / ``_client`` to avoid opening a real WebSocket."""

    @pytest.mark.asyncio
    async def test_initialize_connects_relay(self, monkeypatch):
        engine = ClaudeCodeCommunityEngine()  # production path

        fake_client = MagicMock()
        fake_client.connected = True
        engine._port._relay = AsyncMock(return_value=fake_client)  # type: ignore[method-assign]

        await engine.initialize()
        engine._port._relay.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_initialize_swallows_connect_failure(self, monkeypatch):
        """A relay connect failure in initialize() must not raise — adapters
        connect lazily on first use, so the error is only delayed."""
        engine = ClaudeCodeCommunityEngine()

        engine._port._relay = AsyncMock(side_effect=ConnectionError("no relay"))  # type: ignore[method-assign]

        # Must not raise.
        await engine.initialize()

    @pytest.mark.asyncio
    async def test_shutdown_disconnects_connected_client(self, monkeypatch):
        engine = ClaudeCodeCommunityEngine()

        fake_client = MagicMock()
        fake_client.connected = True
        fake_client.disconnect = AsyncMock()
        engine._port._client = fake_client  # type: ignore[method-assign]

        await engine.shutdown()
        fake_client.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_skips_when_client_none(self):
        """No client was ever connected → shutdown() is a no-op (no exception)."""
        engine = ClaudeCodeCommunityEngine()
        assert engine._port._client is None
        await engine.shutdown()  # must not raise

    @pytest.mark.asyncio
    async def test_shutdown_skips_when_client_not_connected(self):
        engine = ClaudeCodeCommunityEngine()
        fake_client = MagicMock()
        fake_client.connected = False
        fake_client.disconnect = AsyncMock()
        engine._port._client = fake_client  # type: ignore[method-assign]

        await engine.shutdown()
        fake_client.disconnect.assert_not_called()

    @pytest.mark.asyncio
    async def test_shutdown_swallows_disconnect_failure(self):
        """disconnect() raising must not propagate from shutdown()."""
        engine = ClaudeCodeCommunityEngine()
        fake_client = MagicMock()
        fake_client.connected = True
        fake_client.disconnect = AsyncMock(side_effect=RuntimeError("boom"))
        engine._port._client = fake_client  # type: ignore[method-assign]

        await engine.shutdown()  # must not raise


class TestClaudeCodeModelsModule:
    """plugin_api/claude_code/models.py is a placeholder module (currently no
    bespoke native DTOs — wire shapes are plain dicts). Importing it exercises
    the module body (the ``__all__`` assignment), which is the only executable
    line. Pin the contract so a future DTO addition doesn't silently change the
    placeholder semantics without updating this test."""

    def test_module_imports_cleanly(self):
        # The placeholder module exists and is importable as a submodule.
        import importlib

        cc_models = importlib.import_module("engine.community.plugin_api.claude_code.models")
        assert cc_models is not None
        assert cc_models.__name__ == "engine.community.plugin_api.claude_code.models"

    def test_module_all_is_empty_list(self):
        """Placeholder contract: no native DTOs exported yet."""
        from engine.community.plugin_api.claude_code import models as cc_models

        assert cc_models.__all__ == []

    def test_module_has_no_bespoke_dto_types(self):
        """The leaf-rule placeholder forbids bespoke native dataclasses that
        merely re-describe wire dicts. Pin that no such types exist yet by
        enumerating module members that are classes."""
        from engine.community.plugin_api.claude_code import models as cc_models

        import inspect

        classes = [
            name
            for name, obj in inspect.getmembers(cc_models, inspect.isclass)
            # exclude imported dunder helpers / typing primitives
            if not name.startswith("_")
        ]
        assert classes == []