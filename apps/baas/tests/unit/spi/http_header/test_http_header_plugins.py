"""Tests for HttpHeaderPlugin protocol compliance and implementations."""

from __future__ import annotations

from secbaas.community.http_header import (
    get_http_header_plugin,
    set_http_header_plugin,
)
from secbaas.community.plugins.http_header.default import NoOpHttpHeaderPlugin


class _StubHeaderPlugin:
    """Stub implementation: records calls and injects a fixed header."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def inject_header(self, headers: dict) -> None:
        self.calls.append(dict(headers))
        headers["X-Stub"] = "stub"


class TestHttpHeaderProtocolCompliance:
    """Structural conformance tests."""

    def test_noop_plugin_has_required_method(self) -> None:
        plugin = NoOpHttpHeaderPlugin()
        assert hasattr(plugin, "inject_header")

    def test_noop_plugin_injects_nothing(self) -> None:
        headers = {"Accept": "application/json"}
        NoOpHttpHeaderPlugin().inject_header(headers)
        assert headers == {"Accept": "application/json"}

    def test_noop_plugin_leaves_empty_headers_empty(self) -> None:
        headers: dict = {}
        NoOpHttpHeaderPlugin().inject_header(headers)
        assert headers == {}


class TestHttpHeaderAccessor:
    """Tests for the module-level plugin accessor."""

    def test_default_plugin_is_noop(self) -> None:
        plugin = get_http_header_plugin()
        headers: dict = {}
        plugin.inject_header(headers)
        assert headers == {}

    def test_set_plugin_overrides_get(self) -> None:
        original = get_http_header_plugin()
        stub = _StubHeaderPlugin()
        try:
            set_http_header_plugin(stub)
            assert get_http_header_plugin() is stub

            headers: dict = {}
            get_http_header_plugin().inject_header(headers)
            assert headers == {"X-Stub": "stub"}
            assert stub.calls == [{}]
        finally:
            set_http_header_plugin(original)
