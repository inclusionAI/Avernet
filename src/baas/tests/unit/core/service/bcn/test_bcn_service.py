"""Unit tests for DefaultBcnDownlinkService."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.community.api.bcn import (
    Attachment,
    BotRef,
    ChatHistoryInput,
    ChatInjectInput,
    ChatSendInput,
    ContentBlock,
    DownlinkMessage,
    FromRef,
)
from secbaas.community.core.service.bcn._bcn_service import (
    DefaultBcnDownlinkService,
    _extract_message_text,
)

# ==================== _extract_message_text tests ====================


def test_extract_message_text_str():
    msg = DownlinkMessage(role="user", content="hello world")
    assert _extract_message_text(msg) == "hello world"


def test_extract_message_text_blocks():
    msg = DownlinkMessage(
        role="user",
        content=[
            ContentBlock(type="text", text="hello"),
            ContentBlock(type="text", text="world"),
        ],
    )
    assert _extract_message_text(msg) == "hello\nworld"


def test_extract_message_text_blocks_with_non_text():
    msg = DownlinkMessage(
        role="user",
        content=[
            ContentBlock(type="text", text="foo"),
            ContentBlock(type="toolCall", name="search"),
            ContentBlock(type="text", text="bar"),
        ],
    )
    assert _extract_message_text(msg) == "foo\nbar"


def test_extract_message_text_empty_blocks():
    msg = DownlinkMessage(role="user", content=[])
    assert _extract_message_text(msg) == ""


# ==================== Fixtures ====================


@pytest.fixture
def mock_bot_runner():
    runner = MagicMock()
    runner.deliver_message = AsyncMock(return_value=("msg-1", "session-1"))
    runner.inject_message = AsyncMock(return_value=("msg-1", "session-1"))
    runner.get_messages = AsyncMock(return_value=[])
    return runner


@pytest.fixture
def mock_api_key_repo():
    repo = MagicMock()
    record = MagicMock()
    record.api_key_prefix = "baas-prefix"
    record.app_id = "app-001"
    record.app_type = "BCN"
    record.tenant = "tenant-001"
    repo.get_by_prefix.return_value = record
    return repo


@pytest.fixture
def mock_uplink_client():
    client = MagicMock()
    client.send_event = AsyncMock(return_value=MagicMock(ok=True, deduplicated=False))
    return client


@pytest.fixture
def mock_run_repo():
    repo = MagicMock()
    repo.get_by_run_id.return_value = None
    return repo


@pytest.fixture
def service(mock_bot_runner, mock_api_key_repo, mock_uplink_client, mock_run_repo):
    return DefaultBcnDownlinkService(
        bot_runner=mock_bot_runner,
        api_key_repository=mock_api_key_repo,
        bcn_api_key_prefix="baas-prefix",
        uplink_client=mock_uplink_client,
        run_repository=mock_run_repo,
    )


def _make_chat_send_input(**kwargs):
    defaults = dict(
        run_id="run-001",
        session_id="sess-001",
        bcn_group_id="group-001",
        to_bot=BotRef(provider_id="provider-1", provider_bot_ref="bot-001"),
        from_ref=FromRef(kind="user", id="user-1"),
        message=DownlinkMessage(role="user", content="hello"),
        extensions=None,
        timeout_ms=60000,
    )
    defaults.update(kwargs)
    return ChatSendInput(**defaults)


def _make_chat_inject_input(**kwargs):
    defaults = dict(
        id="inject-001",
        session_id="sess-001",
        bcn_group_id="group-001",
        to_bot=BotRef(provider_id="provider-1", provider_bot_ref="bot-001"),
        from_ref=FromRef(kind="user", id="user-1"),
        message=DownlinkMessage(role="user", content="inject msg"),
        timeout_ms=60000,
    )
    defaults.update(kwargs)
    return ChatInjectInput(**defaults)


def _make_chat_history_input(**kwargs):
    defaults = dict(
        id="hist-001",
        session_id="sess-001",
        bcn_group_id="group-001",
        to_bot=BotRef(provider_id="provider-1", provider_bot_ref="bot-001"),
        limit=50,
    )
    defaults.update(kwargs)
    return ChatHistoryInput(**defaults)


def _make_attachment(attachment_id="att_1", **overrides) -> Attachment:
    """Build a domain Attachment dataclass for test inputs."""
    return Attachment(
        attachment_id=attachment_id,
        type="image",
        file_name="test.png",
        url=f"https://cdn.example.com/{attachment_id}",
        **overrides,
    )


# ==================== handle_chat_send tests ====================


@pytest.mark.asyncio
async def test_handle_chat_send_success(service, mock_bot_runner):
    result = await service.handle_chat_send(_make_chat_send_input())
    assert result.ok is True
    # Give the background task time to run
    await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_handle_chat_send_detached(service, mock_bot_runner):
    inp = _make_chat_send_input(extensions={"caller_wait_mode": "detached"})
    result = await service.handle_chat_send(inp)
    assert result.ok is True
    await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_handle_chat_send_deliver_fails(
    service, mock_bot_runner, mock_run_repo, mock_uplink_client
):
    callback_sent = asyncio.Event()

    async def _send_event(*args, **kwargs):
        callback_sent.set()
        return MagicMock(ok=True, deduplicated=False)

    mock_uplink_client.send_event.side_effect = _send_event
    mock_bot_runner.deliver_message = AsyncMock(
        side_effect=RuntimeError("private downstream detail")
    )
    failed_run = MagicMock(
        run_id="run-001",
        status="PENDING",
        error=None,
        result_content_long=None,
        result_extra=None,
        metadata={},
    )
    setattr(failed_run, "bot_id", "bot-001")

    def _update_error(*, run_id: str, error: str) -> None:
        assert run_id == failed_run.run_id
        failed_run.status = "FAILED"
        failed_run.error = error

    mock_run_repo.update_error.side_effect = _update_error
    mock_run_repo.get_by_run_id.return_value = failed_run

    with patch("secbaas.community.core.service.bcn._bcn_service.logger") as mock_logger:
        result = await service.handle_chat_send(_make_chat_send_input())
        assert result.ok is True  # acknowledgement remains fire-and-forget
        await asyncio.wait_for(callback_sent.wait(), timeout=1)

    mock_logger.exception.assert_called_once()
    mock_run_repo.update_error.assert_called_once_with(
        run_id="run-001", error="Message delivery failed"
    )
    mock_uplink_client.send_event.assert_awaited_once()
    event = mock_uplink_client.send_event.await_args.args[0]
    assert event.run_id == "run-001"
    assert event.state == "error"
    assert event.message is not None
    assert event.message.text == "Message delivery failed"
    assert "private downstream detail" not in event.message.text
    callback_kwargs = mock_uplink_client.send_event.await_args.kwargs
    assert callback_kwargs == {
        "bo" + "t_id": "bot-001",
        "event_id": "run-001",
    }


@pytest.mark.asyncio
async def test_handle_chat_send_no_api_key(service, mock_api_key_repo):
    mock_api_key_repo.get_by_prefix.return_value = None
    with pytest.raises(ValueError, match="api key not found"):
        await service.handle_chat_send(_make_chat_send_input())


# ==================== handle_chat_send_stream tests ====================


@pytest.mark.asyncio
async def test_handle_chat_send_stream_success(service, mock_bot_runner):
    async def _async_gen():
        yield MagicMock()

    mock_bot_runner.deliver_message_stream = AsyncMock(
        return_value=("run-001", "sess-001", _async_gen())
    )
    stream = await service.handle_chat_send_stream(_make_chat_send_input())
    assert stream is not None


# ==================== handle_chat_inject tests ====================


@pytest.mark.asyncio
async def test_handle_chat_inject_success(service, mock_bot_runner):
    result = await service.handle_chat_inject(_make_chat_inject_input())
    assert result.ok is True
    mock_bot_runner.inject_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_chat_inject_failure(service, mock_bot_runner):
    mock_bot_runner.inject_message = AsyncMock(side_effect=RuntimeError("boom"))
    result = await service.handle_chat_inject(_make_chat_inject_input())
    assert result.ok is False
    mock_bot_runner.inject_message.assert_awaited_once()


# ==================== handle_chat_history tests ====================


@pytest.mark.asyncio
async def test_handle_chat_history_success(service, mock_bot_runner):
    msg = MagicMock()
    msg.role = "user"
    msg.content = "hello"
    msg.created_at = "2025-01-01T12:00:00"
    msg.id = "msg-1"
    mock_bot_runner.get_messages = AsyncMock(return_value=[msg])
    result = await service.handle_chat_history(_make_chat_history_input())
    assert result.ok is True
    assert len(result.messages) == 1
    assert result.messages[0].role == "user"


@pytest.mark.asyncio
async def test_handle_chat_history_no_messages(service, mock_bot_runner):
    mock_bot_runner.get_messages = AsyncMock(return_value=[])
    result = await service.handle_chat_history(_make_chat_history_input())
    assert result.ok is True
    assert len(result.messages) == 0


@pytest.mark.asyncio
async def test_handle_chat_history_exception(service, mock_bot_runner):
    mock_bot_runner.get_messages = AsyncMock(side_effect=RuntimeError("boom"))
    result = await service.handle_chat_history(_make_chat_history_input())
    assert result.ok is False
    assert len(result.messages) == 0


@pytest.mark.asyncio
async def test_handle_chat_history_null_created_at(service, mock_bot_runner):
    msg = MagicMock()
    msg.role = "user"
    msg.content = "hello"
    msg.created_at = None
    msg.id = "msg-1"
    mock_bot_runner.get_messages = AsyncMock(return_value=[msg])
    result = await service.handle_chat_history(_make_chat_history_input())
    assert result.ok is True
    assert result.messages[0].timestamp == 0


@pytest.mark.asyncio
async def test_handle_chat_history_invalid_created_at(service, mock_bot_runner):
    msg = MagicMock()
    msg.role = "user"
    msg.content = "hello"
    msg.created_at = "not-a-date"
    msg.id = "msg-1"
    mock_bot_runner.get_messages = AsyncMock(return_value=[msg])
    result = await service.handle_chat_history(_make_chat_history_input())
    assert result.ok is True
    assert result.messages[0].timestamp == 0


@pytest.mark.asyncio
async def test_handle_chat_history_null_msg_id(service, mock_bot_runner):
    msg = MagicMock()
    msg.role = "user"
    msg.content = "hello"
    msg.created_at = "2025-01-01T12:00:00"
    msg.id = None
    mock_bot_runner.get_messages = AsyncMock(return_value=[msg])
    result = await service.handle_chat_history(_make_chat_history_input())
    assert result.ok is True
    assert result.messages[0].id is not None  # fallback id


# ==================== _build_bcn_metadata tests ====================


def test_build_bcn_metadata_no_tags(service):
    meta = service._build_bcn_metadata("sess-1", "group-1")
    assert meta["session_id"] == "sess-1"
    assert meta["bcn_group_id"] == "group-1"
    assert meta["bot_options"]["lifecycle_stage"] == "all"
    assert meta["ignore_result"] == "false"


def test_build_bcn_metadata_with_tags(service):
    meta = service._build_bcn_metadata("sess-1", "group-1", tags=["online", "other"])
    assert meta["bot_options"]["lifecycle_stage"] == "online"


def test_build_bcn_metadata_with_verify_tag(service):
    meta = service._build_bcn_metadata("sess-1", "group-1", tags=["verify"])
    assert meta["bot_options"]["lifecycle_stage"] == "verify"


def test_build_bcn_metadata_with_draft_tag(service):
    meta = service._build_bcn_metadata("sess-1", "group-1", tags=["draft"])
    assert meta["bot_options"]["lifecycle_stage"] == "draft"


def test_build_bcn_metadata_ignore_result(service):
    meta = service._build_bcn_metadata("sess-1", "group-1", ignore_result=True)
    assert meta["ignore_result"] == "true"


def test_build_bcn_metadata_request_type(service):
    meta = service._build_bcn_metadata("sess-1", "group-1", request_type="chat")
    assert meta["request_type"] == "chat"


# ==================== attachment passthrough tests ====================


@pytest.mark.asyncio
async def test_handle_chat_send_with_attachments(service, mock_bot_runner):
    """Service passes attachments from ChatSendInput to bot_runner.deliver_message."""
    att1 = _make_attachment(attachment_id="att_1")
    att2 = _make_attachment(attachment_id="att_2")
    inp = _make_chat_send_input(attachments=[att1, att2])
    result = await service.handle_chat_send(inp)
    assert result.ok is True
    # Wait for the background asyncio.create_task to run
    await asyncio.sleep(0.01)

    mock_bot_runner.deliver_message.assert_awaited_once()
    kwargs = mock_bot_runner.deliver_message.call_args.kwargs
    attachments = kwargs.get("attachments")
    assert attachments is not None, (
        "deliver_message must be called with attachments kwarg"
    )
    assert len(attachments) == 2
    assert attachments[0].attachment_id == "att_1"
    assert attachments[1].attachment_id == "att_2"


@pytest.mark.asyncio
async def test_handle_chat_send_without_attachments(service, mock_bot_runner):
    """Service calls deliver_message without attachments when ChatSendInput.attachments is None."""
    inp = _make_chat_send_input()  # attachments defaults to None
    result = await service.handle_chat_send(inp)
    assert result.ok is True
    await asyncio.sleep(0.01)

    mock_bot_runner.deliver_message.assert_awaited_once()
    kwargs = mock_bot_runner.deliver_message.call_args.kwargs
    assert kwargs.get("attachments") is None, (
        "attachments kwarg must be None when not set"
    )


@pytest.mark.asyncio
async def test_handle_chat_inject_with_attachments(service, mock_bot_runner):
    """Service passes attachments from ChatInjectInput to bot_runner.inject_message."""
    att = _make_attachment(attachment_id="att_1")
    inp = _make_chat_inject_input(attachments=[att])
    result = await service.handle_chat_inject(inp)
    assert result.ok is True

    mock_bot_runner.inject_message.assert_awaited_once()
    kwargs = mock_bot_runner.inject_message.call_args.kwargs
    attachments = kwargs.get("attachments")
    assert attachments is not None, (
        "inject_message must be called with attachments kwarg"
    )
    assert len(attachments) == 1
    assert attachments[0].attachment_id == "att_1"


@pytest.mark.asyncio
async def test_handle_chat_send_stream_with_attachments(service, mock_bot_runner):
    """Service passes attachments from ChatSendInput to bot_runner.deliver_message_stream."""

    async def _async_gen():
        yield MagicMock()

    mock_bot_runner.deliver_message_stream = AsyncMock(
        return_value=("run-001", "sess-001", _async_gen())
    )

    att = _make_attachment(attachment_id="att_1")
    inp = _make_chat_send_input(attachments=[att])
    stream = await service.handle_chat_send_stream(inp)
    assert stream is not None

    mock_bot_runner.deliver_message_stream.assert_awaited_once()
    kwargs = mock_bot_runner.deliver_message_stream.call_args.kwargs
    attachments = kwargs.get("attachments")
    assert attachments is not None, (
        "deliver_message_stream must be called with attachments kwarg"
    )
    assert len(attachments) == 1
    assert attachments[0].attachment_id == "att_1"
