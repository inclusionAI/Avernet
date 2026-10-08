from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.bootstrap.application_context import ApplicationContext
from src.bootstrap.oss_business_routes import _trust_gateway_enabled, require_oss_auth
from src.bootstrap.provider_registry import ProviderRegistry


class ContractAuthProvider:
    """Auth provider implementing exactly the published protocol."""

    def __init__(self, current_user: dict | None) -> None:
        self.current_user = current_user

    def authenticate(self, token: str) -> dict | None:
        return self.current_user

    def get_current_user(self, request: object) -> dict | None:
        return self.current_user

    def has_permission(self, user: dict, permission: str) -> bool:
        return True

    def validate_request(self, request: object) -> bool:
        return self.current_user is not None


def build_request(auth_provider: ContractAuthProvider):
    registry = ProviderRegistry()
    registry.register("auth", auth_provider)
    context = ApplicationContext(
        mode="test",
        startup_profile="test",
        registry=registry,
    )
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(context=context)),
    )


def test_disabled_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BCSFUSE_TRUST_GATEWAY", raising=False)
    assert _trust_gateway_enabled() is False


def test_enabled_when_env_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BCSFUSE_TRUST_GATEWAY", "true")
    assert _trust_gateway_enabled() is True


def test_disabled_for_other_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BCSFUSE_TRUST_GATEWAY", "0")
    assert _trust_gateway_enabled() is False


def test_auth_guard_accepts_provider_implementing_published_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BCSFUSE_TRUST_GATEWAY", raising=False)
    request = build_request(ContractAuthProvider({"user_id": "contract-user"}))

    require_oss_auth(request)


def test_auth_guard_rejects_missing_current_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BCSFUSE_TRUST_GATEWAY", raising=False)
    request = build_request(ContractAuthProvider(None))

    with pytest.raises(HTTPException) as exc_info:
        require_oss_auth(request)

    assert exc_info.value.status_code == 401
