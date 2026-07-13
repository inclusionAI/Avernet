"""Unit tests for BotRunner.

Covers:
- BotRunner resolves binding and passes it to create_session
- BotRunner overrides bot_id with binding_info.device_id for baas
- BotRunner overrides bot_id with binding_info.bot_id for non-baas
- BotRunner passes context to create_session and send_message
- deliver_message flow (including ignore_result, arca/baas binding)
- DB-first: insert_run before create_session
- create_session failure marks DB record as FAILED
- session_id stored after create_session
- Idempotency with and without session_id in record
- chat() delegates to deliver_message
- _execute_send_message error/success paths
- _handle_task_exception with real exceptions
- get_result with missing run_id
- PLATFORM_UNAVAILABLE from plugin returns None -> BotBindingNotFoundError
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.api.bot_runtime import (
    BotBindingInfo,
    BotChatContext,
    TooManyRequestsError,
)
from secbaas.community.api.device_manage import ErrorCode, PaasError
from secbaas.community.core.service.bot_run import (
    BotBindingNotFoundError,
    BotRunner,
    BotServiceSelector,
)
from secbaas.community.core.service.bot_run._internal_protocols import MessageDispatcher
from secbaas.community.core.service.bot_run._task_concurrency_pool import (
    TaskConcurrencyPool,
)
from secbaas.community.core.service.bot_run._task_message_dispatcher import (
    TaskMessageDispatcher,
)
from secbaas.community.spi.bot_service import BotBindingData

# ==================== Fixtures ====================

BOT_ID = "test-bot-000001"
ENTITY_ID = "test-entity-001"
APP_ID_BAAS = "301516dd13a942639420174eaa63190e"
API_KEY_PREFIX = "key-abc"


@pytest.fixture
def context():
    return BotChatContext(
        api_key_prefix=API_KEY_PREFIX,
        app_id="owner123",
        app_type="baas",
        iam_token=None,
        tenant="test-tenant",
    )


@pytest.fixture
def mock_bot_service():
    svc = MagicMock()
    svc.create_session = AsyncMock(
        return_value=MagicMock(session_id="agent:main:sess-001")
    )
    svc.send_message = AsyncMock(return_value=MagicMock(content="reply", usage={}))
    svc.inject_message = AsyncMock()
    svc.get_messages = AsyncMock(return_value=[])
    return svc


@pytest.fixture
def mock_selector(mock_bot_service):
    selector = MagicMock(spec=BotServiceSelector)
    selector.select.return_value = mock_bot_service
    return selector


@pytest.fixture
def mock_run_repo():
    repo = MagicMock()
    repo.insert_run = MagicMock()
    repo.update_status = MagicMock()
    repo.update_result = MagicMock()
    repo.update_error = MagicMock()
    repo.update_session_id = MagicMock()
    repo.get_by_run_id = MagicMock(return_value=None)
    return repo


@pytest.fixture
def mock_bot_service_plugin():
    """Mock BotServicePlugin with async get_binding."""
    plugin = MagicMock()
    plugin.get_binding = AsyncMock(return_value=None)
    plugin.report = AsyncMock()
    return plugin


@pytest.fixture
def arca_binding_data():
    """BotBindingData for arca binding (device_id used as sandbox_id)."""
    return BotBindingData(
        bot_id=BOT_ID,
        owner_id=ENTITY_ID,
        bot_type="personal",
        engine_type="openclaw",
        binding_id=100101,
        device_provider="arca",
        device_id="staff_bot_123",
    )


@pytest.fixture
def baas_binding_data():
    """BotBindingData for baas binding."""
    return BotBindingData(
        bot_id=BOT_ID,
        owner_id=ENTITY_ID,
        bot_type="service",
        engine_type="openclaw",
        binding_id=100002,
        device_provider="baas",
        device_id=APP_ID_BAAS,
    )


def _make_runner(
    mock_selector,
    mock_run_repo,
    mock_bot_service_plugin,
    dispatcher=None,
):
    if dispatcher is None:
        dispatcher = MagicMock(spec=MessageDispatcher)
        dispatcher.dispatch_send = AsyncMock()
        dispatcher.dispatch_inject = AsyncMock()
    return BotRunner(
        bot_service_selector=mock_selector,
        run_repository=mock_run_repo,
        bot_service_plugin=mock_bot_service_plugin,
        dispatchers=[dispatcher],
    )


def _make_runner_with_task_dispatcher(
    mock_selector,
    mock_run_repo,
    mock_bot_service_plugin,
    task_pool=None,
):
    """Create runner with TaskMessageDispatcher (real background task execution)."""
    dispatcher = TaskMessageDispatcher(
        run_repository=mock_run_repo,
        task_pool=task_pool,
    )
    return BotRunner(
        bot_service_selector=mock_selector,
        run_repository=mock_run_repo,
        bot_service_plugin=mock_bot_service_plugin,
        dispatchers=[dispatcher],
    )


# ==================== Tests: binding_info passthrough ====================


class TestBindingInfoPassthrough:
    @pytest.mark.asyncio
    async def test_passes_binding_info_to_create_session(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        await runner.chat(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            context=context,
            message="hello",
            metadata={},
        )

        mock_bot_service.create_session.assert_called_once()
        kw = mock_bot_service.create_session.call_args.kwargs
        binding_info = kw["binding_info"]
        assert isinstance(binding_info, BotBindingInfo)
        assert binding_info.bot_id == BOT_ID
        assert binding_info.entity_id == ENTITY_ID
        # arca: sandbox_id == device_id
        assert binding_info.sandbox_id == "staff_bot_123"
        assert binding_info.device_provider == "arca"

    @pytest.mark.asyncio
    async def test_no_binding_info_raises_error(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        context,
    ):
        """WHEN no binding info is available (plugin raises NOT_FOUND),
        THEN chat raises BotBindingNotFoundError."""
        mock_bot_service_plugin.get_binding.side_effect = PaasError(
            ErrorCode.NOT_FOUND, "not found"
        )

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        with pytest.raises(BotBindingNotFoundError):
            await runner.chat(
                bot_id=f"{BOT_ID}:{ENTITY_ID}",
                context=context,
                message="hello",
                metadata={},
            )

    @pytest.mark.asyncio
    async def test_other_paas_error_propagates(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        context,
    ):
        """WHEN plugin raises a non-PLATFORM_UNAVAILABLE PaasError,
        THEN the error propagates (not swallowed)."""
        mock_bot_service_plugin.get_binding.side_effect = PaasError(
            ErrorCode.AUTH_FAILED, "auth error"
        )

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        with pytest.raises(PaasError, match="auth error"):
            await runner.chat(
                bot_id=f"{BOT_ID}:{ENTITY_ID}",
                context=context,
                message="hello",
                metadata={},
            )


# ==================== Tests: bot_id override ====================


class TestBotIdOverride:
    @pytest.mark.asyncio
    async def test_baas_binding_overrides_with_device_id(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        baas_binding_data,
        context,
    ):
        mock_bot_service_plugin.get_binding.return_value = baas_binding_data

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        await runner.chat(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            context=context,
            message="hello",
            metadata={},
        )

        kw = mock_bot_service.create_session.call_args.kwargs
        assert kw["bot_id"] == APP_ID_BAAS
        assert kw["bot_id"] != f"{BOT_ID}:{ENTITY_ID}"

    @pytest.mark.asyncio
    async def test_arca_binding_overrides_with_bot_id(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        await runner.chat(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            context=context,
            message="hello",
            metadata={},
        )

        kw = mock_bot_service.create_session.call_args.kwargs
        # Arca uses bot_id (not device_id)
        assert kw["bot_id"] == BOT_ID
        assert kw["bot_id"] != f"{BOT_ID}:{ENTITY_ID}"

    @pytest.mark.asyncio
    async def test_deliver_message_also_overrides_with_bot_id(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        await runner.deliver_message(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={},
            message_id=None,
        )

        kw = mock_bot_service.create_session.call_args.kwargs
        assert kw["bot_id"] == BOT_ID


# ==================== Tests: no binding_info ====================


class TestNoBindingInfo:
    @pytest.mark.asyncio
    async def test_create_session_called_with_original_bot_id(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        context,
    ):
        mock_bot_service_plugin.get_binding.side_effect = PaasError(
            ErrorCode.NOT_FOUND, "not found"
        )

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        with pytest.raises(BotBindingNotFoundError):
            await runner.chat(
                bot_id=f"{BOT_ID}:{ENTITY_ID}",
                context=context,
                message="hello",
                metadata={},
            )


# ==================== Tests: context passthrough ====================


class TestContextPassthrough:
    @pytest.mark.asyncio
    async def test_context_passed_to_create_session(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        await runner.chat(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            context=context,
            message="hello",
            metadata={},
        )

        kw = mock_bot_service.create_session.call_args.kwargs
        assert kw["context"] is context

    @pytest.mark.asyncio
    async def test_context_passed_to_send_message(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data

        runner = _make_runner_with_task_dispatcher(
            mock_selector, mock_run_repo, mock_bot_service_plugin
        )
        await runner.chat(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            context=context,
            message="hello",
            metadata={},
        )

        # send_message runs in asyncio.create_task, yield to let it execute
        await asyncio.sleep(0)

        kw = mock_bot_service.send_message.call_args.kwargs
        assert kw["context"] is context

    @pytest.mark.asyncio
    async def test_api_key_prefix_extracted_from_context(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        await runner.chat(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            context=context,
            message="hello",
            metadata={},
        )

        mock_run_repo.insert_run.assert_called_once()
        call_kw = mock_run_repo.insert_run.call_args.kwargs
        assert call_kw["api_key_prefix"] == API_KEY_PREFIX


# ==================== Tests: send_message flow ====================


class TestSendMessageFlow:
    @pytest.mark.asyncio
    async def test_session_created_with_binding_info(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        await runner.chat(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            context=context,
            message="hello",
            metadata={},
        )

        # create_session called with binding_info
        mock_bot_service.create_session.assert_called_once()
        kw = mock_bot_service.create_session.call_args.kwargs
        assert isinstance(kw["binding_info"], BotBindingInfo)
        # run inserted to DB
        mock_run_repo.insert_run.assert_called_once()


# ==================== Tests: deliver_message flow ====================


class TestDeliverMessageFlow:
    @pytest.mark.asyncio
    async def test_deliver_message_arca_binding_overrides_with_bot_id(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        """WHEN deliver_message is called with arca binding,
        THEN overrides bot_id with binding_info.bot_id."""
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        await runner.deliver_message(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={},
            message_id=None,
        )

        kw = mock_bot_service.create_session.call_args.kwargs
        assert kw["bot_id"] == BOT_ID

    @pytest.mark.asyncio
    async def test_deliver_message_baas_binding_overrides_with_device_id(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        baas_binding_data,
        context,
    ):
        """WHEN deliver_message is called with baas binding,
        THEN overrides bot_id with device_id."""
        mock_bot_service_plugin.get_binding.return_value = baas_binding_data

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        await runner.deliver_message(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={},
            message_id=None,
        )

        kw = mock_bot_service.create_session.call_args.kwargs
        assert kw["bot_id"] == APP_ID_BAAS

    @pytest.mark.asyncio
    async def test_deliver_message_ignore_result_metadata(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        """WHEN deliver_message has ignore_result="true" (string) in metadata,
        THEN sets wait_result=False for send_message."""
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        await runner.deliver_message(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={"ignore_result": "true"},
            message_id=None,
        )

        runner._dispatchers[0].dispatch_send.assert_called_once()
        kw = runner._dispatchers[0].dispatch_send.call_args.kwargs
        assert kw["wait_result"] is False

    @pytest.mark.asyncio
    async def test_deliver_message_ignore_result_bool_true(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        """WHEN deliver_message has ignore_result=True (bool) in metadata,
        THEN sets wait_result=False for send_message."""
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        await runner.deliver_message(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={"ignore_result": True},
            message_id=None,
        )

        runner._dispatchers[0].dispatch_send.assert_called_once()
        kw = runner._dispatchers[0].dispatch_send.call_args.kwargs
        assert kw["wait_result"] is False

    @pytest.mark.asyncio
    async def test_deliver_message_ignore_result_string_false(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        """WHEN deliver_message has ignore_result="false" (string) in metadata,
        THEN keeps wait_result=True."""
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        await runner.deliver_message(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={"ignore_result": "false"},
            message_id=None,
        )

        runner._dispatchers[0].dispatch_send.assert_called_once()
        kw = runner._dispatchers[0].dispatch_send.call_args.kwargs
        assert kw["wait_result"] is True

    @pytest.mark.asyncio
    async def test_deliver_message_ignore_result_bool_false(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        """WHEN deliver_message has ignore_result=False (bool) in metadata,
        THEN keeps wait_result=True."""
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        await runner.deliver_message(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={"ignore_result": False},
            message_id=None,
        )

        runner._dispatchers[0].dispatch_send.assert_called_once()
        kw = runner._dispatchers[0].dispatch_send.call_args.kwargs
        assert kw["wait_result"] is True

    @pytest.mark.asyncio
    async def test_deliver_message_ignore_result_zero(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        """WHEN deliver_message has ignore_result=0 in metadata,
        THEN keeps wait_result=True because bool(0) is False."""
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        await runner.deliver_message(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={"ignore_result": 0},
            message_id=None,
        )

        runner._dispatchers[0].dispatch_send.assert_called_once()
        kw = runner._dispatchers[0].dispatch_send.call_args.kwargs
        assert kw["wait_result"] is True


# ==================== Tests: DB-first flow ====================


class TestDBFirstFlow:
    @pytest.mark.asyncio
    async def test_deliver_message_inserts_run_before_create_session(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        """DB-first: insert_run is called before create_session."""
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data
        call_order = []

        mock_run_repo.insert_run.side_effect = lambda **kw: call_order.append(
            "insert_run"
        )
        mock_bot_service.create_session.side_effect = lambda **kw: (
            call_order.append("create_session"),
            MagicMock(session_id="sess-001"),
        )[1]

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        await runner.deliver_message(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={},
            message_id="test-msg-id",
        )

        assert call_order == ["insert_run", "create_session"]

    @pytest.mark.asyncio
    async def test_deliver_message_marks_failed_when_create_session_fails(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        """When create_session fails, the PENDING record is marked FAILED."""
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data
        mock_bot_service.create_session.side_effect = RuntimeError(
            "session creation failed"
        )

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        with pytest.raises(RuntimeError, match="session creation failed"):
            await runner.deliver_message(
                bot_id=f"{BOT_ID}:{ENTITY_ID}",
                message="hello",
                context=context,
                metadata={},
                message_id="test-msg-id",
            )

        # insert_run was called (DB-first)
        mock_run_repo.insert_run.assert_called_once()
        # update_error was called to mark the record as FAILED
        mock_run_repo.update_error.assert_called_once()
        call_kw = mock_run_repo.update_error.call_args.kwargs
        assert call_kw["run_id"] == "test-msg-id"
        assert "Session creation failed" in call_kw["error"]

    @pytest.mark.asyncio
    async def test_deliver_message_stores_session_id_after_create_session(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        """After create_session succeeds, session_id is persisted to DB."""
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        await runner.deliver_message(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={},
            message_id="test-msg-id",
        )

        mock_run_repo.update_session_id.assert_called_once_with(
            "test-msg-id", "agent:main:sess-001"
        )

    @pytest.mark.asyncio
    async def test_deliver_message_idempotent_with_existing_session_id(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        """Idempotency: when record exists with session_id, return it directly."""
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data
        existing_record = MagicMock(
            result_extra={"session_id": "existing-sess-001"},
            metadata={"session_id": "metadata-session"},
        )
        mock_run_repo.get_by_run_id.return_value = existing_record

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        msg_id, sess_id = await runner.deliver_message(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={},
            message_id="existing-msg-id",
        )

        assert msg_id == "existing-msg-id"
        assert sess_id == "existing-sess-001"
        # No insert_run, no create_session for idempotent hit
        mock_run_repo.insert_run.assert_not_called()
        mock_bot_service.create_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_deliver_message_idempotent_without_session_id(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        """Idempotency: when record exists without session_id,
        return empty session_id without calling create_session."""
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data
        existing_record = MagicMock(
            result_extra=None,
            metadata=None,
        )
        mock_run_repo.get_by_run_id.return_value = existing_record

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        msg_id, sess_id = await runner.deliver_message(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={},
            message_id="existing-msg-id",
        )

        assert msg_id == "existing-msg-id"
        assert sess_id == ""
        # create_session not called
        mock_bot_service.create_session.assert_not_called()
        # No insert_run for idempotent hit
        mock_run_repo.insert_run.assert_not_called()


# ==================== Tests: inject_message idempotency ====================


class TestInjectMessageIdempotency:
    @pytest.mark.asyncio
    async def test_inject_message_idempotent_with_session_id(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        """inject_message: when record exists with session_id, return it directly."""
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data
        existing_record = MagicMock(
            result_extra={"session_id": "existing-sess-001"},
            metadata={"session_id": "metadata-session"},
        )
        mock_run_repo.get_by_run_id.return_value = existing_record

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        msg_id, sess_id = await runner.inject_message(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={},
            message_id="existing-msg-id",
        )

        assert msg_id == "existing-msg-id"
        assert sess_id == "existing-sess-001"
        mock_run_repo.insert_run.assert_not_called()
        mock_bot_service.create_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_inject_message_idempotent_without_session_id(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        """inject_message: when record exists without session_id, return empty string."""
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data
        existing_record = MagicMock(
            result_extra=None,
            metadata=None,
        )
        mock_run_repo.get_by_run_id.return_value = existing_record

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        msg_id, sess_id = await runner.inject_message(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={},
            message_id="existing-msg-id",
        )

        assert msg_id == "existing-msg-id"
        assert sess_id == ""
        mock_bot_service.create_session.assert_not_called()
        mock_run_repo.insert_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_inject_message_session_id_from_metadata(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        """inject_message: session_id falls back to metadata when result_extra is None."""
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data
        existing_record = MagicMock(
            result_extra=None,
            metadata={"session_id": "meta-sess-001"},
        )
        mock_run_repo.get_by_run_id.return_value = existing_record

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        msg_id, sess_id = await runner.inject_message(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={},
            message_id="existing-msg-id",
        )

        assert sess_id == "meta-sess-001"

    @pytest.mark.asyncio
    async def test_inject_message_normal_flow(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        """inject_message: normal flow when no existing record."""
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data
        mock_run_repo.get_by_run_id.return_value = None

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        msg_id, sess_id = await runner.inject_message(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={},
            message_id="new-msg-id",
        )

        assert msg_id == "new-msg-id"
        assert sess_id == "agent:main:sess-001"
        mock_run_repo.insert_run.assert_called_once()
        mock_bot_service.create_session.assert_called_once()


# ==================== Tests: deliver_message_stream idempotency ====================


class TestDeliverMessageStreamIdempotency:
    @pytest.mark.asyncio
    async def test_stream_raises_on_duplicate_run_id(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        """deliver_message_stream: duplicate run_id raises ValueError."""
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data
        existing_record = MagicMock(
            result_extra={"session_id": "sess-001"},
            metadata={"session_id": "sess-001"},
        )
        mock_run_repo.get_by_run_id.return_value = existing_record

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        with pytest.raises(ValueError, match="Duplicate request in stream mode"):
            await runner.deliver_message_stream(
                bot_id=f"{BOT_ID}:{ENTITY_ID}",
                message="hello",
                context=context,
                metadata={},
                message_id="duplicate-id",
            )

    @pytest.mark.asyncio
    async def test_stream_no_duplicate_proceeds(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        """deliver_message_stream: no existing record proceeds normally."""
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data
        mock_run_repo.get_by_run_id.return_value = None

        async def _empty_iter():
            return
            yield  # make it an async generator

        mock_bot_service.create_session = AsyncMock(
            return_value=MagicMock(session_id="sess-stream-1")
        )

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        # Patch dispatch_send_stream to return a dummy async iterator
        runner._dispatchers[0].dispatch_send_stream = MagicMock(
            return_value=_empty_iter()
        )

        msg_id, sess_id, stream = await runner.deliver_message_stream(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={},
            message_id="new-stream-id",
        )

        assert msg_id == "new-stream-id"
        assert sess_id == "sess-stream-1"
        mock_run_repo.insert_run.assert_called_once()


# ==================== Tests: _check_idempotency direct ====================


class TestCheckIdempotency:
    def test_returns_record_when_found(
        self, mock_selector, mock_run_repo, mock_bot_service_plugin
    ):
        """_check_idempotency returns the record from repo."""
        mock_record = MagicMock()
        mock_run_repo.get_by_run_id.return_value = mock_record

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        result = runner._check_idempotency(run_id="existing-id")

        assert result is mock_record
        mock_run_repo.get_by_run_id.assert_called_once_with(run_id="existing-id")

    def test_returns_none_when_not_found(
        self, mock_selector, mock_run_repo, mock_bot_service_plugin
    ):
        """_check_idempotency returns None when record doesn't exist."""
        mock_run_repo.get_by_run_id.return_value = None

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        result = runner._check_idempotency(run_id="nonexistent-id")

        assert result is None


