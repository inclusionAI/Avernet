"""Authentication provider contract consumed by protected routes."""

from src.application.ports import AuthProvider


def test_auth_provider_contract_includes_route_validation() -> None:
    """Protected routes may depend only on declared provider methods."""
    assert callable(getattr(AuthProvider, "validate_request", None))
