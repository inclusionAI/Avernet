"""Task dispatch configuration comes only from the merged deployment YAML."""

from __future__ import annotations

from agentclaw.community.di.modules import config_module
from agentclaw.community.di.modules.config_module import ConfigModule


def test_task_dispatch_loads_backend_url_from_config(monkeypatch) -> None:
    monkeypatch.setattr(
        config_module,
        "_user_config",
        lambda: {"task_dispatch": {"backend_url": "https://backend.example"}},
    )

    assert ConfigModule().task_dispatch().backend_url == "https://backend.example"


def test_task_dispatch_does_not_select_backend_by_runtime_environment(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        config_module,
        "_user_config",
        lambda: {"task_dispatch": {"backend_url": "https://backend.example"}},
    )
    monkeypatch.setenv("SERVER_ENV", "another-environment")
    monkeypatch.setenv("BACKEND_URL", "https://ignored.example")

    assert ConfigModule().task_dispatch().backend_url == "https://backend.example"


def test_task_dispatch_reuses_standard_callback_origin_when_override_is_empty(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        config_module,
        "_user_config",
        lambda: {
            "task_dispatch": {"backend_url": ""},
            "bcs_client": {
                "task_callback_url": "https://backend.example/api/v1/callback"
            },
        },
    )

    assert ConfigModule().task_dispatch().backend_url == "https://backend.example"
