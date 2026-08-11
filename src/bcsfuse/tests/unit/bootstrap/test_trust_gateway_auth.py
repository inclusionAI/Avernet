import pytest

from src.bootstrap.oss_business_routes import _trust_gateway_enabled


def test_disabled_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BCSFUSE_TRUST_GATEWAY", raising=False)
    assert _trust_gateway_enabled() is False


def test_enabled_when_env_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BCSFUSE_TRUST_GATEWAY", "true")
    assert _trust_gateway_enabled() is True


def test_disabled_for_other_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BCSFUSE_TRUST_GATEWAY", "0")
    assert _trust_gateway_enabled() is False
