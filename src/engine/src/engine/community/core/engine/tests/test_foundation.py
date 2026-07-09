"""
Contract tests for the core/engine foundation.

Covers the pieces added in M0 of the heterogeneous-engine migration:
  - Capability enum values and EngineCapabilities behaviour
  - EngineRegistry register/lookup/error paths
  - BaseEngine accessor behaviour, including validate_capabilities() detecting
    declared-vs-assigned mismatches
  - Engine Protocol structural conformance via @runtime_checkable
"""
from __future__ import annotations

import pytest

from engine.community.core.engine import (
    BaseEngine,
    Capability,
    CapabilityNotSupportedError,
    Engine,
    EngineCapabilities,
    EngineError,
    EngineNotFoundError,
    EngineRegistry,
    HealthStatus,
)

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures — minimal engine implementations used across several tests.
# ─────────────────────────────────────────────────────────────────────────────

class _GoodEngine(BaseEngine):
    """Declares SESSION_LIST + CHAT_STREAM and wires up both plugins."""

    name = "good"
    version = "1.0"

    @property
    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            supported={Capability.SESSION_LIST, Capability.CHAT_STREAM},
        )

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._session = object()
        self._chat = object()


# ─────────────────────────────────────────────────────────────────────────────
# Capability / EngineCapabilities
# ─────────────────────────────────────────────────────────────────────────────

class TestCapability:
    def test_values_are_dotted_domain_strings(self):
        assert Capability.SESSION_LIST.value == "session.list"
        assert Capability.CHAT_STREAM.value == "chat.stream"
        assert Capability.MCP_TOOLS_CALL.value == "mcp.tools.call"

    def test_domain_coverage_spans_all_expected_domains(self):
        domains = {cap.value.split(".", 1)[0] for cap in Capability}
        assert domains >= {
            "session", "chat", "mcp", "skills", "approval",
            "file", "node", "channel", "cron", "model", "health", "effect",
        }


class TestEngineCapabilities:
    def test_supports_covers_supported_and_limited(self):
        caps = EngineCapabilities(
            supported={Capability.CHAT_STREAM},
            limited={Capability.SESSION_LIST: "only current"},
        )
        assert caps.supports(Capability.CHAT_STREAM)
        assert caps.supports(Capability.SESSION_LIST)
        assert not caps.supports(Capability.SESSION_CREATE)

    def test_is_limited_only_true_for_limited(self):
        caps = EngineCapabilities(
            supported={Capability.CHAT_STREAM},
            limited={Capability.SESSION_LIST: "only current"},
        )
        assert not caps.is_limited(Capability.CHAT_STREAM)
        assert caps.is_limited(Capability.SESSION_LIST)

    def test_get_limitation_returns_message(self):
        caps = EngineCapabilities(
            limited={Capability.SESSION_LIST: "only current session"},
        )
        assert caps.get_limitation(Capability.SESSION_LIST) == "only current session"
        assert caps.get_limitation(Capability.CHAT_STREAM) is None

    def test_has_fallback_and_get_fallback(self):
        caps = EngineCapabilities(
            fallback={Capability.MCP_CREATE: "edit mcp.json"},
        )
        assert caps.has_fallback(Capability.MCP_CREATE)
        assert caps.get_fallback(Capability.MCP_CREATE) == "edit mcp.json"
        assert not caps.has_fallback(Capability.MCP_LIST)

    def test_to_dict_round_trip_shape(self):
        caps = EngineCapabilities(
            supported={Capability.CHAT_STREAM, Capability.SESSION_LIST},
            limited={Capability.MCP_START: "use mcporter"},
            fallback={Capability.SKILLS_EXECUTE: "switch engine"},
        )
        d = caps.to_dict()
        assert d["supported"] == ["chat.stream", "session.list"]  # sorted
        assert d["limited"] == {"mcp.start": "use mcporter"}
        assert d["fallback"] == {"skills.execute": "switch engine"}


# ─────────────────────────────────────────────────────────────────────────────
# EngineRegistry
# ─────────────────────────────────────────────────────────────────────────────

class TestEngineRegistry:
    def test_register_and_lookup(self):
        reg = EngineRegistry()
        reg.register(_GoodEngine)

        assert reg.has("good")
        assert reg.get("good") is _GoodEngine
        assert "good" in reg.names()

    def test_get_missing_raises_not_found(self):
        reg = EngineRegistry()
        with pytest.raises(EngineNotFoundError) as exc_info:
            reg.get("does-not-exist")
        assert exc_info.value.engine_name == "does-not-exist"

    def test_reregistering_same_class_is_idempotent(self):
        reg = EngineRegistry()
        reg.register(_GoodEngine)
        reg.register(_GoodEngine)  # must not raise

        assert reg.get("good") is _GoodEngine

    def test_reregistering_different_class_same_name_raises(self):
        class _OtherGood(_GoodEngine):
            pass

        reg = EngineRegistry()
        reg.register(_GoodEngine)
        with pytest.raises(EngineError):
            reg.register(_OtherGood)

    def test_unregister(self):
        reg = EngineRegistry()
        reg.register(_GoodEngine)
        reg.unregister("good")

        assert not reg.has("good")

    def test_unregister_missing_is_noop(self):
        reg = EngineRegistry()
        reg.unregister("not-there")  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# BaseEngine
