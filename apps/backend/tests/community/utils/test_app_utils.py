from agentclaw.community.core.config import provider
from agentclaw.community.core.config.provider import AppConfig
from agentclaw.community.utils.app_utils import (
    DEFAULT_EXECUTOR_APP,
    get_current_executor_app,
)


def test_executor_app_uses_the_registered_config_provider(monkeypatch) -> None:
    class _Provider:
        def load(self) -> AppConfig:
            return AppConfig(
                user_config={}, raw={}, app_name="agentclaw", delegate=None
            )

    monkeypatch.setattr(provider, "_cached", None)
    monkeypatch.setattr(provider, "_provider", _Provider())
    assert get_current_executor_app() == "agentclaw"


def test_executor_app_env_override_only_applies_without_a_provider(monkeypatch) -> None:
    monkeypatch.setattr(provider, "_cached", None)
    monkeypatch.setattr(provider, "_provider", None)
    monkeypatch.setenv("AVERNET_APP_NAME", "agentclaw")
    assert get_current_executor_app() == "agentclaw"

    monkeypatch.delenv("AVERNET_APP_NAME")
    assert get_current_executor_app() == DEFAULT_EXECUTOR_APP


def test_executor_app_rejects_a_configured_name_that_cannot_be_stored(monkeypatch) -> None:
    class _Provider:
        def load(self) -> AppConfig:
            return AppConfig(
                user_config={}, raw={}, app_name=" agentclaw ", delegate=None
            )

    monkeypatch.setattr(provider, "_cached", None)
    monkeypatch.setattr(provider, "_provider", _Provider())
    try:
        get_current_executor_app()
    except ValueError as exc:
        assert "leading or trailing whitespace" in str(exc)
    else:
        raise AssertionError("expected whitespace-padded app_name to fail")


def test_executor_app_rejects_an_empty_configured_name(monkeypatch) -> None:
    class _Provider:
        def load(self) -> AppConfig:
            return AppConfig(user_config={}, raw={}, app_name="", delegate=None)

    monkeypatch.setattr(provider, "_cached", None)
    monkeypatch.setattr(provider, "_provider", _Provider())
    try:
        get_current_executor_app()
    except ValueError as exc:
        assert "must name the deployment" in str(exc)
    else:
        raise AssertionError("expected empty configured app_name to fail")
