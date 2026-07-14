"""Unit tests for TaskProcessor service.

Tests status transitions:
- init → env_preparing → env_ready → task_created → task_executed → success/failed
"""
import logging
from unittest.mock import MagicMock, AsyncMock, call
from datetime import datetime

import pytest

from agentclaw.community.core.quality.repositories import QualityTaskRecord
from agentclaw.community.core.quality.services import task_processor as task_processor_module
from agentclaw.community.core.quality.services.task_processor import (
    InvalidStatusTransitionError,
    TaskProcessor,
    TaskStatus,
    _STATUS_LABELS,
    _TERMINAL_STATUSES,
)


def make_task(
    id: int = 1,
    status: str = "init",
    uuid: str = "test-uuid",
    task_type: str = "eval",
    biz_type: str = "service_bot_single",
    **kwargs,
) -> QualityTaskRecord:
    """Create a test QualityTaskRecord."""
    return QualityTaskRecord(
        id=id,
        uuid=uuid,
        task_type=task_type,
        biz_type=biz_type,
        status=status,
        bot_id=kwargs.get("bot_id"),
        owner_id=kwargs.get("owner_id"),
        ext=kwargs.get("ext", {}),
        operator_id=kwargs.get("operator_id"),
        env=kwargs.get("env", "test"),
        gmt_create=kwargs.get("gmt_create", datetime.now()),
        gmt_modified=kwargs.get("gmt_modified", datetime.now()),
    )


class TestStatusLabels:
    """Tests for status labels and terminal statuses."""

    def test_status_labels(self):
        """Test _STATUS_LABELS contains expected entries."""
        assert _STATUS_LABELS["init"] == "待执行"
        assert _STATUS_LABELS["running"] == "评测中"
        assert _STATUS_LABELS["judging"] == "评判中"
        assert _STATUS_LABELS["reporting"] == "报告生成中"
        assert _STATUS_LABELS["completed"] == "已完成"
        assert _STATUS_LABELS["failed"] == "已失败"

    def test_terminal_statuses(self):
        """Test _TERMINAL_STATUSES contains completed and failed."""
        assert "completed" in _TERMINAL_STATUSES
        assert "failed" in _TERMINAL_STATUSES
        assert "running" not in _TERMINAL_STATUSES


class TestTaskStatus:
    """Tests for TaskStatus enum."""

    def test_status_values(self):
        """Test status enum values."""
        assert TaskStatus.INIT.value == "init"
        assert TaskStatus.ENV_PREPARING.value == "env_preparing"
        assert TaskStatus.ENV_READY.value == "env_ready"
        assert TaskStatus.TASK_CREATED.value == "task_created"
        assert TaskStatus.TASK_EXECUTED.value == "task_executed"
        assert TaskStatus.SUCCESS.value == "success"
        assert TaskStatus.FAILED.value == "failed"


class TestInvalidStatusTransitionError:
    """Tests for InvalidStatusTransitionError."""

    def test_error_message(self):
        """Test error message format."""
        error = InvalidStatusTransitionError("success", "running")
        assert error.current_status == "success"
        assert error.target_status == "running"
        assert "success → running" in str(error)


