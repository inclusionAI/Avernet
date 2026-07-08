"""Unit tests for engine.community.api.cron.schemas (HTTP request models).

Relocated from engine.community.core.cron.tests.test_models — these exercise the api-layer
request schemas, so they live in the api test tree (a core test importing
engine.community.api.* violates the api > core layering enforced by import-linter).
"""
import pytest
from pydantic import ValidationError

from engine.community.api.cron.schemas import (
    CreateTaskRequest,
    NotifyRequest,
    NotifyUpdateRequest,
    RunSingleAutoInitiateRequest,
    UpdateTaskRequest,
)


class TestNotifyRequest:
    def test_defaults(self):
        req = NotifyRequest()
        assert req.enabled is False
        assert req.user_ids is None

    def test_explicit_values(self):
        req = NotifyRequest(enabled=True, user_ids=["u1"])
        assert req.enabled is True
        assert req.user_ids == ["u1"]


class TestCreateTaskRequest:
    def test_required_fields(self):
        req = CreateTaskRequest(
            name="task",
            schedule="0 8 * * *",
            command="run report",
        )
        assert req.timezone == "Asia/Shanghai"  # default
        assert req.enabled is True              # default
        assert req.timeout_secs == 86400        # default
        assert req.model is None
        assert req.notify is None

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            CreateTaskRequest(name="task", schedule="0 8 * * *")  # command missing

    def test_full_construction(self):
        req = CreateTaskRequest(
            name="full-task",
            schedule="*/5 * * * *",
            command="echo hello",
            timezone="UTC",
            enabled=False,
            timeout_secs=3600,
            model="gpt-4",
            notify=NotifyRequest(enabled=True, user_ids=["u1"]),
        )
        assert req.timezone == "UTC"
        assert req.model == "gpt-4"
        assert req.notify.enabled is True


class TestUpdateTaskRequest:
    def test_all_optional(self):
        req = UpdateTaskRequest()
        assert req.name is None
        assert req.schedule is None
        assert req.command is None
        assert req.timeout_secs is None
        assert req.model is None
        assert req.notify is None

    def test_partial_update(self):
        req = UpdateTaskRequest(name="new", enabled=True, timeout_secs=600)
        assert req.name == "new"
        assert req.enabled is True
        assert req.timeout_secs == 600


class TestNotifyUpdateRequest:
    def test_all_none_defaults(self):
        req = NotifyUpdateRequest()
        assert req.enabled is None
        assert req.user_ids is None

    def test_partial_update(self):
        req = NotifyUpdateRequest(enabled=True)
        assert req.enabled is True
        assert req.user_ids is None


class TestRunSingleAutoInitiateRequest:
    def test_accepts_work_item_url(self):
        req = RunSingleAutoInitiateRequest(
            work_item_url="https://project.example.test/work-item/1",
            user_id="u1",
            agent_id="agent-1",
        )

        assert req.work_item_url == "https://project.example.test/work-item/1"
        assert req.workflow == ""
        assert req.append_message == ""
        assert req.model is None

    def test_rejects_unknown_legacy_url_field(self):
        with pytest.raises(ValidationError):
            RunSingleAutoInitiateRequest(
                legacy_url="https://project.example.test/work-item/1",
                user_id="u1",
                agent_id="agent-1",
            )