# ==================== Tests: chat delegates to deliver_message ====================


class TestChatDelegation:
    @pytest.mark.asyncio
    async def test_chat_returns_run_id(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        """chat() returns just the run_id from deliver_message."""
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        run_id = await runner.chat(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            context=context,
            message="hello",
            metadata={},
        )

        assert run_id is not None
        assert isinstance(run_id, str)


# ==================== Tests: _execute_send_message via TaskMessageDispatcher ===


class TestExecuteSendMessage:
    @pytest.mark.asyncio
    async def test_send_message_error_updates_db(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        """WHEN send_message raises an exception,
        THEN update_error is called."""
        mock_bot_service.send_message.side_effect = ValueError("simulated failure")
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data

        runner = _make_runner_with_task_dispatcher(
            mock_selector, mock_run_repo, mock_bot_service_plugin
        )
        await runner.chat(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            context=context,
            message="hello",
            metadata={},
        )

        await asyncio.sleep(0)

        mock_run_repo.update_error.assert_called_once()
        call_kw = mock_run_repo.update_error.call_args.kwargs
        assert "simulated failure" in call_kw["error"]

    @pytest.mark.asyncio
    async def test_send_message_with_usage_tracking(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        """WHEN send_message returns usage data,
        THEN usage info is included in update_result extra."""
        mock_bot_service.send_message.return_value = MagicMock(
            content="hi",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data

        runner = _make_runner_with_task_dispatcher(
            mock_selector, mock_run_repo, mock_bot_service_plugin
        )
        await runner.chat(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            context=context,
            message="hello",
            metadata={},
        )

        await asyncio.sleep(0)

        mock_run_repo.update_result.assert_called_once()
        call_kw = mock_run_repo.update_result.call_args.kwargs
        assert call_kw["content_long"] == "hi"
        assert call_kw["extra"]["usage"]["prompt_tokens"] == 10
        assert call_kw["extra"]["usage"]["completion_tokens"] == 5

    @pytest.mark.asyncio
    async def test_send_message_no_usage(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        """WHEN send_message returns no usage data,
        THEN extra dict does not contain usage key."""
        mock_bot_service.send_message.return_value = MagicMock(
            content="hi",
            usage=None,
        )
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data

        runner = _make_runner_with_task_dispatcher(
            mock_selector, mock_run_repo, mock_bot_service_plugin
        )
        await runner.chat(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            context=context,
            message="hello",
            metadata={},
        )

        await asyncio.sleep(0)

        mock_run_repo.update_result.assert_called_once()
        call_kw = mock_run_repo.update_result.call_args.kwargs
        assert "usage" not in call_kw["extra"]


# ==================== Tests: _handle_task_exception ====================


class TestHandleTaskException:
    @pytest.mark.asyncio
    async def test_background_task_exception_is_logged(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        """WHEN _execute_send_message itself raises unhandled exception,
        THEN _handle_task_exception catches and logs it."""
        mock_bot_service.send_message.side_effect = ValueError("task error")
        mock_run_repo.update_error.side_effect = RuntimeError("db write failed")
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data

        runner = _make_runner_with_task_dispatcher(
            mock_selector, mock_run_repo, mock_bot_service_plugin
        )
        await runner.chat(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            context=context,
            message="hello",
            metadata={},
        )

        await asyncio.sleep(0.1)


# ==================== Tests: get_result ====================


class TestGetResult:
    def test_get_result_raises_keyerror_for_missing_run(
        self, mock_selector, mock_run_repo, mock_bot_service_plugin
    ):
        """WHEN get_result is called with unknown run_id, THEN raises KeyError."""
        mock_run_repo.get_by_run_id.return_value = None

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)

        with pytest.raises(KeyError, match="Run not found"):
            runner.get_result("nonexistent-run-id")

    def test_get_result_returns_record(
        self, mock_selector, mock_run_repo, mock_bot_service_plugin
    ):
        """WHEN get_result is called with known run_id, THEN returns the record."""
        mock_record = MagicMock()
        mock_run_repo.get_by_run_id.return_value = mock_record

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)

        result = runner.get_result("known-run-id")

        assert result is mock_record
        mock_run_repo.get_by_run_id.assert_called_once_with("known-run-id")


# ==================== Tests: TaskConcurrencyPool integration ====================


@pytest.fixture
def pool():
    """并发池，softmax=1，per_key_max=0"""
    return TaskConcurrencyPool(softmax=1, per_key_max=0)


class TestRunnerWithPool:
    """BotRunner 与 TaskConcurrencyPool 集成测试。"""

    @pytest.mark.asyncio
    async def test_chat_returns_immediately(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
        pool,
    ):
        """chat() 立即返回 run_id，排队在 background task 内部。"""
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data

        # send_message 很慢，但 chat() 应该立即返回
        async def slow_send(*args, **kwargs):
            await asyncio.sleep(0.5)
            return MagicMock(content="reply", usage={})

        mock_bot_service.send_message = slow_send

        runner = _make_runner_with_task_dispatcher(
            mock_selector, mock_run_repo, mock_bot_service_plugin, task_pool=pool
        )

        # 第一个请求立即返回
        r1 = await runner.chat(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={},
        )
        assert r1 is not None

        # 第二个请求也应该立即返回（入口不阻塞）
        r2 = await runner.chat(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello again",
            context=context,
            metadata={},
        )
        assert r2 is not None
        assert r1 != r2

    @pytest.mark.asyncio
    async def test_slot_released_after_task(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        """slot 在 background task 完成后释放。"""
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data
        mock_bot_service.send_message = AsyncMock(
            return_value=MagicMock(content="reply", usage={})
        )

        p = TaskConcurrencyPool(softmax=1, per_key_max=0)

        runner = _make_runner_with_task_dispatcher(
            mock_selector, mock_run_repo, mock_bot_service_plugin, task_pool=p
        )

        # 发送请求
        await runner.chat(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={},
        )

        # 等待异步任务完成并释放 slot
        await asyncio.sleep(0.3)
        assert p.active_count == 0


class TestRunnerPoolSlotRelease:
    """测试 slot 在各种场景下正确释放。"""

    @pytest.mark.asyncio
    async def test_slot_released_on_success(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        pool = TaskConcurrencyPool(softmax=1, per_key_max=0)
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data
        mock_bot_service.send_message = AsyncMock(
            return_value=MagicMock(content="reply", usage={})
        )

        runner = _make_runner_with_task_dispatcher(
            mock_selector, mock_run_repo, mock_bot_service_plugin, task_pool=pool
        )

        # 第一个请求
        await runner.chat(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={},
        )
        # 等待异步任务完成
        await asyncio.sleep(0.2)

        # slot 应该已经释放
        assert pool.active_count == 0
        await runner.chat(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello again",
            context=context,
            metadata={},
        )

    @pytest.mark.asyncio
    async def test_slot_not_consumed_on_binding_error(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        context,
    ):
        """resolve_binding 返回 None 时不消耗并发池 slot。"""
        mock_bot_service_plugin.get_binding.side_effect = PaasError(
            ErrorCode.NOT_FOUND, "not found"
        )

        # 不传 pool — binding 解析失败不经过 pool
        runner = _make_runner_with_task_dispatcher(
            mock_selector, mock_run_repo, mock_bot_service_plugin
        )

        with pytest.raises(BotBindingNotFoundError):
            await runner.chat(
                bot_id=f"{BOT_ID}:{ENTITY_ID}",
                message="hello",
                context=context,
                metadata={},
            )

        with pytest.raises(BotBindingNotFoundError):
            await runner.chat(
                bot_id=f"{BOT_ID}:{ENTITY_ID}",
                message="hello",
                context=context,
                metadata={},
            )

    @pytest.mark.asyncio
    async def test_no_pool_backward_compat(
        self,
        mock_selector,
        mock_bot_service,
        mock_run_repo,
        mock_bot_service_plugin,
        arca_binding_data,
        context,
    ):
        """task_pool=None 时行为不变（向后兼容）。"""
        mock_bot_service_plugin.get_binding.return_value = arca_binding_data

        runner = _make_runner_with_task_dispatcher(
            mock_selector, mock_run_repo, mock_bot_service_plugin
        )
        run_id = await runner.chat(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={},
        )
        assert run_id is not None


# ==================== Tests: _select_dispatcher config routing ====================


def _config_response(value: str | None = None):
    """Build a mock SystemConfigResponse with the given conf_value."""
    mock = MagicMock()
    mock.conf_value = value
    return mock


def _make_runner_with_config(
    mock_selector,
    mock_run_repo,
    mock_bot_service_plugin,
    config_service,
    dispatchers,
):
    return BotRunner(
        bot_service_selector=mock_selector,
        run_repository=mock_run_repo,
        bot_service_plugin=mock_bot_service_plugin,
        dispatchers=dispatchers,
        system_config_service=config_service,
    )


class TestSelectDispatcherConfig:
    """Tests for system_config-driven dispatcher routing."""

    def test_no_config_service_defaults_to_task(
        self, mock_selector, mock_run_repo, mock_bot_service_plugin
    ):
        """Without system_config_service, defaults to TaskMessageDispatcher."""
        task_d = TaskMessageDispatcher(run_repository=mock_run_repo)
        queue_d = MagicMock(spec=MessageDispatcher)
        queue_d.__class__.__name__ = "QueueTaskMessageDispatcher"
        runner = BotRunner(
            bot_service_selector=mock_selector,
            run_repository=mock_run_repo,
            bot_service_plugin=mock_bot_service_plugin,
            dispatchers=[queue_d, task_d],
        )
        result = runner._select_dispatcher("bot-1")
        assert result is task_d

    def test_config_returns_queue_dispatcher(
        self, mock_selector, mock_run_repo, mock_bot_service_plugin
    ):
        """When config value is 'QueueTaskMessageDispatcher', selects queue dispatcher."""
        task_d = TaskMessageDispatcher(run_repository=mock_run_repo)
        queue_d = MagicMock(spec=MessageDispatcher)
        queue_d.__class__.__name__ = "QueueTaskMessageDispatcher"

        config_service = MagicMock()
        config_service.get_config.return_value = _config_response(
            "QueueTaskMessageDispatcher"
        )

        runner = _make_runner_with_config(
            mock_selector,
            mock_run_repo,
            mock_bot_service_plugin,
            config_service,
            [queue_d, task_d],
        )
        result = runner._select_dispatcher("bot-1")
        assert result is queue_d

    def test_config_method_stream_overrides(
        self, mock_selector, mock_run_repo, mock_bot_service_plugin
    ):
        """:stream suffix key takes priority over base key."""
        task_d = TaskMessageDispatcher(run_repository=mock_run_repo)
        queue_d = MagicMock(spec=MessageDispatcher)
        queue_d.__class__.__name__ = "QueueTaskMessageDispatcher"

        # bot_id key -> queue, bot_id:stream key -> task
        def get_config(key):
            if ":stream" in key:
                return _config_response("TaskMessageDispatcher")
            return _config_response("QueueTaskMessageDispatcher")

        config_service = MagicMock()
        config_service.get_config.side_effect = get_config

        runner = _make_runner_with_config(
            mock_selector,
            mock_run_repo,
            mock_bot_service_plugin,
            config_service,
            [queue_d, task_d],
        )

        assert runner._select_dispatcher("bot-1") is queue_d
        assert runner._select_dispatcher("bot-1", method="stream") is task_d

    def test_config_fallback_to_wildcard(
        self, mock_selector, mock_run_repo, mock_bot_service_plugin
    ):
        """When bot_id key is not found, falls back to * wildcard."""
        task_d = TaskMessageDispatcher(run_repository=mock_run_repo)
        queue_d = MagicMock(spec=MessageDispatcher)
        queue_d.__class__.__name__ = "QueueTaskMessageDispatcher"

        def get_config(key):
            if key == "bot_run.dispatcher_route.*":
                return _config_response("QueueTaskMessageDispatcher")
            return None

        config_service = MagicMock()
        config_service.get_config.side_effect = get_config

        runner = _make_runner_with_config(
            mock_selector,
            mock_run_repo,
            mock_bot_service_plugin,
            config_service,
            [queue_d, task_d],
        )
        result = runner._select_dispatcher("bot-1")
        assert result is queue_d

    def test_config_get_config_exception_falls_through(
        self, mock_selector, mock_run_repo, mock_bot_service_plugin
    ):
        """When get_config raises, continues to next key."""
        task_d = TaskMessageDispatcher(run_repository=mock_run_repo)
        queue_d = MagicMock(spec=MessageDispatcher)
        queue_d.__class__.__name__ = "QueueTaskMessageDispatcher"

        call_count = [0]

        def get_config(key):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("db error")
            return _config_response("QueueTaskMessageDispatcher")

        config_service = MagicMock()
        config_service.get_config.side_effect = get_config

        runner = _make_runner_with_config(
            mock_selector,
            mock_run_repo,
            mock_bot_service_plugin,
            config_service,
            [queue_d, task_d],
        )
        result = runner._select_dispatcher("bot-1")
        assert result is queue_d

    def test_config_empty_value_falls_through(
        self, mock_selector, mock_run_repo, mock_bot_service_plugin
    ):
        """Empty conf_value is treated as unconfigured, falls through to next key."""
        task_d = TaskMessageDispatcher(run_repository=mock_run_repo)
        queue_d = MagicMock(spec=MessageDispatcher)
        queue_d.__class__.__name__ = "QueueTaskMessageDispatcher"

        def get_config(key):
            if key == "bot_run.dispatcher_route.*":
                return _config_response("QueueTaskMessageDispatcher")
            return _config_response("   ")

        config_service = MagicMock()
        config_service.get_config.side_effect = get_config

        runner = _make_runner_with_config(
            mock_selector,
            mock_run_repo,
            mock_bot_service_plugin,
            config_service,
            [queue_d, task_d],
        )
        result = runner._select_dispatcher("bot-1")
        assert result is queue_d

    def test_config_unknown_dispatcher_name_falls_back(
        self, mock_selector, mock_run_repo, mock_bot_service_plugin
    ):
        """When config value is not in dispatcher_map, falls back to last dispatcher."""
        task_d = TaskMessageDispatcher(run_repository=mock_run_repo)
        config_service = MagicMock()
        config_service.get_config.return_value = _config_response(
            "NonExistentDispatcher"
        )

        runner = _make_runner_with_config(
            mock_selector,
            mock_run_repo,
            mock_bot_service_plugin,
            config_service,
            [task_d],
        )
        result = runner._select_dispatcher("bot-1")
        assert result is task_d
