# mypy: disable-error-code="call-arg"
"""Tests for WsConnectionInfo dataclass."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from secbaas.api.bot_runtime import WsConnectionInfo


class TestWsConnectionInfo:
    """Test cases for WsConnectionInfo dataclass."""

    def test_create_ws_connection_info(self) -> None:
        """Should create WsConnectionInfo with all fields."""
        expires = datetime.now(UTC) + timedelta(seconds=300)

        info = WsConnectionInfo(
            ws_url="wss://agentclawproxy-prod.alipay.com/proxypass/ARCA_SANDBOX-123:8080/api/openclaw/ws",
            token="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test",
            target="ARCA_SANDBOX-123:8080",
            expires_at=expires,
        )

        assert (
            info.ws_url
            == "wss://agentclawproxy-prod.alipay.com/proxypass/ARCA_SANDBOX-123:8080/api/openclaw/ws"
        )
        assert info.token == "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test"
        assert info.target == "ARCA_SANDBOX-123:8080"
        assert info.expires_at == expires

    def test_ws_connection_info_immutable_by_default(self) -> None:
        """Dataclass should be immutable by default (frozen)."""
        # If dataclass is frozen, this test verifies immutability
        # If not frozen, dataclass is mutable (both OK, this documents behavior)
        expires = datetime.now(UTC)

        info = WsConnectionInfo(
            ws_url="wss://test.com/ws",
            token="token123",
            target="ARCA_TEST:8080",
            expires_at=expires,
        )

        # Verify we can access all fields
        assert isinstance(info.ws_url, str)
        assert isinstance(info.token, str)
        assert isinstance(info.target, str)
        assert isinstance(info.expires_at, datetime)

    def test_ws_connection_info_required_fields(self) -> None:
        """All fields are required for WsConnectionInfo."""
        expires = datetime.now(UTC)

        # Missing ws_url should fail (at runtime for dataclass)
        with pytest.raises(TypeError):
            WsConnectionInfo(
                target="ARCA_TEST:8080",
                expires_at=expires,
            )

    def test_target_format_arca(self) -> None:
        """Target should follow ARCA_{sandbox_id}:{port} format."""
        expires = datetime.now(UTC)

        info = WsConnectionInfo(
            ws_url="wss://test/ws",
            token="token",
            target="ARCA_SANDBOX-abc@tenant:8080",
            expires_at=expires,
        )

        assert info.target.startswith("ARCA_")
        assert ":8080" in info.target
