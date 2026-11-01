"""Integration tests for AuthService with StubAuthPlugin.

Verifies container wiring, authentication flows, and authorization checks.
Uses the it-zdas overlay which wires the stub plugin via the DI container.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from secbaas.community.spi.auth import AuthUser


@pytest.mark.integration
class TestAuthServiceIntegration:
    """Integration tests for AuthService."""

    def test_auth_service_resolves_from_container(
        self,
        bootstrap_init,
    ):
        """Verify AuthService is properly registered and accessible from the container."""
        service = bootstrap_init.services.auth_service()
        assert service is not None, (
            "AuthService should resolve from bootstrap_init.services.auth_service()"
        )

    def test_authenticate_request_returns_stub_user(
        self,
        bootstrap_init,
    ):
        """Authenticate with a cookie and verify the stub user is returned."""

        async def _run():
            service = bootstrap_init.services.auth_service()
            user = await service.authenticate_request(cookie="any_cookie_value")
            return user

        user = asyncio.run(_run())
        assert isinstance(user, AuthUser), (
            f"authenticate_request should return AuthUser, got {type(user)}"
        )
        assert user.staffId == "000001", (
            f"Expected staffId='000001', got {user.staffId!r}"
        )
        assert user.operatorName == "stub_operator", (
            f"Expected operatorName='stub_operator', got {user.operatorName!r}"
        )

    def test_authenticate_request_without_cookie(
        self,
        bootstrap_init,
    ):
        """Ensure authenticate_request works with an empty cookie (stub is lenient)."""

        async def _run():
            service = bootstrap_init.services.auth_service()
            user = await service.authenticate_request(cookie="")
            return user

        user = asyncio.run(_run())
        assert isinstance(user, AuthUser), (
            "authenticate_request with empty cookie should still return AuthUser"
        )
        # StubAuthPlugin always returns the same hardcoded user regardless of input
        assert user.staffId == "000001"

    def test_is_operator_returns_true(
        self,
        bootstrap_init,
    ):
        """Build an AuthUser and verify is_operator returns True (stub always-allows)."""
        service = bootstrap_init.services.auth_service()
        user = AuthUser(
            id="test-1",
            staffId="000001",
            operatorName="test_op",
        )
        assert service.is_operator(user) is True, (
            "is_operator should return True for the stub plugin"
        )

    def test_check_user_permission(
        self,
        bootstrap_init,
    ):
        """Verify check_user_permission returns True (stub always-allows)."""
        service = bootstrap_init.services.auth_service()
        user = AuthUser(
            id="test-1",
            staffId="000001",
            operatorName="test_op",
        )
        result = service.check_user_permission(
            user=user,
            permission_codes="some_permission,another_permission",
        )
        assert result is True, (
            "check_user_permission should return True for the stub plugin"
        )

    def test_build_operation_context(
        self,
        bootstrap_init,
    ):
        """Build an OperationContext from cookie/referer and verify fields."""

        async def _run():
            service = bootstrap_init.services.auth_service()
            cookie = "test_cookie=value123"
            referer = "http://localhost:8888/"

            ctx = await service.build_operation_context(cookie=cookie, referer=referer)
            return ctx

        ctx = asyncio.run(_run())
        assert ctx is not None, (
            "build_operation_context should return a non-None OperationContext"
        )
        assert ctx.operator == "000001", (
            f"Expected operator='000001' (from stub staffId), got {ctx.operator!r}"
        )
        assert ctx.env is not None, (
            "Expected env to be set (from env_utils.get_current_env())"
        )
