# mypy: disable-error-code="call-arg"
"""Tests for HttpConnectionInfo dataclass."""

from __future__ import annotations

import pytest

from secbaas.api.bot_runtime._http_connection_info import HttpConnectionInfo


class TestHttpConnectionInfo:
    """Test cases for HttpConnectionInfo dataclass."""

    def test_create_http_connection_info(self) -> None:
        """Should create HttpConnectionInfo with all fields accessible."""
        info = HttpConnectionInfo(
            http_url="http://antclaw-a1b2c3.inc.alipay.net:9999",
            token="abc123",
        )

        assert info.http_url == "http://antclaw-a1b2c3.inc.alipay.net:9999"
        assert info.token == "abc123"

    def test_fields_are_positional_no_defaults(self) -> None:
        """HttpConnectionInfo fields should be positional-only (no default values)."""
        # Verify all fields can be set via positional args
        info = HttpConnectionInfo("http://example.com:8080", "token-456")
        assert info.http_url == "http://example.com:8080"
        assert info.token == "token-456"

    def test_create_with_realistic_values(self) -> None:
        """Should create HttpConnectionInfo with realistic URL and token values."""
        info = HttpConnectionInfo(
            http_url="http://antclaw.xyz:9999/api/v1/health",
            token="no-expiry-token",
        )
        assert info.http_url == "http://antclaw.xyz:9999/api/v1/health"
        assert info.token == "no-expiry-token"

    def test_all_fields_accessible_as_attributes(self) -> None:
        """All two fields should be accessible as attributes."""
        info = HttpConnectionInfo(
            http_url="http://poolab-dev:8080",
            token="secret-token",
        )
        assert isinstance(info.http_url, str)
        assert isinstance(info.token, str)
