"""Quality HTTP router unit tests.

Uses simple Fake/Stub implementations instead of mocking.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.quality import router
from agentclaw.community.api.quality_service import QualityTaskServiceProtocol
from agentclaw.community.api.task_processor_service import TaskProcessorProtocol
from agentclaw.community.core.quality.repositories import QualityTaskRecord


def _run(coro):
    """Run async route handler from sync tests."""
    return asyncio.run(coro)


def _make_record(
    id: int = 1,
    uuid: str = "test-uuid",
    task_type: str = "eval",
    biz_type: str = "service_bot_single",
    status: str = "init",
    bot_id: str | None = None,
    owner_id: str | None = None,
    ext: dict | None = None,
    operator_id: str | None = None,
    env: str = "test",
) -> QualityTaskRecord:
    """Create a test QualityTaskRecord."""
    return QualityTaskRecord(
        id=id,
        uuid=uuid,
        task_type=task_type,
        biz_type=biz_type,
        status=status,
        bot_id=bot_id,
        owner_id=owner_id,
        ext=ext or {},
        operator_id=operator_id,
        env=env,
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
    )


# ============================================================================
# Fake implementations (simple in-memory stubs)
# ============================================================================


class FakeQualityTaskService:
    """In-memory fake implementation of QualityTaskServiceProtocol."""

    def __init__(self):
        self._tasks: dict[int, QualityTaskRecord] = {}
        self._next_id = 1

    def add_task(self, record: QualityTaskRecord) -> QualityTaskRecord:
        """Add a task to the fake storage."""
        if record.id == 0:
            record = _make_record(
                id=self._next_id,
                uuid=record.uuid,
                task_type=record.task_type,
                biz_type=record.biz_type,
                status=record.status,
                bot_id=record.bot_id,
                owner_id=record.owner_id,
                ext=record.ext,
                operator_id=record.operator_id,
            )
            self._next_id += 1
        self._tasks[record.id] = record
        return record

    def get_task_by_id(self, id: int) -> Optional[QualityTaskRecord]:
        return self._tasks.get(id)

    def get_task_by_uuid(self, uuid: str) -> Optional[QualityTaskRecord]:
        for task in self._tasks.values():
            if task.uuid == uuid:
                return task
        return None

    def list_tasks(
        self,
        *,
        task_type: str,
        biz_type: str,
        bot_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[QualityTaskRecord], int]:
        tasks = [
            t for t in self._tasks.values()
            if t.task_type == task_type and t.biz_type == biz_type
        ]
        if bot_id:
            tasks = [t for t in tasks if t.bot_id == bot_id]
        if owner_id:
            tasks = [t for t in tasks if t.owner_id == owner_id]
        total = len(tasks)
        start = (page - 1) * page_size
        return tasks[start:start + page_size], total

    def create_task(
        self,
        *,
        task_type: str,
        biz_type: str,
        bot_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        ext: Optional[dict] = None,
        operator_id: Optional[str] = None,
    ) -> QualityTaskRecord:
        record = _make_record(
            id=self._next_id,
            task_type=task_type,
            biz_type=biz_type,
            bot_id=bot_id,
            owner_id=owner_id,
            ext=ext,
            operator_id=operator_id,
        )
        self._next_id += 1
        self._tasks[record.id] = record
        return record

    def update_task_status(
        self, id: int, status: str, ext: Optional[dict] = None
    ) -> Optional[QualityTaskRecord]:
        if id not in self._tasks:
            return None
        task = self._tasks[id]
        # Create new record with updated status
        updated = _make_record(
            id=task.id,
            uuid=task.uuid,
            task_type=task.task_type,
            biz_type=task.biz_type,
            status=status,
            bot_id=task.bot_id,
            owner_id=task.owner_id,
            ext=ext or task.ext,
            operator_id=task.operator_id,
        )
        self._tasks[id] = updated
        return updated


class FakeTaskProcessor:
    """Fake implementation of TaskProcessorProtocol."""

    def __init__(self, service: FakeQualityTaskService):
        self._service = service
        self.process_call_count = 0
        self.last_processed_id: Optional[int] = None

    async def process(self, id: int) -> QualityTaskRecord:
        """Advance task status: init → env_preparing → env_ready → task_created → task_executed → success."""
        self.process_call_count += 1
        self.last_processed_id = id

        task = self._service.get_task_by_id(id)
        if not task:
            raise ValueError(f"Task {id} not found")

        status_flow = {
            "init": "env_preparing",
            "env_preparing": "env_ready",
            "env_ready": "task_created",
            "task_created": "task_executed",
            "task_executed": "success",
        }
        new_status = status_flow.get(task.status, task.status)
        return self._service.update_task_status(id, new_status)

    async def to_env_preparing(self, id: int) -> QualityTaskRecord:
        task = self._service.get_task_by_id(id)
        if not task:
            raise ValueError(f"Task {id} not found")
        return self._service.update_task_status(id, "env_preparing")

    def to_env_ready(self, id: int) -> QualityTaskRecord:
        task = self._service.get_task_by_id(id)
        if not task:
            raise ValueError(f"Task {id} not found")
        return self._service.update_task_status(id, "env_ready")

    def to_task_created(self, id: int) -> QualityTaskRecord:
        task = self._service.get_task_by_id(id)
        if not task:
            raise ValueError(f"Task {id} not found")
        return self._service.update_task_status(id, "task_created")

    def to_task_executed(self, id: int) -> QualityTaskRecord:
        task = self._service.get_task_by_id(id)
        if not task:
            raise ValueError(f"Task {id} not found")
        return self._service.update_task_status(id, "task_executed")

    def to_env_released(self, id: int, source_status: str, target_status: str) -> QualityTaskRecord:
        task = self._service.get_task_by_id(id)
        if not task:
            raise ValueError(f"Task {id} not found")
        return self._service.update_task_status(id, target_status)

    def to_success(self, id: int) -> QualityTaskRecord:
        task = self._service.get_task_by_id(id)
        if not task:
            raise ValueError(f"Task {id} not found")
        return self._service.update_task_status(id, "success")

    def to_failed(self, id: int) -> QualityTaskRecord:
        task = self._service.get_task_by_id(id)
        if not task:
            raise ValueError(f"Task {id} not found")
        return self._service.update_task_status(id, "failed")


class FailingTaskProcessor:
    """Processor that raises exception for testing error handling."""

    def __init__(self, error: Exception):
        self._error = error

    async def process(self, id: int) -> QualityTaskRecord:
        raise self._error

    async def to_env_preparing(self, id: int) -> QualityTaskRecord:
        raise self._error

    def to_env_ready(self, id: int) -> QualityTaskRecord:
        raise self._error

    def to_task_created(self, id: int) -> QualityTaskRecord:
        raise self._error

    def to_task_executed(self, id: int) -> QualityTaskRecord:
        raise self._error

    def to_env_released(self, id: int, source_status: str, target_status: str) -> QualityTaskRecord:
        raise self._error

    def to_success(self, id: int) -> QualityTaskRecord:
        raise self._error

    def to_failed(self, id: int) -> QualityTaskRecord:
        raise self._error


# ============================================================================
# TestCases
# ============================================================================


class TestProcessTaskForOthers:
    """Tests for process_task_for_others endpoint."""

    def test_anonymous_user_returns_400(self):
        """Test that anonymous user gets 400 error (line 286)."""
        service = FakeQualityTaskService()
        processor = FakeTaskProcessor(service)
        anonymous_user = AuthenticatedUser(id="anonymous", staffId="anonymous", operatorName="anonymous")

        resp = _run(router.process_task_for_others(
            id=1,
            processor=processor,
            service=service,
            user=anonymous_user,
        ))

        assert resp.success is False
        assert resp.message == "无法获取用户信息"
        assert resp.error_code == 400
        assert resp.data is None
        # Should not call processor
        assert processor.process_call_count == 0

    def test_no_staff_id_returns_400(self):
        """Test that user with no staffId gets 400 error (line 286)."""
        service = FakeQualityTaskService()
        processor = FakeTaskProcessor(service)
        user_no_id = AuthenticatedUser(id="", staffId="", operatorName="test")

        resp = _run(router.process_task_for_others(
            id=1,
            processor=processor,
            service=service,
            user=user_no_id,
        ))

        assert resp.success is False
        assert resp.message == "无法获取用户信息"
        assert resp.error_code == 400
        assert resp.data is None

    def test_unexpected_exception_returns_500(self):
        """Test that unexpected exception returns 500 error (lines 318-320)."""
        service = FakeQualityTaskService()
        # Create a task so the lookup succeeds
        service.add_task(_make_record(id=1, bot_id="bot-1", owner_id="owner-1"))

        # Processor that raises unexpected error
        processor = FailingTaskProcessor(RuntimeError("Database connection lost"))

        super_admin_user = AuthenticatedUser(id="admin", staffId="100000", operatorName="admin")

        resp = _run(router.process_task_for_others(
            id=1,
            processor=processor,
            service=service,
            user=super_admin_user,
        ))

        assert resp.success is False
        assert "推进任务状态失败" in resp.message
        assert "Database connection lost" in resp.message
        assert resp.error_code == 500
        assert resp.data is None

    def test_non_super_admin_returns_403(self):
        """Test that non-super-admin user gets 403 error."""
        service = FakeQualityTaskService()
        processor = FakeTaskProcessor(service)
        regular_user = AuthenticatedUser(id="user-1", staffId="regular-user", operatorName="user")

        resp = _run(router.process_task_for_others(
            id=1,
            processor=processor,
            service=service,
            user=regular_user,
        ))

        assert resp.success is False
        assert resp.message == "无权限执行此操作"
        assert resp.error_code == 403
        assert resp.data is None

    def test_task_not_found_raises_404(self):
        """Test that missing task raises 404 HTTPException."""
        service = FakeQualityTaskService()  # Empty, no tasks
        processor = FakeTaskProcessor(service)
        super_admin_user = AuthenticatedUser(id="admin", staffId="100000", operatorName="admin")

        with pytest.raises(HTTPException) as exc_info:
            _run(router.process_task_for_others(
                id=999,
                processor=processor,
                service=service,
                user=super_admin_user,
            ))

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Task not found"

    def test_success_flow(self):
        """Test successful task processing."""
        service = FakeQualityTaskService()
        service.add_task(_make_record(id=1, bot_id="bot-1", owner_id="owner-1", status="init"))
        processor = FakeTaskProcessor(service)
        super_admin_user = AuthenticatedUser(id="admin", staffId="100000", operatorName="admin")

        resp = _run(router.process_task_for_others(
            id=1,
            processor=processor,
            service=service,
            user=super_admin_user,
        ))

        assert resp.success is True
        assert resp.message == "状态推进成功"
        assert resp.error_code == 200
        assert resp.data is not None
        assert resp.data["status"] == "env_preparing"
        assert processor.process_call_count == 1
        assert processor.last_processed_id == 1

    def test_success_flow_multiple_status_transitions(self):
        """Test task processing through multiple status transitions."""
        service = FakeQualityTaskService()
        service.add_task(_make_record(id=1, bot_id="bot-1", owner_id="owner-1", status="env_preparing"))
        processor = FakeTaskProcessor(service)
        super_admin_user = AuthenticatedUser(id="admin", staffId="100000", operatorName="admin2")

        resp = _run(router.process_task_for_others(
            id=1,
            processor=processor,
            service=service,
            user=super_admin_user,
        ))

        assert resp.success is True
        assert resp.data["status"] == "env_ready"

        # Process again
        resp2 = _run(router.process_task_for_others(
            id=1,
            processor=processor,
            service=service,
            user=super_admin_user,
        ))

        assert resp2.success is True
        assert resp2.data["status"] == "task_created"

    def test_http_exception_is_re_raised(self):
        """Test that HTTPException is re-raised, not caught by generic except (line 316)."""
        service = FakeQualityTaskService()
        service.add_task(_make_record(id=1, bot_id="bot-1", owner_id="owner-1"))

        # Processor that raises HTTPException
        processor = FailingTaskProcessor(HTTPException(status_code=503, detail="Service unavailable"))

        super_admin_user = AuthenticatedUser(id="admin", staffId="100000", operatorName="admin")

        with pytest.raises(HTTPException) as exc_info:
            _run(router.process_task_for_others(
                id=1,
                processor=processor,
                service=service,
                user=super_admin_user,
            ))

        assert exc_info.value.status_code == 503
        assert exc_info.value.detail == "Service unavailable"


class TestUpdateTaskStatusForOthers:
    """Tests for update_task_status_for_others endpoint."""

    def test_anonymous_user_returns_400(self):
        """Test that anonymous user gets 400 error."""
        service = FakeQualityTaskService()
        anonymous_user = AuthenticatedUser(id="anonymous", staffId="anonymous", operatorName="anonymous")

        resp = _run(router.update_task_status_for_others(
            id=1,
            status="running",
            service=service,
            user=anonymous_user,
        ))

        assert resp.success is False
        assert resp.message == "无法获取用户信息"
        assert resp.error_code == 400

    def test_non_super_admin_returns_403(self):
        """Test that non-super-admin user gets 403 error."""
        service = FakeQualityTaskService()
        regular_user = AuthenticatedUser(id="user-1", staffId="regular-user", operatorName="user")

        resp = _run(router.update_task_status_for_others(
            id=1,
            status="running",
            service=service,
            user=regular_user,
        ))

        assert resp.success is False
        assert resp.message == "无权限执行此操作"
        assert resp.error_code == 403

    def test_task_not_found_raises_404(self):
        """Test that missing task raises 404."""
        service = FakeQualityTaskService()  # Empty
        super_admin_user = AuthenticatedUser(id="admin", staffId="100000", operatorName="admin")

        with pytest.raises(HTTPException) as exc_info:
            _run(router.update_task_status_for_others(
                id=999,
                status="running",
                service=service,
                user=super_admin_user,
            ))

        assert exc_info.value.status_code == 404

    def test_success_update_status(self):
        """Test successful status update."""
        service = FakeQualityTaskService()
        service.add_task(_make_record(id=1, bot_id="bot-1", owner_id="owner-1", status="init"))
        super_admin_user = AuthenticatedUser(id="admin", staffId="100000", operatorName="admin")

        resp = _run(router.update_task_status_for_others(
            id=1,
            status="running",
            service=service,
            user=super_admin_user,
        ))

        assert resp.success is True
        assert resp.message == "状态更新成功"
        assert resp.data["status"] == "running"

    def test_unexpected_exception_returns_500(self):
        """Test unexpected exception returns 500."""
        service = FakeQualityTaskService()
        service.add_task(_make_record(id=1, bot_id="bot-1", owner_id="owner-1"))

        # Override update_task_status to raise error
        def failing_update(id, status, ext=None):
            raise RuntimeError("DB error")

        service.update_task_status = failing_update

        super_admin_user = AuthenticatedUser(id="admin", staffId="100000", operatorName="admin")

        resp = _run(router.update_task_status_for_others(
            id=1,
            status="running",
            service=service,
            user=super_admin_user,
        ))

        assert resp.success is False
        assert "更新任务状态失败" in resp.message
        assert resp.error_code == 500

    def test_all_super_admins_can_operate(self):
        """Test that every configured super_admin id can operate."""
        from agentclaw.community.core.access.admin_scopes import super_admin

        service = FakeQualityTaskService()
        service.add_task(_make_record(id=1, bot_id="bot-1", owner_id="owner-1", status="init"))

        admin_ids = sorted(super_admin())
        assert admin_ids, "test config should seed at least one super_admin"
        for admin_id in admin_ids:
            admin_user = AuthenticatedUser(id=f"admin-{admin_id}", staffId=admin_id, operatorName="admin")
            resp = _run(router.update_task_status_for_others(
                id=1,
                status="running",
                service=service,
                user=admin_user,
            ))
            assert resp.success is True, f"Admin {admin_id} should have access"