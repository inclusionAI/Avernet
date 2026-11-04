from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module, provider, singleton

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.task.router import router
from agentclaw.community.core.task.task_dispatch.task_settings import (
    HARNESS_POLLER,
    RELAY_EXECUTION,
    SEARCH_SKILL,
    SKILL_REPORT,
    TaskSettingsServiceProtocol,
)


class _FakeTaskSettingsService:
    """Record task-setting reads and return one configured state."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.get_calls: list[tuple[str, str]] = []

    def is_enabled(self, setting_type: str) -> bool:
        return self.enabled

    def get_enabled(self, *, setting_type: str, env: str) -> bool:
        self.get_calls.append((setting_type, env))
        return self.enabled

    def set_enabled(
        self,
        *,
        setting_type: str,
        enabled: bool,
        env: str,
        operator: str | None = None,
    ) -> bool:
        raise AssertionError("GET /settings must not mutate settings")


class _TaskSettingsModule(Module):
    def __init__(self, service: _FakeTaskSettingsService) -> None:
        self._service = service

    @singleton
    @provider
    def settings_service(self) -> TaskSettingsServiceProtocol:
        return self._service


def _client(service: _FakeTaskSettingsService) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="id-1",
        staffId="owner-1",
        operatorName="owner",
    )
    attach_injector(app, Injector([_TaskSettingsModule(service)]))
    return TestClient(app)


@pytest.mark.unit
def test_get_task_settings_lists_all_supported_switches():
    service = _FakeTaskSettingsService(enabled=True)
    response = _client(service).get("/api/v1/collaboration/tasks/settings")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["code"] == 200000
    states = payload["data"]
    by_type = {state["setting_type"]: state for state in states}
    assert set(by_type) == {SEARCH_SKILL, SKILL_REPORT, HARNESS_POLLER, RELAY_EXECUTION}
    assert all(state["enabled"] is True for state in states)
    assert {state["env"] for state in states} == {env for _setting, env in service.get_calls}
    assert service.get_calls