class TestTaskProcessor:
    """Tests for TaskProcessor service."""

    @pytest.fixture
    def mock_repo(self):
        """Create a mock repository."""
        return MagicMock()

    @pytest.fixture
    def mock_masa_eval_http(self):
        """Create a mock HttpClient for MasaAgentEval."""
        return MagicMock()

    @pytest.fixture
    def mock_publish_flow_service(self):
        """Create a mock PublishFlowService."""
        mock = MagicMock()
        mock.eval_publish = AsyncMock(return_value={
            "bot_uuid": "test-bot-uuid-123",
            "baas_publish_id": "baas-publish-456",
        })
        mock.eval_teardown = MagicMock(return_value={})
        mock.get_baas_publish_progress = MagicMock(return_value={"status": "SUCCESS"})
        return mock

    @pytest.fixture
    def mock_tracer(self):
        """Create a mock TracerPlugin."""
        mock = MagicMock()
        mock.current_trace_id.return_value = None
        return mock

    @pytest.fixture
    def processor(self, mock_repo, mock_masa_eval_http, mock_publish_flow_service, mock_tracer):
        """Create a TaskProcessor with mock dependencies."""
        return TaskProcessor(mock_repo, mock_masa_eval_http, mock_publish_flow_service, mock_tracer)

    # ── process() routing tests ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_process_from_init_routes_to_env_preparing(
        self, processor, mock_repo, mock_publish_flow_service, caplog
    ):
        """process() routes init → to_env_preparing() → to_env_ready()."""
        # The module logger may be a non-propagating SOFAPy logger (propagate=False),
        # so caplog's root handler would miss it. Attach caplog's handler directly.
        task_processor_module.logger.addHandler(caplog.handler)
        try:
            caplog.set_level(logging.INFO)
            task_init = make_task(
                id=1,
                status="init",
                bot_id="test-bot-123",
                uuid="task-uuid-123",
                operator_id="operator-001",
                ext={"publish_id": "100"},
            )
            task_env_preparing = make_task(
                id=1,
                status="env_preparing",
                uuid="task-uuid-123",
                ext={"publish_id": "100", "bot_uuid": "test-bot-uuid-123", "baas_publish_id": "baas-456"},
            )
            # Call sequence:
            # 1. process: get_by_id -> init
            # 2. to_env_preparing: get_by_id -> init
            # 3. _transition_to (init -> env_preparing): get_by_id -> init
            # 4. to_env_ready: get_by_id -> env_preparing
            mock_repo.get_by_id.side_effect = [
                task_init,         # process
                task_init,         # to_env_preparing
                task_init,         # _transition_to (init -> env_preparing)
                task_env_preparing,  # to_env_ready
            ]
            mock_repo.update_status.return_value = task_env_preparing
            # BaaS publish still in progress, so to_env_ready returns unchanged
            mock_publish_flow_service.get_baas_publish_progress.return_value = {"status": "RUNNING"}

            result = await processor.process(1)

            assert result.status == "env_preparing"
            # Verify log records task info after fetch
            assert "[process] task fetched: id=1, status=init, bot_id=test-bot-123" in caplog.text
            mock_publish_flow_service.eval_publish.assert_called_once_with(
                publish_id=100,
                operator="operator-001",
                biz_id="task-uuid-123",
            )
        finally:
            task_processor_module.logger.removeHandler(caplog.handler)

    @pytest.mark.asyncio
    async def test_process_from_env_preparing_routes_to_env_ready(
        self, processor, mock_repo, mock_publish_flow_service, mock_masa_eval_http
    ):
        """process() routes env_preparing → to_env_ready() → to_task_created() when BaaS publish is SUCCESS."""
        task_env_preparing = make_task(
            id=1,
            status="env_preparing",
            bot_id="test-bot-id",
            owner_id="test-owner-id",
            ext={"baas_publish_id": "baas-123", "set_uuid": "test-set-uuid", "version": "v1.0"},
        )
        task_env_ready = make_task(
            id=1,
            status="env_ready",
            bot_id="test-bot-id",
            owner_id="test-owner-id",
            ext={"baas_publish_id": "baas-123", "set_uuid": "test-set-uuid", "version": "v1.0"},
        )
        task_task_created = make_task(
            id=1,
            status="task_created",
            bot_id="test-bot-id",
            owner_id="test-owner-id",
            ext={"baas_publish_id": "baas-123", "set_uuid": "test-set-uuid", "set_task_uuid": "task-uuid-123", "version": "v1.0"},
        )
        # Call sequence:
        # 1. process: get_by_id -> env_preparing
        # 2. to_env_ready: get_by_id -> env_preparing
        # 3. _transition_to (env_preparing -> env_ready): get_by_id -> env_preparing
        # 4. to_task_created: get_by_id -> env_ready
        # 5. to_task_executed: get_by_id -> task_created
        mock_repo.get_by_id.side_effect = [
            task_env_preparing,  # process
            task_env_preparing,  # to_env_ready
            task_env_preparing,  # _transition_to (env_preparing -> env_ready)
            task_env_ready,      # to_task_created
            task_task_created,   # to_task_executed
        ]
        mock_repo.update_status.return_value = task_task_created
        mock_publish_flow_service.get_baas_publish_progress.return_value = {"status": "SUCCESS"}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"success": true, "data": {"set_task_uuid": "task-uuid-123"}}'
        mock_response.json.return_value = {
            "success": True,
            "data": {"set_task_uuid": "task-uuid-123"},
        }
        mock_masa_eval_http.post.return_value = mock_response
        mock_progress_response = MagicMock()
        mock_progress_response.json.return_value = {
            "success": True,
            "data": {"set_task_uuid": "task-uuid-123", "status": "running"},
        }
        mock_masa_eval_http.get.return_value = mock_progress_response

        result = await processor.process(1)

        assert result.status == "task_created"
        mock_publish_flow_service.get_baas_publish_progress.assert_called_once_with(
            baas_publish_id="baas-123", include_devices=False
        )
        mock_masa_eval_http.post.assert_called_once()  # to_task_created calls /eval/start

    @pytest.mark.asyncio
    async def test_process_from_env_ready_routes_to_task_created(
        self, processor, mock_repo, mock_masa_eval_http
    ):
        """process() routes env_ready → to_task_created() → to_task_executed()."""
        task_env_ready = make_task(
            id=1,
            status="env_ready",
            bot_id="test-bot-id",
            ext={"set_uuid": "test-set-uuid", "set_task_uuid": "existing-task-uuid"},
        )
        task_task_created = make_task(
            id=1,
            status="task_created",
            bot_id="test-bot-id",
            ext={"set_task_uuid": "existing-task-uuid"},
        )
        mock_repo.get_by_id.side_effect = [task_env_ready, task_env_ready, task_task_created]
        mock_repo.update_status.return_value = task_task_created

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "data": {
                "set_task_uuid": "existing-task-uuid",
                "status": "running",
            },
        }
        mock_masa_eval_http.get.return_value = mock_response

        result = await processor.process(1)

        assert result.status == "task_created"
        mock_masa_eval_http.post.assert_not_called()
        mock_masa_eval_http.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_from_task_created_routes_to_task_executed(
        self, processor, mock_repo, mock_masa_eval_http
    ):
        """process() routes task_created → to_task_executed()."""
        task_task_created = make_task(
            id=1,
            status="task_created",
            bot_id="test-bot-id",
            ext={"set_task_uuid": "test-task-uuid"},
        )
        mock_repo.get_by_id.return_value = task_task_created
        mock_repo.update_status.return_value = make_task(
            id=1,
            status="task_executed",
            ext={"set_task_uuid": "test-task-uuid"},
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "data": {
                "set_task_uuid": "test-task-uuid",
                "status": "running",
            },
        }
        mock_masa_eval_http.get.return_value = mock_response

        result = await processor.process(1)

        assert result.status == "task_created"

    @pytest.mark.asyncio
    async def test_process_from_task_executed_routes_to_success(
        self, processor, mock_repo, mock_publish_flow_service
    ):
        """process() routes task_executed → to_env_released() → success."""
        mock_repo.get_by_id.return_value = make_task(
            id=1,
            status="task_executed",
            ext={"bot_uuid": "bot-uuid-to-teardown"},
            operator_id="operator-001",
        )
        mock_repo.update_status.return_value = make_task(id=1, status="success")

        result = await processor.process(1)

        assert result.status == "success"
        mock_publish_flow_service.eval_teardown.assert_called_once_with(
            "bot-uuid-to-teardown", operator="operator-001"
        )

    @pytest.mark.asyncio
    async def test_process_from_success_returns_unchanged(self, processor, mock_repo, caplog):
        """process() returns task unchanged for terminal status 'success'."""
        task_processor_module.logger.addHandler(caplog.handler)
        try:
            caplog.set_level(logging.INFO)
            mock_repo.get_by_id.return_value = make_task(id=1, status="success", bot_id="bot-1")

            result = await processor.process(1)

            assert result.status == "success"
            mock_repo.update_status.assert_not_called()
            # Verify terminal status log and task fetched log
            assert "[process] task fetched: id=1, status=success, bot_id=bot-1" in caplog.text
            assert "is at terminal status 'success', returning unchanged" in caplog.text
        finally:
            task_processor_module.logger.removeHandler(caplog.handler)

    @pytest.mark.asyncio
    async def test_process_from_failed_returns_unchanged(self, processor, mock_repo, caplog):
        """process() returns task unchanged for terminal status 'failed'."""
        task_processor_module.logger.addHandler(caplog.handler)
        try:
            caplog.set_level(logging.INFO)
            mock_repo.get_by_id.return_value = make_task(id=1, status="failed", bot_id="bot-2")

            result = await processor.process(1)

            assert result.status == "failed"
            mock_repo.update_status.assert_not_called()
            # Verify terminal status log and task fetched log
            assert "[process] task fetched: id=1, status=failed, bot_id=bot-2" in caplog.text
            assert "is at terminal status 'failed', returning unchanged" in caplog.text
        finally:
            task_processor_module.logger.removeHandler(caplog.handler)

    @pytest.mark.asyncio
    async def test_process_task_not_found_raises_error(self, processor, mock_repo, caplog):
        """process() raises ValueError if task not found."""
        # The module logger may be a non-propagating SOFAPy logger (propagate=False),
        # so caplog's root handler would miss it. Attach caplog's handler directly.
        task_processor_module.logger.addHandler(caplog.handler)
        try:
            caplog.set_level(logging.INFO)
            mock_repo.get_by_id.return_value = None

            with pytest.raises(ValueError) as exc_info:
                await processor.process(999)

            assert "Task not found: 999" in str(exc_info.value)
            # Verify log records None when task not found
            assert "[process] task fetched: id=999, status=None, bot_id=None" in caplog.text
        finally:
            task_processor_module.logger.removeHandler(caplog.handler)

    @pytest.mark.asyncio
    async def test_process_records_exception_to_ext_on_failure(self, processor, mock_repo, mock_publish_flow_service, mock_tracer, caplog):
        """process() records exception info to ext when transition fails."""
        task_processor_module.logger.addHandler(caplog.handler)
        try:
            caplog.set_level(logging.INFO)
            task_init = make_task(
                id=1,
                status="init",
                uuid="task-uuid-123",
                operator_id="operator-001",
                ext={"publish_id": "100"},
            )
            mock_repo.get_by_id.return_value = task_init
            mock_publish_flow_service.eval_publish.side_effect = RuntimeError("BaaS error")
            mock_repo.update_ext.return_value = True
            mock_tracer.current_trace_id.return_value = "trace-123"

            with pytest.raises(RuntimeError, match="BaaS error"):
                await processor.process(1)

            # Verify ext was updated with error info including trace_id
            mock_repo.update_ext.assert_called_once()
            call_args = mock_repo.update_ext.call_args
            assert call_args[0][0] == 1
            assert "error_msg" in call_args[0][1]
            assert "BaaS error" in call_args[0][1]["error_msg"]
            assert "trace_id: trace-123" in call_args[0][1]["error_msg"]
        finally:
            task_processor_module.logger.removeHandler(caplog.handler)

    @pytest.mark.asyncio
    async def test_process_records_exception_without_trace_id(self, processor, mock_repo, mock_publish_flow_service, mock_tracer, caplog):
        """process() records exception info without trace_id when tracer returns None."""
        task_processor_module.logger.addHandler(caplog.handler)
        try:
            caplog.set_level(logging.INFO)
            task_init = make_task(
                id=1,
                status="init",
                uuid="task-uuid-123",
                operator_id="operator-001",
                ext={"publish_id": "100"},
            )
            mock_repo.get_by_id.return_value = task_init
            mock_publish_flow_service.eval_publish.side_effect = RuntimeError("BaaS error")
            mock_repo.update_ext.return_value = True
            mock_tracer.current_trace_id.return_value = None

            with pytest.raises(RuntimeError, match="BaaS error"):
                await processor.process(1)

            # Verify ext was updated with error info without trace_id
            mock_repo.update_ext.assert_called_once()
            call_args = mock_repo.update_ext.call_args
            assert call_args[0][0] == 1
            assert "error_msg" in call_args[0][1]
            assert call_args[0][1]["error_msg"] == "BaaS error"
            assert "trace_id" not in call_args[0][1]["error_msg"]
        finally:
            task_processor_module.logger.removeHandler(caplog.handler)

    @pytest.mark.asyncio
    async def test_process_reraises_even_if_ext_update_fails(self, processor, mock_repo, mock_publish_flow_service, mock_tracer, caplog):
        """process() re-raises original exception even if ext update fails."""
        task_processor_module.logger.addHandler(caplog.handler)
        try:
            caplog.set_level(logging.INFO)
            task_init = make_task(
                id=1,
                status="init",
                uuid="task-uuid-123",
                operator_id="operator-001",
                ext={"publish_id": "100"},
            )
            mock_repo.get_by_id.return_value = task_init
            mock_publish_flow_service.eval_publish.side_effect = RuntimeError("BaaS error")
            mock_repo.update_ext.side_effect = ValueError("DB error")

            with pytest.raises(RuntimeError, match="BaaS error"):
                await processor.process(1)

            # Verify warning was logged about ext update failure
            assert "failed to update ext with error info" in caplog.text
        finally:
            task_processor_module.logger.removeHandler(caplog.handler)

    @pytest.mark.asyncio
    async def test_process_records_exception_with_none_ext(self, processor, mock_repo, mock_publish_flow_service, mock_tracer, caplog):
        """process() handles task.ext being None when recording exception."""
        task_processor_module.logger.addHandler(caplog.handler)
        try:
            caplog.set_level(logging.INFO)
            task_init = make_task(
                id=1,
                status="init",
                uuid="task-uuid-123",
                operator_id="operator-001",
                ext=None,  # ext is None
            )
            mock_repo.get_by_id.return_value = task_init
            mock_repo.update_ext.return_value = True
            mock_tracer.current_trace_id.return_value = None

            # When ext is None, to_env_preparing will raise ValueError for missing publish_id
            with pytest.raises(ValueError, match="missing publish_id"):
                await processor.process(1)

            # Verify ext was updated (creates new dict from None)
            mock_repo.update_ext.assert_called_once()
            call_args = mock_repo.update_ext.call_args
            assert call_args[0][0] == 1
            # ext is None, so create empty dict and add error_msg
            assert "error_msg" in call_args[0][1]
            assert "missing publish_id" in call_args[0][1]["error_msg"]
        finally:
            task_processor_module.logger.removeHandler(caplog.handler)

    # ── to_env_preparing() tests ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_to_env_preparing_success(
        self, processor, mock_repo, mock_publish_flow_service
    ):
        """to_env_preparing() calls eval_publish and transitions init → env_preparing, then calls to_env_ready."""
        task_init = make_task(
            id=1,
            status="init",
            uuid="task-uuid-123",
            operator_id="operator-001",
            ext={"publish_id": "100"},
        )
        task_env_preparing = make_task(
            id=1,
            status="env_preparing",
            uuid="task-uuid-123",
            bot_id="test-bot-id",
            ext={"publish_id": "100", "bot_uuid": "test-bot-uuid-123", "baas_publish_id": "baas-publish-456", "set_uuid": "test-set-uuid"},
        )
        # Call sequence:
        # 1. to_env_preparing: get_by_id -> init
        # 2. _transition_to (init -> env_preparing): get_by_id -> init
        # 3. to_env_ready: get_by_id -> env_preparing
        mock_repo.get_by_id.side_effect = [
            task_init,           # to_env_preparing
            task_init,           # _transition_to (init -> env_preparing)
            task_env_preparing,  # to_env_ready
        ]
        mock_repo.update_status.return_value = task_env_preparing
        # BaaS publish still in progress, so to_env_ready returns unchanged
        mock_publish_flow_service.get_baas_publish_progress.return_value = {"status": "RUNNING"}

        result = await processor.to_env_preparing(1)

        # When BaaS publish is RUNNING, to_env_ready returns env_preparing status
        assert result.status == "env_preparing"
        mock_publish_flow_service.eval_publish.assert_called_once_with(
            publish_id=100,
            operator="operator-001",
            biz_id="task-uuid-123",
        )
        # Verify bot_uuid and baas_publish_id are saved to ext (first update_status call)
        call_args = mock_repo.update_status.call_args_list[0]
        assert call_args[0][0] == 1
        assert call_args[0][1] == "env_preparing"
        ext_update = call_args[0][2]
        assert ext_update["bot_uuid"] == "test-bot-uuid-123"
        assert ext_update["baas_publish_id"] == "baas-publish-456"
        assert ext_update["source_status"] == "init"

    @pytest.mark.asyncio
    async def test_to_env_preparing_missing_publish_id_raises_error(
        self, processor, mock_repo, mock_publish_flow_service
    ):
        """to_env_preparing() raises error if publish_id is missing in ext."""
        mock_repo.get_by_id.return_value = make_task(
            id=1,
            status="init",
            uuid="task-uuid-123",
            ext={},  # Missing publish_id
        )

        with pytest.raises(ValueError) as exc_info:
            await processor.to_env_preparing(1)

        assert "missing publish_id" in str(exc_info.value)
        mock_publish_flow_service.eval_publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_to_env_preparing_missing_uuid_raises_error(
        self, processor, mock_repo, mock_publish_flow_service
    ):
        """to_env_preparing() raises error if uuid field is missing."""
        mock_repo.get_by_id.return_value = make_task(
            id=1,
            status="init",
            uuid=None,  # Missing uuid
            ext={"publish_id": "100"},
        )

        with pytest.raises(ValueError) as exc_info:
            await processor.to_env_preparing(1)

        assert "missing uuid" in str(exc_info.value)
        mock_publish_flow_service.eval_publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_to_env_preparing_wrong_status_raises_error(
        self, processor, mock_repo, mock_publish_flow_service
    ):
        """to_env_preparing() raises error if not in 'init' status."""
        mock_repo.get_by_id.return_value = make_task(
            id=1,
            status="task_created",
            uuid="task-uuid-123",
            ext={"publish_id": "100"},
        )

        with pytest.raises(ValueError) as exc_info:
            await processor.to_env_preparing(1)

        # Note: eval_publish is called before status check in _transition_to
        assert "Expected status 'init'" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_to_env_preparing_eval_publish_failure_raises_error(
        self, processor, mock_repo, mock_publish_flow_service
    ):
        """to_env_preparing() raises error if eval_publish fails."""
        mock_repo.get_by_id.return_value = make_task(
            id=1,
            status="init",
            uuid="task-uuid-123",
            operator_id="operator-001",
            ext={"publish_id": "100"},
        )
        mock_publish_flow_service.eval_publish.side_effect = Exception("Publish failed")

        with pytest.raises(Exception) as exc_info:
            await processor.to_env_preparing(1)

        assert "Publish failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_to_env_preparing_without_operator(
        self, processor, mock_repo, mock_publish_flow_service
    ):
        """to_env_preparing() uses empty string when operator_id is None."""
        task_init = make_task(
            id=1,
            status="init",
            uuid="task-uuid-123",
            operator_id=None,  # No operator
            ext={"publish_id": "100"},
        )
        task_env_preparing = make_task(
            id=1,
            status="env_preparing",
            uuid="task-uuid-123",
            ext={"publish_id": "100", "baas_publish_id": "baas-456"},
        )
        # Call sequence:
        # 1. to_env_preparing: get_by_id -> init
        # 2. _transition_to (init -> env_preparing): get_by_id -> init
        # 3. to_env_ready: get_by_id -> env_preparing
        mock_repo.get_by_id.side_effect = [
            task_init,           # to_env_preparing
            task_init,           # _transition_to (init -> env_preparing)
            task_env_preparing,  # to_env_ready
        ]
        mock_repo.update_status.return_value = task_env_preparing
        # BaaS publish still in progress
        mock_publish_flow_service.get_baas_publish_progress.return_value = {"status": "RUNNING"}

        result = await processor.to_env_preparing(1)

        assert result.status == "env_preparing"
        mock_publish_flow_service.eval_publish.assert_called_once_with(
            publish_id=100,
            operator="",  # Empty string when None
            biz_id="task-uuid-123",
        )

    @pytest.mark.asyncio
    async def test_to_env_preparing_result_without_bot_uuid(
        self, processor, mock_repo, mock_publish_flow_service
    ):
        """to_env_preparing() handles result without bot_uuid/baas_publish_id."""
        task_init = make_task(
            id=1,
            status="init",
            uuid="task-uuid-123",
            operator_id="operator-001",
            ext={"publish_id": "100"},
        )
        task_env_preparing = make_task(
            id=1,
            status="env_preparing",
            uuid="task-uuid-123",
            ext={"publish_id": "100"},
        )
        # Call sequence:
        # 1. to_env_preparing: get_by_id -> init
        # 2. _transition_to (init -> env_preparing): get_by_id -> init
        # 3. to_env_ready: get_by_id -> env_preparing (will fail due to missing baas_publish_id)
        mock_repo.get_by_id.side_effect = [
            task_init,           # to_env_preparing
            task_init,           # _transition_to (init -> env_preparing)
            task_env_preparing,  # to_env_ready
        ]
        mock_repo.update_status.return_value = task_env_preparing
        mock_publish_flow_service.eval_publish.return_value = {}  # No bot_uuid/baas_publish_id

        # to_env_ready will raise ValueError because baas_publish_id is missing
        with pytest.raises(ValueError) as exc_info:
            await processor.to_env_preparing(1)

        assert "missing baas_publish_id" in str(exc_info.value)
        # Verify bot_uuid and baas_publish_id are NOT saved to ext (first update_status call)
        call_args = mock_repo.update_status.call_args_list[0]
        ext_update = call_args[0][2]
        assert "bot_uuid" not in ext_update
        assert "baas_publish_id" not in ext_update

    # ── to_env_ready() tests ──────────────────────────────────────────────────

    def test_to_env_ready_success(self, processor, mock_repo, mock_publish_flow_service, mock_masa_eval_http):
        """to_env_ready() transitions env_preparing → env_ready → task_created when BaaS publish is SUCCESS."""
        task_env_preparing = make_task(
            id=1,
            status="env_preparing",
            bot_id="test-bot-id",
            owner_id="test-owner-id",
            ext={"baas_publish_id": "baas-123", "set_uuid": "test-set-uuid", "version": "v1.0"},
        )
        task_env_ready = make_task(
            id=1,
            status="env_ready",
            bot_id="test-bot-id",
            owner_id="test-owner-id",
            ext={"baas_publish_id": "baas-123", "set_uuid": "test-set-uuid", "version": "v1.0"},
        )
        task_task_created = make_task(
            id=1,
            status="task_created",
            bot_id="test-bot-id",
            owner_id="test-owner-id",
            ext={"baas_publish_id": "baas-123", "set_uuid": "test-set-uuid", "set_task_uuid": "task-uuid-123", "version": "v1.0"},
        )
        # Call sequence:
        # 1. to_env_ready: get_by_id -> env_preparing
        # 2. _transition_to (env_preparing -> env_ready): get_by_id -> env_preparing
        # 3. to_task_created: get_by_id -> env_ready
        # 4. to_task_executed: get_by_id -> task_created
        mock_repo.get_by_id.side_effect = [
            task_env_preparing,  # to_env_ready
            task_env_preparing,  # _transition_to (env_preparing -> env_ready)
            task_env_ready,      # to_task_created
            task_task_created,   # to_task_executed
        ]
        mock_repo.update_status.return_value = task_task_created
        mock_publish_flow_service.get_baas_publish_progress.return_value = {"status": "SUCCESS"}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"success": true, "data": {"set_task_uuid": "task-uuid-123"}}'
        mock_response.json.return_value = {
            "success": True,
            "data": {"set_task_uuid": "task-uuid-123"},
        }
        mock_masa_eval_http.post.return_value = mock_response
        mock_progress_response = MagicMock()
        mock_progress_response.json.return_value = {
            "success": True,
            "data": {"set_task_uuid": "task-uuid-123", "status": "running"},
        }
        mock_masa_eval_http.get.return_value = mock_progress_response

        result = processor.to_env_ready(1)

        assert result.status == "task_created"
        mock_publish_flow_service.get_baas_publish_progress.assert_called_once_with(
            baas_publish_id="baas-123", include_devices=False
        )
        mock_masa_eval_http.post.assert_called_once()  # to_task_created calls /eval/start

    def test_to_env_ready_in_progress_returns_unchanged(
        self, processor, mock_repo, mock_publish_flow_service
    ):
        """to_env_ready() returns unchanged task when BaaS publish is still RUNNING."""
        task = make_task(
            id=1,
            status="env_preparing",
            ext={"baas_publish_id": "baas-123"},
        )
        mock_repo.get_by_id.return_value = task
        mock_publish_flow_service.get_baas_publish_progress.return_value = {"status": "RUNNING"}

        result = processor.to_env_ready(1)

        assert result.status == "env_preparing"
        mock_repo.update_ext.assert_called_once()
        mock_repo.update_status.assert_not_called()

    def test_to_env_ready_failed_transitions_to_failed(
        self, processor, mock_repo, mock_publish_flow_service
    ):
        """to_env_ready() transitions env_preparing → failed when BaaS publish FAILED and calls teardown."""
        mock_repo.get_by_id.return_value = make_task(
            id=1,
            status="env_preparing",
            ext={"baas_publish_id": "baas-123", "bot_uuid": "bot-uuid-456"},
            operator_id="operator-001",
        )
        mock_repo.update_status.return_value = make_task(
            id=1, status="failed", ext={"baas_publish_id": "baas-123", "error": "BaaS error"}
        )
        mock_publish_flow_service.get_baas_publish_progress.return_value = {
            "status": "FAILED",
            "error": "BaaS error",
        }
        mock_publish_flow_service.eval_teardown.return_value = {
            "destroy_publish_id": "destroy-123"
        }

        result = processor.to_env_ready(1)

        # Verify teardown was called to release environment
        mock_publish_flow_service.eval_teardown.assert_called_once_with(
            "bot-uuid-456", operator="operator-001"
        )
        # Verify ext was updated with error before teardown
        mock_repo.update_ext.assert_called_once()
        ext_call_arg = mock_repo.update_ext.call_args[0][1]
        assert ext_call_arg["error_msg"] == "BaaS error"
        assert ext_call_arg["baas_publish_progress"]["status"] == "FAILED"
        # Verify final status is failed and destroy_publish_id in ext
        assert result.status == "failed"
        call_args = mock_repo.update_status.call_args
        assert call_args[0][0] == 1
        assert call_args[0][1] == "failed"
        assert call_args[0][2]["source_status"] == "env_preparing"
        assert call_args[0][2]["destroy_publish_id"] == "destroy-123"

    def test_to_env_ready_missing_baas_publish_id_raises_error(self, processor, mock_repo):
        """to_env_ready() raises error if baas_publish_id is missing."""
        mock_repo.get_by_id.return_value = make_task(
            id=1,
            status="env_preparing",
            ext={},
        )

        with pytest.raises(ValueError) as exc_info:
            processor.to_env_ready(1)

        assert "missing baas_publish_id" in str(exc_info.value)

    def test_to_env_ready_wrong_status_raises_error(self, processor, mock_repo):
        """to_env_ready() raises error if not in 'env_preparing' status."""
        mock_repo.get_by_id.return_value = make_task(
            id=1,
            status="init",
            ext={"baas_publish_id": "baas-123"},
        )

        with pytest.raises(ValueError) as exc_info:
            processor.to_env_ready(1)

        assert "Expected status 'env_preparing'" in str(exc_info.value)

    def test_to_env_ready_failed_without_bot_uuid_skips_teardown(
        self, processor, mock_repo, mock_publish_flow_service
    ):
        """to_env_ready() skips teardown when bot_uuid is missing."""
        mock_repo.get_by_id.return_value = make_task(
            id=1,
            status="env_preparing",
            ext={"baas_publish_id": "baas-123"},  # no bot_uuid
        )
        mock_repo.update_status.return_value = make_task(
            id=1, status="failed", ext={"baas_publish_id": "baas-123", "error": "BaaS error"}
        )
        mock_publish_flow_service.get_baas_publish_progress.return_value = {
            "status": "FAILED",
            "error": "BaaS error",
        }

        result = processor.to_env_ready(1)

        # Verify teardown was NOT called since no bot_uuid
        mock_publish_flow_service.eval_teardown.assert_not_called()
        # Verify ext was still updated with error
        mock_repo.update_ext.assert_called_once()
        ext_call_arg = mock_repo.update_ext.call_args[0][1]
        assert ext_call_arg["error_msg"] == "BaaS error"
        # Verify final status is failed and no destroy_publish_id in ext
        assert result.status == "failed"
        call_args = mock_repo.update_status.call_args
        assert "destroy_publish_id" not in call_args[0][2]

    # ── to_task_created() tests ──────────────────────────────────────────────

    def test_to_task_created_with_existing_set_task_uuid(
        self, processor, mock_repo, mock_masa_eval_http
    ):
        """to_task_created() skips API call when set_task_uuid already exists."""
        env_ready_task = make_task(
            id=1,
            status="env_ready",
            bot_id="test-bot-id",
            ext={"set_uuid": "test-set-uuid", "set_task_uuid": "existing-task-uuid"},
        )
        task_created_task = make_task(
            id=1,
            status="task_created",
            ext={"set_uuid": "test-set-uuid", "set_task_uuid": "existing-task-uuid"},
        )
        mock_repo.get_by_id.side_effect = [env_ready_task, task_created_task]
        mock_repo.update_status.return_value = task_created_task

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "data": {"set_task_uuid": "existing-task-uuid", "status": "running"},
        }
        mock_masa_eval_http.get.return_value = mock_response

        result = processor.to_task_created(1)

        assert result.status == "task_created"
        mock_masa_eval_http.post.assert_not_called()
        mock_masa_eval_http.get.assert_called_once()

    def test_to_task_created_calls_eval_start_api(
        self, processor, mock_repo, mock_masa_eval_http
    ):
        """to_task_created() calls /eval/start API when set_task_uuid missing."""
        env_ready_task = make_task(
            id=1,
            status="env_ready",
            uuid="task-uuid-123",
            task_type="eval",
            bot_id="test-bot-id",
            owner_id="test-owner-id",
            ext={"set_uuid": "test-set-uuid", "version": "v1.0"},
        )
        task_created_task = make_task(
            id=1,
            status="task_created",
            bot_id="test-bot-id",
            owner_id="test-owner-id",
            ext={"set_uuid": "test-set-uuid", "set_task_uuid": "new-task-uuid", "version": "v1.0"},
        )
        mock_repo.get_by_id.side_effect = [env_ready_task, task_created_task]
        mock_repo.update_status.return_value = task_created_task

        mock_start_response = MagicMock()
        mock_start_response.status_code = 200
        mock_start_response.text = '{"success": true, "data": {"set_task_uuid": "new-task-uuid"}}'
        mock_start_response.json.return_value = {
            "success": True,
            "data": {"set_task_uuid": "new-task-uuid"},
        }
        mock_masa_eval_http.post.return_value = mock_start_response

        mock_progress_response = MagicMock()
        mock_progress_response.json.return_value = {
            "success": True,
            "data": {"set_task_uuid": "new-task-uuid", "status": "running"},
        }
        mock_masa_eval_http.get.return_value = mock_progress_response

        result = processor.to_task_created(1)

        assert result.status == "task_created"
        mock_masa_eval_http.post.assert_called_once_with(
            "/eval/start",
            json={
                "env": "eval-task-uuid-123",
                "bot_id": "test-bot-id:test-owner-id",
                "set_uuid": "test-set-uuid",
                "version": "v1.0",
            },
        )
        mock_masa_eval_http.get.assert_called_once()

    def test_to_task_created_missing_set_uuid_raises_error(
        self, processor, mock_repo, mock_masa_eval_http
    ):
        """to_task_created() raises error if set_uuid is missing in ext."""
        mock_repo.get_by_id.return_value = make_task(
            id=1,
            status="env_ready",
            bot_id="test-bot-id",
            ext={},  # Missing set_uuid
        )

        with pytest.raises(ValueError) as exc_info:
            processor.to_task_created(1)

        assert "missing set_uuid" in str(exc_info.value)
        mock_masa_eval_http.post.assert_not_called()

    def test_to_task_created_missing_bot_id_raises_error(
        self, processor, mock_repo, mock_masa_eval_http
    ):
        """to_task_created() raises error if bot_id is missing."""
        mock_repo.get_by_id.return_value = make_task(
            id=1,
            status="env_ready",
            bot_id=None,  # Missing bot_id
            ext={"set_uuid": "test-set-uuid", "version": "v1.0"},
        )

        with pytest.raises(ValueError) as exc_info:
            processor.to_task_created(1)

        assert "missing bot_id" in str(exc_info.value)
        mock_masa_eval_http.post.assert_not_called()

    def test_to_task_created_missing_owner_id_raises_error(
        self, processor, mock_repo, mock_masa_eval_http
    ):
        """to_task_created() raises error if owner_id is missing."""
        mock_repo.get_by_id.return_value = make_task(
            id=1,
            status="env_ready",
            bot_id="test-bot-id",
            owner_id=None,  # Missing owner_id
            ext={"set_uuid": "test-set-uuid", "version": "v1.0"},
        )

        with pytest.raises(ValueError) as exc_info:
            processor.to_task_created(1)

        assert "missing owner_id" in str(exc_info.value)
        mock_masa_eval_http.post.assert_not_called()

    def test_to_task_created_missing_version_raises_error(
        self, processor, mock_repo, mock_masa_eval_http
    ):
        """to_task_created() raises error if version is missing in ext."""
        mock_repo.get_by_id.return_value = make_task(
            id=1,
            status="env_ready",
            bot_id="test-bot-id",
            owner_id="test-owner-id",
            ext={"set_uuid": "test-set-uuid"},  # Missing version
        )

        with pytest.raises(ValueError) as exc_info:
            processor.to_task_created(1)

        assert "missing version" in str(exc_info.value)
        mock_masa_eval_http.post.assert_not_called()

    def test_to_task_created_converts_version_to_string(
        self, processor, mock_repo, mock_masa_eval_http
    ):
        """to_task_created() converts integer version to string for API call."""
        env_ready_task = make_task(
            id=1,
            status="env_ready",
            uuid="task-uuid-456",
            task_type="eval",
            bot_id="test-bot-id",
            owner_id="test-owner-id",
            ext={"set_uuid": "test-set-uuid", "version": 123},  # version as int
        )
        task_created_task = make_task(
            id=1,
            status="task_created",
            bot_id="test-bot-id",
            owner_id="test-owner-id",
            ext={"set_uuid": "test-set-uuid", "set_task_uuid": "new-task-uuid", "version": 123},
        )
        mock_repo.get_by_id.side_effect = [env_ready_task, task_created_task]
        mock_repo.update_status.return_value = task_created_task

        mock_start_response = MagicMock()
        mock_start_response.status_code = 200
        mock_start_response.text = '{"success": true, "data": {"set_task_uuid": "new-task-uuid"}}'
        mock_start_response.json.return_value = {
            "success": True,
            "data": {"set_task_uuid": "new-task-uuid"},
        }
        mock_masa_eval_http.post.return_value = mock_start_response

        mock_progress_response = MagicMock()
        mock_progress_response.json.return_value = {
            "success": True,
            "data": {"set_task_uuid": "new-task-uuid", "status": "running"},
        }
        mock_masa_eval_http.get.return_value = mock_progress_response

        result = processor.to_task_created(1)

        assert result.status == "task_created"
        # Verify version was converted to string "123" in API call
        mock_masa_eval_http.post.assert_called_once_with(
            "/eval/start",
            json={
                "env": "eval-task-uuid-456",
                "bot_id": "test-bot-id:test-owner-id",
                "set_uuid": "test-set-uuid",
                "version": "123",  # should be string, not int
            },
        )

    def test_to_task_created_api_returns_error_raises_value_error(
        self, processor, mock_repo, mock_masa_eval_http
    ):
        """to_task_created() raises ValueError if API returns success=false."""
        mock_repo.get_by_id.return_value = make_task(
            id=1,
            status="env_ready",
            bot_id="test-bot-id",
            owner_id="test-owner-id",
            ext={"set_uuid": "test-set-uuid", "version": "v1.0"},
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"success": false, "message": "Evaluation set not found"}'
        mock_response.json.return_value = {
            "success": False,
            "message": "Evaluation set not found",
        }
        mock_masa_eval_http.post.return_value = mock_response

        with pytest.raises(ValueError) as exc_info:
            processor.to_task_created(1)

        assert "Eval start failed" in str(exc_info.value)

    # ── to_task_executed() tests ──────────────────────────────────────────────

    def test_to_task_executed_non_terminal_status_returns_task(
        self, processor, mock_repo, mock_masa_eval_http
    ):
        """to_task_executed() returns task unchanged when status is non-terminal."""
        task = make_task(
            id=1,
            status="task_created",
            ext={"set_task_uuid": "test-task-uuid"},
        )
        mock_repo.get_by_id.return_value = task

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "data": {
                "set_task_uuid": "test-task-uuid",
                "status": "running",
            },
        }
        mock_masa_eval_http.get.return_value = mock_response

        result = processor.to_task_executed(1)

        assert result.status == "task_created"
        mock_masa_eval_http.get.assert_called_once_with(
            "/eval/progress?set_task_uuid=test-task-uuid"
        )

    def test_to_task_executed_completed_transitions_to_success(
        self, processor, mock_repo, mock_masa_eval_http, mock_publish_flow_service
    ):
        """to_task_executed() transitions to success when status is 'completed'."""
        task_created = make_task(
            id=1,
            status="task_created",
            ext={"set_task_uuid": "test-task-uuid", "bot_uuid": "bot-uuid-123"},
            operator_id="operator-001",
        )
        task_executed = make_task(
            id=1,
            status="task_executed",
            ext={"set_task_uuid": "test-task-uuid", "bot_uuid": "bot-uuid-123"},
            operator_id="operator-001",
        )
        task_success = make_task(
            id=1,
            status="success",
            ext={"set_task_uuid": "test-task-uuid", "bot_uuid": "bot-uuid-123"},
            operator_id="operator-001",
        )
        # get_by_id sequence:
        # 1. to_task_executed() initial call
        # 2. _transition_to_with_ext() call
        # 3. to_env_released() -> get_by_id for teardown
        # 4. to_env_released() -> _transition_to() call
        mock_repo.get_by_id.side_effect = [task_created, task_created, task_executed, task_executed]
        mock_repo.update_status.side_effect = [task_executed, task_success]

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "data": {
                "set_task_uuid": "test-task-uuid",
                "status": "completed",
            },
        }
        mock_masa_eval_http.get.return_value = mock_response

        result = processor.to_task_executed(1)

        assert result.status == "success"
        mock_masa_eval_http.get.assert_called_once()
        assert mock_repo.update_status.call_count == 2
        # Verify eval_teardown was called
        mock_publish_flow_service.eval_teardown.assert_called_once_with(
            "bot-uuid-123", operator="operator-001"
        )

    def test_to_task_executed_failed_transitions_to_failed(
        self, processor, mock_repo, mock_masa_eval_http, mock_publish_flow_service
    ):
        """to_task_executed() transitions to failed when status is 'failed'."""
        task_created = make_task(
            id=1,
            status="task_created",
            ext={"set_task_uuid": "test-task-uuid", "bot_uuid": "bot-uuid-123"},
            operator_id="operator-001",
        )
        task_executed = make_task(
            id=1,
            status="task_executed",
            ext={"set_task_uuid": "test-task-uuid", "bot_uuid": "bot-uuid-123"},
            operator_id="operator-001",
        )
        task_failed = make_task(
            id=1,
            status="failed",
            ext={"set_task_uuid": "test-task-uuid", "bot_uuid": "bot-uuid-123"},
            operator_id="operator-001",
        )
        # get_by_id sequence:
        # 1. to_task_executed() initial call
        # 2. _transition_to_with_ext() call
        # 3. to_env_released() -> get_by_id for teardown
        # 4. to_env_released() -> _transition_to() call
        mock_repo.get_by_id.side_effect = [task_created, task_created, task_executed, task_executed]
        mock_repo.update_status.side_effect = [task_executed, task_failed]

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "data": {
                "set_task_uuid": "test-task-uuid",
                "status": "failed",
            },
        }
        mock_masa_eval_http.get.return_value = mock_response

        result = processor.to_task_executed(1)

        assert result.status == "failed"
        mock_masa_eval_http.get.assert_called_once()
        assert mock_repo.update_status.call_count == 2
        # Verify error_msg contains set_task_uuid
        mock_repo.update_ext.assert_called_once()
        ext_call_arg = mock_repo.update_ext.call_args[0][1]
        assert "set_task_uuid=test-task-uuid" in ext_call_arg["error_msg"]
        # Verify eval_teardown was called
        mock_publish_flow_service.eval_teardown.assert_called_once_with(
            "bot-uuid-123", operator="operator-001"
        )

    def test_to_task_executed_missing_set_task_uuid_raises_error(
        self, processor, mock_repo, mock_masa_eval_http
    ):
        """to_task_executed() raises error if set_task_uuid is missing."""
        mock_repo.get_by_id.return_value = make_task(
            id=1,
            status="task_created",
            ext={},  # Missing set_task_uuid
        )

        with pytest.raises(ValueError) as exc_info:
            processor.to_task_executed(1)

        assert "missing set_task_uuid" in str(exc_info.value)
        mock_masa_eval_http.get.assert_not_called()

    def test_to_task_executed_api_returns_error_raises_value_error(
        self, processor, mock_repo, mock_masa_eval_http
    ):
        """to_task_executed() raises ValueError if API returns success=false."""
        mock_repo.get_by_id.return_value = make_task(
            id=1,
            status="task_created",
            ext={"set_task_uuid": "test-task-uuid"},
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": False,
            "message": "Task not found",
        }
        mock_masa_eval_http.get.return_value = mock_response

        with pytest.raises(ValueError) as exc_info:
            processor.to_task_executed(1)

        assert "Eval progress failed" in str(exc_info.value)

    # ── to_env_released() tests ──────────────────────────────────────────────

    def test_to_env_released_to_success_with_bot_uuid(
        self, processor, mock_repo, mock_publish_flow_service
    ):
        """to_env_released() calls eval_teardown when bot_uuid exists and saves destroy_publish_id."""
        mock_repo.get_by_id.return_value = make_task(
            id=1,
            status="task_executed",
            ext={"bot_uuid": "bot-uuid-123"},
            operator_id="operator-001",
        )
        mock_repo.update_status.return_value = make_task(id=1, status="success")
        mock_publish_flow_service.eval_teardown.return_value = {
            "destroy_publish_id": "destroy-456"
        }

        result = processor.to_env_released(1, "task_executed", "success")

        assert result.status == "success"
        mock_publish_flow_service.eval_teardown.assert_called_once_with(
            "bot-uuid-123", operator="operator-001"
        )
        mock_repo.update_status.assert_called_once_with(
            1, "success", {"bot_uuid": "bot-uuid-123", "destroy_publish_id": "destroy-456", "source_status": "task_executed"}
        )

    def test_to_env_released_to_success_without_bot_uuid(
        self, processor, mock_repo, mock_publish_flow_service
    ):
        """to_env_released() skips eval_teardown when bot_uuid is missing."""
        mock_repo.get_by_id.return_value = make_task(
            id=1,
            status="task_executed",
            ext={},  # No bot_uuid
            operator_id="operator-001",
        )
        mock_repo.update_status.return_value = make_task(id=1, status="success")

        result = processor.to_env_released(1, "task_executed", "success")

        assert result.status == "success"
        mock_publish_flow_service.eval_teardown.assert_not_called()
        # ext should only have source_status, no destroy_publish_id
        mock_repo.update_status.assert_called_once_with(
            1, "success", {"source_status": "task_executed"}
        )

    def test_to_env_released_to_failed_with_bot_uuid(
        self, processor, mock_repo, mock_publish_flow_service
    ):
        """to_env_released() transitions to failed and calls eval_teardown."""
        mock_repo.get_by_id.return_value = make_task(
            id=1,
            status="task_executed",
            ext={"bot_uuid": "bot-uuid-123"},
            operator_id="operator-001",
        )
        mock_repo.update_status.return_value = make_task(id=1, status="failed")
        mock_publish_flow_service.eval_teardown.return_value = {
            "destroy_publish_id": "destroy-789"
        }

        result = processor.to_env_released(1, "task_executed", "failed")

        assert result.status == "failed"
        mock_publish_flow_service.eval_teardown.assert_called_once_with(
            "bot-uuid-123", operator="operator-001"
        )
        # Verify destroy_publish_id is saved
        call_args = mock_repo.update_status.call_args
        assert call_args[0][2]["destroy_publish_id"] == "destroy-789"

    def test_to_env_released_eval_teardown_failure_logged(
        self, processor, mock_repo, mock_publish_flow_service
    ):
        """to_env_released() logs warning but continues when eval_teardown fails."""
        mock_repo.get_by_id.return_value = make_task(
            id=1,
            status="task_executed",
            ext={"bot_uuid": "bot-uuid-123"},
            operator_id="operator-001",
        )
        mock_repo.update_status.return_value = make_task(id=1, status="success")
        mock_publish_flow_service.eval_teardown.side_effect = Exception("Teardown error")

        # Should not raise, just log warning
        result = processor.to_env_released(1, "task_executed", "success")

        assert result.status == "success"
        mock_publish_flow_service.eval_teardown.assert_called_once()
        # No destroy_publish_id since teardown failed
        call_args = mock_repo.update_status.call_args
        assert "destroy_publish_id" not in call_args[0][2]

    def test_to_env_released_wrong_status_raises_error(self, processor, mock_repo):
        """to_env_released() raises error if current status doesn't match source."""
        mock_repo.get_by_id.return_value = make_task(id=1, status="success")

        with pytest.raises(ValueError) as exc_info:
            processor.to_env_released(1, "task_executed", "success")

        assert "Expected status 'task_executed'" in str(exc_info.value)

    def test_to_env_released_teardown_result_none_no_destroy_publish_id(
        self, processor, mock_repo, mock_publish_flow_service
    ):
        """to_env_released() handles teardown_result=None gracefully."""
        mock_repo.get_by_id.return_value = make_task(
            id=1,
            status="task_executed",
            ext={"bot_uuid": "bot-uuid-123"},
            operator_id="operator-001",
        )
        mock_repo.update_status.return_value = make_task(id=1, status="success")
        mock_publish_flow_service.eval_teardown.return_value = None

        result = processor.to_env_released(1, "task_executed", "success")

        assert result.status == "success"
        mock_publish_flow_service.eval_teardown.assert_called_once()
        # No destroy_publish_id since teardown_result is None
        call_args = mock_repo.update_status.call_args
        assert "destroy_publish_id" not in call_args[0][2]

    def test_to_env_released_teardown_result_empty_dict_no_destroy_publish_id(
        self, processor, mock_repo, mock_publish_flow_service
    ):
        """to_env_released() handles teardown_result={} without destroy_publish_id."""
        mock_repo.get_by_id.return_value = make_task(
            id=1,
            status="task_executed",
            ext={"bot_uuid": "bot-uuid-123"},
            operator_id="operator-001",
        )
        mock_repo.update_status.return_value = make_task(id=1, status="success")
        mock_publish_flow_service.eval_teardown.return_value = {}

        result = processor.to_env_released(1, "task_executed", "success")

        assert result.status == "success"
        mock_publish_flow_service.eval_teardown.assert_called_once()
        # No destroy_publish_id since teardown_result is empty dict
        call_args = mock_repo.update_status.call_args
        assert "destroy_publish_id" not in call_args[0][2]

    # ── _call_eval_start_api() tests ──────────────────────────────────────────

    def test_call_eval_start_api_success(self, processor, mock_masa_eval_http):
        """_call_eval_start_api() returns set_task_uuid on success."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"success": true, "data": {"set_task_uuid": "new-task-uuid"}}'
        mock_response.json.return_value = {
            "success": True,
            "data": {"set_task_uuid": "new-task-uuid"},
        }
        mock_masa_eval_http.post.return_value = mock_response

        result = processor._call_eval_start_api("pre", "test-bot-id", "test-set-uuid", "v1.0")

        assert result == "new-task-uuid"
        mock_masa_eval_http.post.assert_called_once_with(
            "/eval/start",
            json={
                "env": "pre",
                "bot_id": "test-bot-id",
                "set_uuid": "test-set-uuid",
                "version": "v1.0",
            },
        )

    def test_call_eval_start_api_missing_data_raises_error(
        self, processor, mock_masa_eval_http
    ):
        """_call_eval_start_api() raises error if data field is missing."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"success": true, "data": null}'
        mock_response.json.return_value = {
            "success": True,
            "data": None,
        }
        mock_masa_eval_http.post.return_value = mock_response

        with pytest.raises(ValueError) as exc_info:
            processor._call_eval_start_api("pre", "test-bot-id", "test-set-uuid", "v1.0")

        assert "missing 'data' field" in str(exc_info.value)

    def test_call_eval_start_api_missing_set_task_uuid_raises_error(
        self, processor, mock_masa_eval_http
    ):
        """_call_eval_start_api() raises error if set_task_uuid is missing."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"success": true, "data": {"other_field": "value"}}'
        mock_response.json.return_value = {
            "success": True,
            "data": {"other_field": "value"},
        }
        mock_masa_eval_http.post.return_value = mock_response

        with pytest.raises(ValueError) as exc_info:
            processor._call_eval_start_api("pre", "test-bot-id", "test-set-uuid", "v1.0")

        assert "missing set_task_uuid" in str(exc_info.value)

    # ── _call_eval_progress_api() tests ───────────────────────────────────────

    def test_call_eval_progress_api_success(self, processor, mock_masa_eval_http):
        """_call_eval_progress_api() returns data on success."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"success": true, "data": {"set_task_uuid": "test-task-uuid", "status": "completed"}}'
        mock_response.json.return_value = {
            "success": True,
            "data": {
                "set_task_uuid": "test-task-uuid",
                "status": "completed",
            },
        }
        mock_masa_eval_http.get.return_value = mock_response

        result = processor._call_eval_progress_api("test-task-uuid")

        assert result["status"] == "completed"
        mock_masa_eval_http.get.assert_called_once_with(
            "/eval/progress?set_task_uuid=test-task-uuid"
        )

    def test_call_eval_progress_api_missing_data_raises_error(
        self, processor, mock_masa_eval_http
    ):
        """_call_eval_progress_api() raises error if data field is missing."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"success": true, "data": null}'
        mock_response.json.return_value = {
            "success": True,
            "data": None,
        }
        mock_masa_eval_http.get.return_value = mock_response

        with pytest.raises(ValueError) as exc_info:
            processor._call_eval_progress_api("test-task-uuid")

        assert "missing 'data' field" in str(exc_info.value)

    # ── update_status failure handling ────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_update_status_returns_none_raises_error(
        self, processor, mock_repo, mock_publish_flow_service
    ):
        """_transition_to raises ValueError if update_status returns None."""
        mock_repo.get_by_id.return_value = make_task(
            id=1,
            status="init",
            uuid="task-uuid-123",
            ext={"publish_id": "100"},
        )
        mock_repo.update_status.return_value = None

        with pytest.raises(ValueError) as exc_info:
            await processor.to_env_preparing(1)

        assert "Failed to update task: 1" in str(exc_info.value)

    # ── _transition_to (with ext) tests ────────────────────────────────────────

    def test_transition_to_with_ext_success(self, processor, mock_repo):
        """_transition_to updates status with ext data."""
        mock_repo.get_by_id.return_value = make_task(id=1, status="task_created")
        mock_repo.update_status.return_value = make_task(
            id=1,
            status="task_executed",
            ext={"set_task_uuid": "test-uuid", "eval_progress": {"status": "completed"}},
        )

        ext = {"set_task_uuid": "test-uuid", "eval_progress": {"status": "completed"}}
        result = processor._transition_to(
            1, "task_created", "task_executed", ext
        )

        assert result.status == "task_executed"
        call_args = mock_repo.update_status.call_args
        assert call_args[0][0] == 1
        assert call_args[0][1] == "task_executed"
        assert call_args[0][2]["source_status"] == "task_created"

    def test_transition_to_with_ext_task_not_found(self, processor, mock_repo):
        """_transition_to raises error if task not found."""
        mock_repo.get_by_id.return_value = None

        with pytest.raises(ValueError) as exc_info:
            processor._transition_to(1, "task_created", "task_executed", {})

        assert "Task not found: 1" in str(exc_info.value)

    def test_transition_to_with_ext_wrong_status(self, processor, mock_repo):
        """_transition_to raises error if status doesn't match."""
        mock_repo.get_by_id.return_value = make_task(id=1, status="success")

        with pytest.raises(ValueError) as exc_info:
            processor._transition_to(1, "task_created", "task_executed", {})

        assert "Expected status 'task_created'" in str(exc_info.value)

    def test_transition_to_with_ext_update_returns_none(self, processor, mock_repo):
        """_transition_to raises error if update_status returns None."""
        mock_repo.get_by_id.return_value = make_task(id=1, status="task_created")
        mock_repo.update_status.return_value = None

        with pytest.raises(ValueError) as exc_info:
            processor._transition_to(1, "task_created", "task_executed", {})

        assert "Failed to update task: 1" in str(exc_info.value)

    # ── Additional exception coverage tests ────────────────────────────────────────

    def test_to_task_created_task_not_found(self, processor, mock_repo):
        """to_task_created() raises error if task not found."""
        mock_repo.get_by_id.return_value = None

        with pytest.raises(ValueError) as exc_info:
            processor.to_task_created(1)

        assert "Task not found: 1" in str(exc_info.value)

    def test_to_task_created_update_returns_none(self, processor, mock_repo, mock_masa_eval_http):
        """to_task_created() raises error if update_status returns None."""
        mock_repo.get_by_id.return_value = make_task(
            id=1,
            status="env_ready",
            bot_id="test-bot-id",
            ext={"set_task_uuid": "existing-task-uuid"},
        )
        mock_repo.update_status.return_value = None

        with pytest.raises(ValueError) as exc_info:
            processor.to_task_created(1)

        assert "Failed to update task: 1" in str(exc_info.value)

    def test_to_task_executed_task_not_found(self, processor, mock_repo, mock_masa_eval_http):
        """to_task_executed() raises error if task not found."""
        mock_repo.get_by_id.return_value = None

        with pytest.raises(ValueError) as exc_info:
            processor.to_task_executed(1)

        assert "Task not found: 1" in str(exc_info.value)

    def test_transition_to_success_without_ext(self, processor, mock_repo):
        """_transition_to updates status without ext data (ext=None)."""
        mock_repo.get_by_id.return_value = make_task(id=1, status="env_preparing")
        mock_repo.update_status.return_value = make_task(id=1, status="env_ready")

        result = processor._transition_to(1, "env_preparing", "env_ready")

        assert result.status == "env_ready"
        call_args = mock_repo.update_status.call_args
        assert call_args[0][0] == 1
        assert call_args[0][1] == "env_ready"
        # ext should only contain source_status when ext parameter is None
        assert call_args[0][2] == {"source_status": "env_preparing"}

    def test_transition_to_task_not_found(self, processor, mock_repo):
        """_transition_to raises error if task not found."""
        mock_repo.get_by_id.return_value = None

        with pytest.raises(ValueError) as exc_info:
            processor._transition_to(1, "init", "env_preparing")

        assert "Task not found: 1" in str(exc_info.value)
