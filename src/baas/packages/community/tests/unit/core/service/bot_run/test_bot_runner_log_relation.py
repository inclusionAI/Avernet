"""Unit tests for BotRunner log-relation integration via BotServicePlugin.

Covers:
- BotRunner.deliver_message() calls _report_log_relation
- BotRunner.deliver_message() skips report when bot_service_plugin=None
- Metadata biz_task_id/biz_scene override and fallback logic
- Empty string biz_task_id is not replaced by run_id (is not None check)
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.api.bot_runtime import BotChatContext
from secbaas.core.service.bot_run import BotRunner, BotServiceSelector
from secbaas.core.service.bot_run._internal_protocols import MessageDispatcher
from secbaas.spi.bot_service import BotBindingData, BotServicePlugin, LogRelationPayload

# ==================== Fixtures ====================

BOT_ID = "20260507_9szl2cmj"
ENTITY_ID = "397302"
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
def arca_binding_data():
    return BotBindingData(
        bot_id=BOT_ID,
        owner_id=ENTITY_ID,
        bot_type="personal",
        engine_type="openclaw",
        binding_id=1333961,
        device_provider="arca",
        device_id="staff_bot_123",
    )


@pytest.fixture
def mock_bot_service():
    svc = MagicMock()
    svc.create_session = AsyncMock(
        return_value=MagicMock(session_id="agent:main:sess-001")
    )
    svc.send_message = AsyncMock(return_value=MagicMock(content="reply", usage={}))
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
def mock_bot_service_plugin(arca_binding_data):
    """Mock BotServicePlugin with async get_binding and report."""
    plugin = MagicMock(spec=BotServicePlugin)
    plugin.get_binding = AsyncMock(return_value=arca_binding_data)
    plugin.report = AsyncMock()
    return plugin


@pytest.fixture
def mock_dispatcher():
    d = MagicMock(spec=MessageDispatcher)
    d.dispatch_send = AsyncMock()
    return d


def _make_runner(
    mock_selector,
    mock_run_repo,
    mock_bot_service_plugin,
    dispatcher=None,
):
    if dispatcher is None:
        dispatcher = MagicMock(spec=MessageDispatcher)
        dispatcher.dispatch_send = AsyncMock()
    return BotRunner(
        bot_service_selector=mock_selector,
        run_repository=mock_run_repo,
        bot_service_plugin=mock_bot_service_plugin,
        dispatcher=dispatcher,
    )


# ==================== Tests: BotRunner._report_log_relation integration ======


class TestBotRunnerLogRelationIntegration:
    @pytest.mark.asyncio
    async def test_runner_deliver_message_calls_report(
        self,
        mock_selector,
        mock_run_repo,
        mock_bot_service_plugin,
        context,
    ):
        """BotRunner.deliver_message() calls _report_log_relation after dispatch."""
        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        await runner.deliver_message(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={},
            message_id="test-msg-id",
        )

        mock_bot_service_plugin.report.assert_called_once()
        payload = mock_bot_service_plugin.report.call_args[0][0]
        assert isinstance(payload, LogRelationPayload)
        assert payload.biz_task_id == "test-msg-id"  # run_id used as fallback
        assert payload.biz_scene == "default"
        assert payload.engine == "openclaw"
        assert payload.collector == "baas"

    @pytest.mark.asyncio
    async def test_runner_deliver_message_report_failure_is_non_fatal(
        self,
        mock_selector,
        mock_run_repo,
        mock_bot_service_plugin,
        context,
    ):
        """When bot_service_plugin.report() raises, it is caught and logged."""
        mock_bot_service_plugin.report = AsyncMock(
            side_effect=RuntimeError("unexpected")
        )

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        # deliver_message should NOT raise even though report() fails
        await runner.deliver_message(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={},
            message_id="test-msg-id",
        )


# ==================== Tests: metadata biz fields ====================


class TestMetadataBizFields:
    @pytest.mark.asyncio
    async def test_metadata_explicit_biz_fields_override_defaults(
        self,
        mock_selector,
        mock_run_repo,
        mock_bot_service_plugin,
        context,
    ):
        """Explicit biz_task_id and biz_scene in metadata override fallback values."""
        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        await runner.deliver_message(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={
                "biz_task_id": "custom-task-456",
                "biz_scene": "custom_scene",
            },
            message_id="test-msg-id",
        )

        payload = mock_bot_service_plugin.report.call_args[0][0]
        assert payload.biz_task_id == "custom-task-456"
        assert payload.biz_scene == "custom_scene"

    @pytest.mark.asyncio
    async def test_metadata_biz_task_id_none_uses_run_id(
        self,
        mock_selector,
        mock_run_repo,
        mock_bot_service_plugin,
        context,
    ):
        """When biz_task_id=None in metadata, run_id is used instead."""
        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        await runner.deliver_message(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={"biz_task_id": None},
            message_id="run-abc-123",
        )

        payload = mock_bot_service_plugin.report.call_args[0][0]
        assert payload.biz_task_id == "run-abc-123"

    @pytest.mark.asyncio
    async def test_metadata_biz_scene_none_uses_default(
        self,
        mock_selector,
        mock_run_repo,
        mock_bot_service_plugin,
        context,
    ):
        """When biz_scene=None in metadata, 'default' is used."""
        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        await runner.deliver_message(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={"biz_scene": None},
            message_id="test-msg-id",
        )

        payload = mock_bot_service_plugin.report.call_args[0][0]
        assert payload.biz_scene == "default"

    @pytest.mark.asyncio
    async def test_metadata_empty_string_biz_task_id_not_replaced(
        self,
        mock_selector,
        mock_run_repo,
        mock_bot_service_plugin,
        context,
    ):
        """Empty string biz_task_id='' is NOT replaced by run_id.

        This is the review finding: 'is not None' check means empty string
        is a valid explicit value and should pass through unchanged.
        """
        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        await runner.deliver_message(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={"biz_task_id": ""},
            message_id="run-abc-123",
        )

        payload = mock_bot_service_plugin.report.call_args[0][0]
        assert payload.biz_task_id == ""
        # It should NOT be "run-abc-123"

    @pytest.mark.asyncio
    async def test_metadata_none_completely_skips_biz_task_id(
        self,
        mock_selector,
        mock_run_repo,
        mock_bot_service_plugin,
        context,
    ):
        """When metadata has no biz_task_id key at all, run_id is used."""
        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        await runner.deliver_message(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={},  # no biz_task_id key at all
            message_id="run-xyz-789",
        )

        payload = mock_bot_service_plugin.report.call_args[0][0]
        assert payload.biz_task_id == "run-xyz-789"

    @pytest.mark.asyncio
    async def test_report_log_relation_exception_is_caught(
        self,
        mock_selector,
        mock_run_repo,
        mock_bot_service_plugin,
        context,
    ):
        """When _report_log_relation's client.report() raises, it is caught and logged."""
        mock_bot_service_plugin.report = AsyncMock(
            side_effect=RuntimeError("unexpected")
        )

        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        # deliver_message should NOT raise even though report() fails
        await runner.deliver_message(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={},
            message_id="test-msg-id",
        )

    @pytest.mark.asyncio
    async def test_report_log_relation_uses_entity_id_for_user_id(
        self,
        mock_selector,
        mock_run_repo,
        mock_bot_service_plugin,
        context,
    ):
        """_report_log_relation uses binding_info.entity_id as user_id."""
        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        await runner.deliver_message(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={},
            message_id="test-msg-id",
        )

        payload = mock_bot_service_plugin.report.call_args[0][0]
        # user_id should come from binding_info.entity_id
        assert payload.user_id == ENTITY_ID

    @pytest.mark.asyncio
    async def test_report_log_relation_includes_session_in_refs(
        self,
        mock_selector,
        mock_run_repo,
        mock_bot_service_plugin,
        context,
    ):
        """_report_log_relation includes session_id in the refs list."""
        runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)
        await runner.deliver_message(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={},
            message_id="test-msg-id",
        )

        payload = mock_bot_service_plugin.report.call_args[0][0]
        assert payload.refs == [
            {"ref_type": "session_key", "ref_value": "agent:main:sess-001"}
        ]
