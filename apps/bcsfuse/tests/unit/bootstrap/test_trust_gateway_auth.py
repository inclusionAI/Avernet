from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.bootstrap.application_context import ApplicationContext
from app.bootstrap.oss_business_routes import _trust_gateway_enabled, require_oss_auth
from app.bootstrap.provider_registry import ProviderRegistry


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


class Config:
    def __init__(self, trust_gateway: bool) -> None:
        self.trust_gateway = trust_gateway

    def get_bool(self, key: str, default: bool = False) -> bool:
        assert key == "auth.trust_gateway"
        return self.trust_gateway


def build_request(
    auth_provider: ContractAuthProvider,
    config: Config | None = None,
    mode: str = "test",
):
    registry = ProviderRegistry()
    registry.register("config", config or Config(False))
    registry.register("auth", auth_provider)
    context = ApplicationContext(
        mode=mode,
        startup_profile="test",
        registry=registry,
    )
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(context=context)),
    )


def test_gateway_trust_is_disabled_by_composed_config() -> None:
    assert _trust_gateway_enabled(Config(False)) is False


def test_gateway_trust_is_enabled_by_composed_config() -> None:
    assert _trust_gateway_enabled(Config(True)) is True


def test_route_gateway_trust_cannot_be_enabled_by_raw_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BCSFUSE_TRUST_GATEWAY", "true")
    request = build_request(ContractAuthProvider(None), Config(False))

    with pytest.raises(HTTPException) as exc_info:
        require_oss_auth(request)

    assert exc_info.value.status_code == 401


def test_auth_guard_accepts_provider_implementing_published_contract() -> None:
    request = build_request(ContractAuthProvider({"user_id": "contract-user"}))

    require_oss_auth(request)


def test_auth_guard_rejects_missing_current_user() -> None:
    request = build_request(ContractAuthProvider(None))

    with pytest.raises(HTTPException) as exc_info:
        require_oss_auth(request)

    assert exc_info.value.status_code == 401


def test_gateway_env_override_is_loaded_by_config_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from app.infra.public.config.yaml_env_config_provider import (
        YamlEnvConfigProvider,
    )

    yaml_path = tmp_path / "application-test.yaml"
    yaml_path.write_text("auth:\n  trust_gateway: false\n", encoding="utf-8")
    monkeypatch.setenv("BCSFUSE_TRUST_GATEWAY", "true")

    config = YamlEnvConfigProvider(config_path=str(yaml_path))

    assert config.get_bool("auth.trust_gateway", False) is True


def test_composed_gateway_trust_value_skips_local_bearer_validation() -> None:
    request = build_request(ContractAuthProvider(None), Config(True), mode="runtime")

    require_oss_auth(request)
