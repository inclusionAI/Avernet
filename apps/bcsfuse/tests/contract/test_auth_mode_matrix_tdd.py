"""The authentication contract must be explicit for runtime and local modes."""

from __future__ import annotations

import pytest

from app.bootstrap.application_context import ApplicationContext
from app.bootstrap.oss_business_support import require_oss_auth
from app.bootstrap.provider_registry import ProviderRegistry
from app.infra.public.auth.simple_token_auth_provider import (
    SimpleTokenAuthProvider,
)


class RequestHeaders:
    def __init__(self, authorization: str = "") -> None:
        self.values = {"Authorization": authorization}

    def get(self, name: str, default: str = "") -> str:
        return self.values.get(name, default)


class FakeRequest:
    def __init__(self, context: ApplicationContext) -> None:
        class State:
            pass

        class App:
            pass

        app = App()
        app.state = State()
        app.state.context = context
        self.app = app
        self.headers = RequestHeaders()


class TrustGatewayConfig:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def get_bool(self, key: str, default: bool) -> bool:
        assert key == "auth.trust_gateway"
        return self.enabled


class RejectingAuthProvider:
    def validate_request(self, request) -> bool:
        return False


def context_for(mode: str, *, trust_gateway: bool) -> ApplicationContext:
    registry = ProviderRegistry()
    registry.register("config", TrustGatewayConfig(trust_gateway))
    registry.register("auth", RejectingAuthProvider())
    return ApplicationContext(
        mode=mode, startup_profile="opensource", registry=registry
    )


def test_runtime_without_gateway_trust_requires_the_auth_provider() -> None:
    request = FakeRequest(context_for("runtime", trust_gateway=False))
    with pytest.raises(Exception) as caught:
        require_oss_auth(request)
    assert getattr(caught.value, "status_code", None) == 401


def test_gateway_trust_bypasses_runtime_shared_bearer_only() -> None:
    request = FakeRequest(context_for("runtime", trust_gateway=True))
    require_oss_auth(request)


@pytest.mark.parametrize("mode", ["dev", "dev_smoke"])
def test_gateway_trust_cannot_add_a_new_bypass_outside_runtime(mode: str) -> None:
    request = FakeRequest(context_for(mode, trust_gateway=True))
    with pytest.raises(Exception) as caught:
        require_oss_auth(request)
    assert getattr(caught.value, "status_code", None) == 401


def test_simple_token_provider_matrix(monkeypatch) -> None:
    monkeypatch.delenv("BCSFUSE_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("BCSFUSE_AUTH_TOKEN", "contract-test")

    monkeypatch.setenv("BCSFUSE_PROVIDER_MODE", "runtime")
    runtime_without_provider = SimpleTokenAuthProvider()
    assert runtime_without_provider.authenticate("") is None
    assert runtime_without_provider.authenticate("contract-test")["username"] == "api_user"

    monkeypatch.setenv("BCSFUSE_PROVIDER_MODE", "dev")
    assert SimpleTokenAuthProvider().authenticate("")["username"] == "dev_user"

    monkeypatch.setenv("BCSFUSE_PROVIDER_MODE", "dev_smoke")
    assert SimpleTokenAuthProvider().authenticate("")["username"] == "dev_user"

    monkeypatch.setenv("BCSFUSE_PROVIDER_MODE", "test")
    assert SimpleTokenAuthProvider().authenticate("") is None
