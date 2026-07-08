"""Unit tests for TaskMessageDispatcher."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.api.bot_runtime import BotBindingInfo, BotChatContext
from secbaas.core.service.bot_run._task_concurrency_pool import TaskConcurrencyPool
from secbaas.core.service.bot_run._task_message_dispatcher import (
    TaskMessageDispatcher,
)

BOT_ID = "test-bot-000001"
ENTITY_ID = "test-entity-001"


@pytest.fixture
def mock_bot_service():
    svc = MagicMock()
    svc.create_session = AsyncMock(
        return_value=MagicMock(session_id="agent:main:sess-001")
    )
    svc.send_message = AsyncMock(return_value=MagicMock(content="reply", usage={}))
    svc.inject_message = AsyncMock()
    return svc


@pytest.fixture
def mock_run_repo():
    repo = MagicMock()
    repo.insert_run = MagicMock()
    repo.update_status = MagicMock()
    repo.update_result = MagicMock()
    repo.update_error = MagicMock()
    repo.get_by_run_id = MagicMock(return_value=None)
    return repo


@pytest.fixture
def arca_binding():
    return BotBindingInfo(
        bot_id=BOT_ID,
        entity_id=ENTITY_ID,
        sandbox_id="ARCA-SANDBOX-abc@0",
        device_id="staff_bot_123",
        device_provider="arca",
        binding_id=100101,
        bot_type="personal",
    )


@pytest.fixture
def context():
    return BotChatContext(
        api_key_prefix="key-abc",
        app_id="owner123",
        app_type="baas",
        iam_token=None,
        tenant="test-tenant",
    )


class TestTaskMessageDispatcherSend:
    @pytest.mark.asyncio
    async def test_dispatch_send_creates_background_task(
        self, mock_bot_service, mock_run_repo, arca_binding, context
    ):
        dispatcher = TaskMessageDispatcher(run_repository=mock_run_repo, task_pool=None)
        await dispatcher.dispatch_send(
            bot_service=mock_bot_service,
            run_id="run-001",
            session_id="sess-001",
            message="hello",
            binding_info=arca_binding,
            context=context,
        )
        await asyncio.sleep(0)

        mock_bot_service.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_send_updates_status_running(
        self, mock_bot_service, mock_run_repo, arca_binding, context
    ):
        dispatcher = TaskMessageDispatcher(run_repository=mock_run_repo, task_pool=None)
        await dispatcher.dispatch_send(
            bot_service=mock_bot_service,
            run_id="run-001",
            session_id="sess-001",
            message="hello",
            binding_info=arca_binding,
            context=context,
        )
        await asyncio.sleep(0)

        mock_run_repo.update_status.assert_called_once_with("run-001", "RUNNING")

    @pytest.mark.asyncio
    async def test_dispatch_send_updates_result_on_success(
        self, mock_bot_service, mock_run_repo, arca_binding, context
    ):
        dispatcher = TaskMessageDispatcher(run_repository=mock_run_repo, task_pool=None)
        await dispatcher.dispatch_send(
            bot_service=mock_bot_service,
            run_id="run-001",
            session_id="sess-001",
            message="hello",
            binding_info=arca_binding,
            context=context,
        )
        await asyncio.sleep(0)

        mock_run_repo.update_result.assert_called_once()
        call_kw = mock_run_repo.update_result.call_args.kwargs
        assert call_kw["run_id"] == "run-001"
        assert call_kw["content_long"] == "reply"
        assert call_kw["extra"]["session_id"] == "sess-001"

    @pytest.mark.asyncio
    async def test_dispatch_send_updates_error_on_failure(
        self, mock_bot_service, mock_run_repo, arca_binding, context
    ):
        mock_bot_service.send_message.side_effect = ValueError("send failed")
        dispatcher = TaskMessageDispatcher(run_repository=mock_run_repo, task_pool=None)
        await dispatcher.dispatch_send(
            bot_service=mock_bot_service,
            run_id="run-001",
            session_id="sess-001",
            message="hello",
            binding_info=arca_binding,
            context=context,
        )
        await asyncio.sleep(0)

        mock_run_repo.update_error.assert_called_once()
        call_kw = mock_run_repo.update_error.call_args.kwargs
        assert call_kw["run_id"] == "run-001"
        assert "send failed" in call_kw["error"]

    @pytest.mark.asyncio
    async def test_dispatch_send_wait_result_false(
        self, mock_bot_service, mock_run_repo, arca_binding, context
    ):
        dispatcher = TaskMessageDispatcher(run_repository=mock_run_repo, task_pool=None)
        await dispatcher.dispatch_send(
            bot_service=mock_bot_service,
            run_id="run-001",
            session_id="sess-001",
            message="hello",
            binding_info=arca_binding,
            context=context,
            wait_result=False,
        )
        await asyncio.sleep(0)

        mock_bot_service.send_message.assert_called_once()
        call_kw = mock_bot_service.send_message.call_args.kwargs
        assert call_kw["wait_result"] is False

        # extra should have ignore_result=true
        result_kw = mock_run_repo.update_result.call_args.kwargs
        assert result_kw["extra"]["ignore_result"] == "true"

    @pytest.mark.asyncio
    async def test_dispatch_send_with_callback(
        self, mock_bot_service, mock_run_repo, arca_binding, context
    ):
        callback_called = asyncio.Event()

        class _StubCallback:
            async def __call__(self, run_id: str) -> None:
                callback_called.set()

        dispatcher = TaskMessageDispatcher(
            run_repository=mock_run_repo,
            task_pool=None,
            post_run_callback_factories={"bcn_uplink": _StubCallback()},
        )
        await dispatcher.dispatch_send(
            bot_service=mock_bot_service,
            run_id="run-001",
            session_id="sess-001",
            message="hello",
            binding_info=arca_binding,
            context=context,
            callback="bcn_uplink",
        )
        await asyncio.sleep(0.1)

        assert callback_called.is_set()


class TestTaskMessageDispatcherInject:
    @pytest.mark.asyncio
    async def test_dispatch_inject_creates_background_task(
        self, mock_bot_service, mock_run_repo, arca_binding, context
    ):
        dispatcher = TaskMessageDispatcher(run_repository=mock_run_repo, task_pool=None)
        await dispatcher.dispatch_inject(
            bot_service=mock_bot_service,
            run_id="run-001",
            session_id="sess-001",
            message="system instruction",
            binding_info=arca_binding,
            context=context,
        )
        await asyncio.sleep(0)

        mock_bot_service.inject_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_inject_updates_result_on_success(
        self, mock_bot_service, mock_run_repo, arca_binding, context
    ):
        dispatcher = TaskMessageDispatcher(run_repository=mock_run_repo, task_pool=None)
        await dispatcher.dispatch_inject(
            bot_service=mock_bot_service,
            run_id="run-001",
            session_id="sess-001",
            message="system instruction",
            binding_info=arca_binding,
            context=context,
        )
        await asyncio.sleep(0)

        mock_run_repo.update_result.assert_called_once()
        call_kw = mock_run_repo.update_result.call_args.kwargs
        assert call_kw["content_long"] == ""
        assert call_kw["extra"]["injected"] == "true"


class TestTaskMessageDispatcherWithPool:
    @pytest.mark.asyncio
    async def test_acquires_slot_and_releases(
        self, mock_bot_service, mock_run_repo, arca_binding, context
    ):
        pool = TaskConcurrencyPool(softmax=1, per_key_max=0)
        mock_bot_service.send_message = AsyncMock(
            return_value=MagicMock(content="reply", usage={})
        )

        dispatcher = TaskMessageDispatcher(run_repository=mock_run_repo, task_pool=pool)
        await dispatcher.dispatch_send(
            bot_service=mock_bot_service,
            run_id="run-001",
            session_id="sess-001",
            message="hello",
            binding_info=arca_binding,
            context=context,
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
        )
        await asyncio.sleep(0.2)
        assert pool.active_count == 0

    @pytest.mark.asyncio
    async def test_slot_released_on_error(
        self, mock_bot_service, mock_run_repo, arca_binding, context
    ):
        pool = TaskConcurrencyPool(softmax=1, per_key_max=0)
        mock_bot_service.send_message.side_effect = ValueError("error")

        dispatcher = TaskMessageDispatcher(run_repository=mock_run_repo, task_pool=pool)
        await dispatcher.dispatch_send(
            bot_service=mock_bot_service,
            run_id="run-001",
            session_id="sess-001",
            message="hello",
            binding_info=arca_binding,
            context=context,
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
        )
        await asyncio.sleep(0.2)
        assert pool.active_count == 0