# ─────────────────────────────────────────────────────────────────────────────

class TestBaseEngineAccessors:
    def test_accessing_unassigned_session_raises_capability_error(self):
        class _Minimal(BaseEngine):
            name = "minimal"
            version = "1.0"

            @property
            def capabilities(self) -> EngineCapabilities:
                return EngineCapabilities()

        with pytest.raises(CapabilityNotSupportedError) as exc_info:
            _ = _Minimal().session
        assert exc_info.value.engine_name == "minimal"
        assert exc_info.value.capability is Capability.SESSION_LIST

    def test_accessing_unassigned_chat_raises_capability_error(self):
        class _Minimal(BaseEngine):
            name = "minimal"
            version = "1.0"

            @property
            def capabilities(self) -> EngineCapabilities:
                return EngineCapabilities()

        with pytest.raises(CapabilityNotSupportedError):
            _ = _Minimal().chat

    def test_optional_plugins_default_to_none(self):
        engine = _GoodEngine()
        assert engine.mcp is None
        assert engine.skills is None
        assert engine.cron is None

    @pytest.mark.asyncio
    async def test_default_lifecycle_methods_are_noops(self):
        engine = _GoodEngine()
        assert await engine.initialize() is None
        assert await engine.shutdown() is None
        status = await engine.health_check()
        assert isinstance(status, HealthStatus)
        assert status.healthy is True


class TestBaseEngineValidateCapabilities:
    """validate_capabilities() catches mismatches between declared caps and
    assigned plugin instances, raising with all problems in one message."""

    def test_passes_when_declarations_match_assignments(self):
        _GoodEngine().validate_capabilities()  # no raise

    def test_fails_when_session_cap_declared_but_session_not_assigned(self):
        class _Liar(BaseEngine):
            name = "liar"
            version = "1.0"

            @property
            def capabilities(self) -> EngineCapabilities:
                return EngineCapabilities(
                    supported={Capability.SESSION_LIST, Capability.CHAT_STREAM},
                )

            def __init__(self) -> None:
                super().__init__()
                self._chat = object()
                # self._session intentionally unassigned

        with pytest.raises(EngineError) as exc_info:
            _Liar().validate_capabilities()
        msg = str(exc_info.value)
        assert "session.list" in msg
        assert "_session" in msg

    def test_fails_when_chat_assigned_but_no_chat_cap_declared(self):
        class _Silent(BaseEngine):
            name = "silent"
            version = "1.0"

            @property
            def capabilities(self) -> EngineCapabilities:
                return EngineCapabilities(supported={Capability.SESSION_LIST})

            def __init__(self) -> None:
                super().__init__()
                self._session = object()
                self._chat = object()  # not declared

        with pytest.raises(EngineError) as exc_info:
            _Silent().validate_capabilities()
        msg = str(exc_info.value)
        assert "_chat" in msg

    def test_collects_multiple_problems_in_single_raise(self):
        class _Messy(BaseEngine):
            name = "messy"
            version = "1.0"

            @property
            def capabilities(self) -> EngineCapabilities:
                return EngineCapabilities(
                    supported={Capability.SESSION_LIST, Capability.CHAT_STREAM},
                )

            def __init__(self) -> None:
                super().__init__()
                # both mandatory plugins unassigned

        with pytest.raises(EngineError) as exc_info:
            _Messy().validate_capabilities()
        msg = str(exc_info.value)
        assert "session.list" in msg
        assert "chat.stream" in msg
        # both problems reported in one shot
        assert msg.count("  - ") == 2


# ─────────────────────────────────────────────────────────────────────────────
# Engine Protocol structural conformance
# ─────────────────────────────────────────────────────────────────────────────

class TestEngineProtocol:
    def test_baseengine_subclass_satisfies_engine_protocol(self):
        assert isinstance(_GoodEngine(), Engine)

    def test_engine_is_runtime_checkable(self):
        # Duck-typed engine — no BaseEngine inheritance — still satisfies
        class _Duck:
            name = "duck"
            version = "0.1"
            capabilities = EngineCapabilities()
            session = object()
            chat = object()
            mcp = None
            skills = None
            cron = None
            models = None
            node = None
            file = None
            default_config = None
            web_shell = None
            bash = None
            relay = None
            approval = None

            async def initialize(self) -> None: ...
            async def shutdown(self) -> None: ...
            async def health_check(self) -> HealthStatus:
                return HealthStatus(healthy=True)
            async def on_connection_open(self, auth=None) -> None: ...
            async def on_connection_close(self, auth=None) -> None: ...

        assert isinstance(_Duck(), Engine)

    def test_missing_mandatory_property_breaks_conformance(self):
        class _MissingSession:
            name = "x"
            version = "0"
            capabilities = EngineCapabilities()
            chat = object()
            mcp = None
            skills = None
            cron = None
            models = None
            relay = None

            async def initialize(self) -> None: ...
            async def shutdown(self) -> None: ...
            async def health_check(self) -> HealthStatus:
                return HealthStatus(healthy=True)

        assert not isinstance(_MissingSession(), Engine)
