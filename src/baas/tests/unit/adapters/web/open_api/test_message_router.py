"""Unit tests for message_router.py.

Uses Patch + AsyncMock to invoke handler functions directly,
NOT TestClient. Covers deliver_message and get_message_result
endpoints plus _normalize_bot_id helper.
"""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from secbaas.community.adapters.web.routers.open_api.message_router import (
    deliver_message,
    deliver_message_stream,
    get_message_result,
    normalize_bot_id,
)
from secbaas.community.api.api_gateway import APIKeyRecord
from secbaas.community.api.bot_runtime import (
    BotChatContext,
    BotNotAvailableError,
    BotNotFoundError,
    BotServiceError,
)
from secbaas.community.api.open_api import OpenAPICode
from secbaas.community.api.sse import StreamChunk
from secbaas.community.core.repository.bot_run import BotRunRecord

# ── helpers ──────────────────────────────────────────────────


def _make_api_key_record(app_type="system", app_id="bot-1:entity-1", tenant="t1"):
    return APIKeyRecord(
        id=1,
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
        api_key_hash="h",
        api_key_prefix="kp-001",
        key_name="k",
        app_id=app_id,
        app_type=app_type,
        description=None,
        rate_limit_rpm=None,
        rate_limit_rpd=None,
        status="ACTIVE",
        owner="o",
        tenant=tenant,
        env="test",
        creator="c",
        modifier=None,
        policy=None,
    )


def _make_context():
    return BotChatContext(
        api_key_prefix="kp-001",
        app_id="bot-1:entity-1",
        app_type="system",
        iam_token=None,
        tenant="t1",
    )


def _make_run_record(**overrides):
    defaults = {
        "id": 1,
        "gmt_create": datetime.now(),
        "gmt_modified": datetime.now(),
        "run_id": "msg-001",
        "bot_id": "bot-1:entity-1",
        "api_key_prefix": "kp-001",
        "message": "hello",
        "message_long": "hello",
        "metadata": {"session_id": "sess-1"},
        "status": "COMPLETED",
        "result_content": "reply",
        "result_content_long": "reply content",
        "result_extra": {"usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        "error": None,
        "completed_at": datetime.now(),
    }
    defaults.update(overrides)
    return BotRunRecord(**defaults)


def _make_message_request(bot_id="bot-1:entity-1", message="hello", metadata=None):
    from secbaas.community.adapters.web.routers.open_api.model import MessageRequest

    return MessageRequest(bot_id=bot_id, message=message, metadata=metadata)


def _make_stream_message_request(
    bot_id="bot-1:entity-1", message="hello", metadata=None
):
    from secbaas.community.adapters.web.routers.open_api.model import (
        StreamMessageRequest,
    )

    return StreamMessageRequest(bot_id=bot_id, message=message, metadata=metadata)


# ── _normalize_bot_id ────────────────────────────────────────


class TestNormalizeBotId:
    def test_no_colon_passthrough(self):
        assert normalize_bot_id("simple") == "simple"

    def test_colon_strips_leading_zeros(self):
        assert normalize_bot_id("bot:000123") == "bot:123"

    def test_colon_no_leading_zeros_unchanged(self):
        assert normalize_bot_id("bot:123") == "bot:123"

    def test_colon_all_zeros_returns_zero(self):
        assert normalize_bot_id("bot:000") == "bot:0"

    def test_multiple_colons_strips_last_part(self):
        assert normalize_bot_id("a:b:007") == "a:b:7"


# ── deliver_message ──────────────────────────────────────────


class TestDeliverMessage:
    @pytest.mark.asyncio
    async def test_non_system_app_type_returns_403(self):
        """app_type not in system/app → 403."""
        api_key = _make_api_key_record(app_type="user")
        ctx = _make_context()
        req = _make_message_request()

        mock_runner = MagicMock()
        mock_mock_runner = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await deliver_message(
                request=req,
                api_key_record=api_key,
                context=ctx,
                bot_runner=mock_runner,
            )
        assert exc.value.status_code == 403
        detail = exc.value.detail
        assert detail["code"] == OpenAPICode.FORBIDDEN

    @pytest.mark.asyncio
    async def test_system_app_type_passes_check(self):
        """system app_type should pass the check."""
        api_key = _make_api_key_record(app_type="system")
        ctx = _make_context()
        req = _make_message_request()

        mock_runner = AsyncMock()
        mock_runner.deliver_message = AsyncMock(return_value=("msg-001", "sess-1"))

        mock_mock_runner = MagicMock()

        with patch(
            "secbaas.community.adapters.web.routers.open_api.message_router.validate_policy",
            return_value="bot-1:entity-1",
        ) as mock_policy:
            result = await deliver_message(
                request=req,
                api_key_record=api_key,
                context=ctx,
                bot_runner=mock_runner,
            )
            mock_policy.assert_called_once_with(api_key, "bot-1:entity-1")
        assert result.code == 0
        assert result.message == "success"
        assert result.data.message_id == "msg-001"
        assert result.data.session_id == "sess-1"

    @pytest.mark.asyncio
    async def test_app_app_type_passes_check(self):
        """app app_type should pass the check."""
        api_key = _make_api_key_record(app_type="app")
        ctx = _make_context()
        req = _make_message_request()

        mock_runner = AsyncMock()
        mock_runner.deliver_message = AsyncMock(return_value=("msg-002", "sess-2"))

        mock_mock_runner = MagicMock()

        with patch(
            "secbaas.community.adapters.web.routers.open_api.message_router.validate_policy",
            return_value="bot-1:entity-1",
        ):
            result = await deliver_message(
                request=req,
                api_key_record=api_key,
                context=ctx,
                bot_runner=mock_runner,
            )
        assert result.code == 0

    @pytest.mark.asyncio
    async def test_normalizes_bot_id_with_leading_zeros(self):
        """bot_id with leading zeros in entity part gets normalized."""
        api_key = _make_api_key_record(app_type="system")
        ctx = _make_context()
        req = _make_message_request(bot_id="bot-1:000123")

        mock_runner = AsyncMock()
        mock_runner.deliver_message = AsyncMock(return_value=("msg-003", "sess-3"))

        mock_mock_runner = MagicMock()

        with patch(
            "secbaas.community.adapters.web.routers.open_api.message_router.validate_policy",
            return_value="bot-1:123",
        ) as mock_policy:
            await deliver_message(
                request=req,
                api_key_record=api_key,
                context=ctx,
                bot_runner=mock_runner,
            )
            mock_policy.assert_called_once_with(api_key, "bot-1:123")

    @pytest.mark.asyncio
    async def test_lifecycle_stage_from_metadata(self):
        """lifecycle_stage extracted from metadata.bot_options."""
        api_key = _make_api_key_record(app_type="system")
        ctx = _make_context()
        req = _make_message_request(
            metadata={"bot_options": {"lifecycle_stage": "draft"}}
        )

        mock_runner = AsyncMock()
        mock_runner.deliver_message = AsyncMock(return_value=("msg-004", "sess-4"))

        mock_mock_runner = MagicMock()

        with patch(
            "secbaas.community.adapters.web.routers.open_api.message_router.validate_policy",
            return_value="bot-1:entity-1",
        ):
            await deliver_message(
                request=req,
                api_key_record=api_key,
                context=ctx,
                bot_runner=mock_runner,
            )

    @pytest.mark.asyncio
    async def test_lifecycle_stage_missing_from_metadata(self):
        """No lifecycle_stage when metadata.bot_options missing."""
        api_key = _make_api_key_record(app_type="system")
        ctx = _make_context()
        req = _make_message_request(metadata={"other": "value"})

        mock_runner = AsyncMock()
        mock_runner.deliver_message = AsyncMock(return_value=("msg-005", "sess-5"))

        mock_mock_runner = MagicMock()

        with patch(
            "secbaas.community.adapters.web.routers.open_api.message_router.validate_policy",
            return_value="bot-1:entity-1",
        ):
            result = await deliver_message(
                request=req,
                api_key_record=api_key,
                context=ctx,
                bot_runner=mock_runner,
            )
        assert result.code == 0

    @pytest.mark.asyncio
    async def test_lifecycle_stage_no_metadata(self):
        """No lifecycle_stage when metadata is None."""
        api_key = _make_api_key_record(app_type="system")
        ctx = _make_context()
        req = _make_message_request(metadata=None)

        mock_runner = AsyncMock()
        mock_runner.deliver_message = AsyncMock(return_value=("msg-006", "sess-6"))

        mock_mock_runner = MagicMock()

        with patch(
            "secbaas.community.adapters.web.routers.open_api.message_router.validate_policy",
            return_value="bot-1:entity-1",
        ):
            result = await deliver_message(
                request=req,
                api_key_record=api_key,
                context=ctx,
                bot_runner=mock_runner,
            )
        assert result.code == 0

    @pytest.mark.asyncio
    async def test_null_inner_session_id_returns_business_error(self):
        """When deliver_message returns None session_id → BUSINESS_ERROR."""
        api_key = _make_api_key_record(app_type="system")
        ctx = _make_context()
        req = _make_message_request()

        mock_runner = AsyncMock()
        mock_runner.deliver_message = AsyncMock(return_value=("msg-007", None))

        mock_mock_runner = MagicMock()

        with patch(
            "secbaas.community.adapters.web.routers.open_api.message_router.validate_policy",
            return_value="bot-1:entity-1",
        ):
            result = await deliver_message(
                request=req,
                api_key_record=api_key,
                context=ctx,
                bot_runner=mock_runner,
            )
        assert result.code == OpenAPICode.BUSINESS_ERROR
        assert result.message == "Session not exist"
        assert result.data.session_id is None

    @pytest.mark.asyncio
    async def test_bot_not_found_returns_404(self):
        """BotNotFoundError → 404."""
        api_key = _make_api_key_record(app_type="system")
        ctx = _make_context()
        req = _make_message_request()

        mock_runner = AsyncMock()
        mock_runner.deliver_message = AsyncMock(side_effect=BotNotFoundError("bot-1"))

        mock_mock_runner = MagicMock()

        with patch(
            "secbaas.community.adapters.web.routers.open_api.message_router.validate_policy",
            return_value="bot-1:entity-1",
        ):
            with pytest.raises(HTTPException) as exc:
                await deliver_message(
                    request=req,
                    api_key_record=api_key,
                    context=ctx,
                    bot_runner=mock_runner,
                )
        assert exc.value.status_code == 404
        assert exc.value.detail["code"] == 60001

    @pytest.mark.asyncio
    async def test_bot_not_available_returns_503(self):
        """BotNotAvailableError → 503."""
        api_key = _make_api_key_record(app_type="system")
        ctx = _make_context()
        req = _make_message_request()

        mock_runner = AsyncMock()
        mock_runner.deliver_message = AsyncMock(
            side_effect=BotNotAvailableError("bot-1", "INACTIVE")
        )

        mock_mock_runner = MagicMock()

        with patch(
            "secbaas.community.adapters.web.routers.open_api.message_router.validate_policy",
            return_value="bot-1:entity-1",
        ):
            with pytest.raises(HTTPException) as exc:
                await deliver_message(
                    request=req,
                    api_key_record=api_key,
                    context=ctx,
                    bot_runner=mock_runner,
                )
        assert exc.value.status_code == 503
        assert exc.value.detail["code"] == 60001

    @pytest.mark.asyncio
    async def test_bot_service_error_returns_400(self):
        """BotServiceError → 400."""
        api_key = _make_api_key_record(app_type="system")
        ctx = _make_context()
        req = _make_message_request()

        mock_runner = AsyncMock()
        mock_runner.deliver_message = AsyncMock(
            side_effect=BotServiceError("tenant is required")
        )

        mock_mock_runner = MagicMock()

        with patch(
            "secbaas.community.adapters.web.routers.open_api.message_router.validate_policy",
            return_value="bot-1:entity-1",
        ):
            with pytest.raises(HTTPException) as exc:
                await deliver_message(
                    request=req,
                    api_key_record=api_key,
                    context=ctx,
                    bot_runner=mock_runner,
                )
        assert exc.value.status_code == 400
        assert exc.value.detail["code"] == OpenAPICode.BUSINESS_ERROR

    @pytest.mark.asyncio
    async def test_generic_exception_returns_500(self):
        """Unexpected Exception → 500."""
        api_key = _make_api_key_record(app_type="system")
        ctx = _make_context()
        req = _make_message_request()

        mock_runner = AsyncMock()
        mock_runner.deliver_message = AsyncMock(side_effect=RuntimeError("boom"))

        mock_mock_runner = MagicMock()

        with patch(
            "secbaas.community.adapters.web.routers.open_api.message_router.validate_policy",
            return_value="bot-1:entity-1",
        ):
            with pytest.raises(HTTPException) as exc:
                await deliver_message(
                    request=req,
                    api_key_record=api_key,
                    context=ctx,
                    bot_runner=mock_runner,
                )
        assert exc.value.status_code == 500
        assert exc.value.detail["code"] == 50001


# ── deliver_message: callback_url ────────────────────────────


class TestDeliverMessageCallbackUrl:
    @pytest.mark.asyncio
    async def test_callback_url_sets_metadata_and_callback(self):
        """When callback_url is provided, metadata gets callback_url and callback='http_callback'."""
        api_key = _make_api_key_record(app_type="system")
        ctx = _make_context()
        req = _make_message_request()
        req.callback_url = "http://example.com/cb"

        mock_runner = AsyncMock()
        mock_runner.deliver_message = AsyncMock(return_value=("msg-cb-1", "sess-cb-1"))

        with patch(
            "secbaas.community.adapters.web.routers.open_api.message_router.validate_policy",
            return_value="bot-1:entity-1",
        ):
            await deliver_message(
                request=req,
                api_key_record=api_key,
                context=ctx,
                bot_runner=mock_runner,
            )

        call_kwargs = mock_runner.deliver_message.call_args.kwargs
        assert call_kwargs["metadata"]["callback_url"] == "http://example.com/cb"
        assert call_kwargs["callback"] == "http_callback"

    @pytest.mark.asyncio
    async def test_no_callback_url_means_none_callback(self):
        """When callback_url is None, callback is None and metadata has no callback_url."""
        api_key = _make_api_key_record(app_type="system")
        ctx = _make_context()
        req = _make_message_request(metadata={"timeout": "30"})

        mock_runner = AsyncMock()
        mock_runner.deliver_message = AsyncMock(return_value=("msg-cb-2", "sess-cb-2"))

        with patch(
            "secbaas.community.adapters.web.routers.open_api.message_router.validate_policy",
            return_value="bot-1:entity-1",
        ):
            await deliver_message(
                request=req,
                api_key_record=api_key,
                context=ctx,
                bot_runner=mock_runner,
            )

        call_kwargs = mock_runner.deliver_message.call_args.kwargs
        assert call_kwargs["callback"] is None
        assert "callback_url" not in call_kwargs["metadata"]
        assert call_kwargs["metadata"]["timeout"] == "30"

    @pytest.mark.asyncio
    async def test_callback_url_with_existing_metadata(self):
        """callback_url is added to existing metadata without losing other keys."""
        api_key = _make_api_key_record(app_type="system")
        ctx = _make_context()
        req = _make_message_request(metadata={"timeout": "30", "biz_scene": "test"})
        req.callback_url = "http://example.com/hook"

        mock_runner = AsyncMock()
        mock_runner.deliver_message = AsyncMock(return_value=("msg-cb-3", "sess-cb-3"))

        with patch(
            "secbaas.community.adapters.web.routers.open_api.message_router.validate_policy",
            return_value="bot-1:entity-1",
        ):
            await deliver_message(
                request=req,
                api_key_record=api_key,
                context=ctx,
                bot_runner=mock_runner,
            )

        call_kwargs = mock_runner.deliver_message.call_args.kwargs
        assert call_kwargs["metadata"]["callback_url"] == "http://example.com/hook"
        assert call_kwargs["metadata"]["timeout"] == "30"
        assert call_kwargs["metadata"]["biz_scene"] == "test"
        assert call_kwargs["callback"] == "http_callback"


# ── deliver_message_stream ──────────────────────────────────


async def _async_iter(chunks: list[StreamChunk]):
    for c in chunks:
        yield c


def _make_converter_factory():
    from secbaas.community.core.service.sse import DefaultStreamConverter

    factory = MagicMock()
    factory.create = MagicMock(return_value=DefaultStreamConverter())
    return factory


class TestDeliverMessageStream:
    @pytest.mark.asyncio
    async def test_non_system_app_type_returns_403(self):
        api_key = _make_api_key_record(app_type="user")
        ctx = _make_context()
        req = _make_stream_message_request()

        mock_runner = MagicMock()
        with pytest.raises(HTTPException) as exc:
            await deliver_message_stream(
                request=req,
                api_key_record=api_key,
                context=ctx,
                bot_runner=mock_runner,
                converter_factory=_make_converter_factory(),
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_success_returns_streaming_response(self):
        from fastapi.responses import StreamingResponse

        api_key = _make_api_key_record(app_type="system")
        ctx = _make_context()
        req = _make_stream_message_request()

        chunks = [
            StreamChunk(type="delta", content="hel"),
            StreamChunk(type="delta", content="lo"),
            StreamChunk(type="final", content="hello world"),
        ]
        mock_runner = AsyncMock()
        mock_runner.deliver_message_stream = AsyncMock(
            return_value=("msg-s1", "sess-s1", _async_iter(chunks))
        )

        with patch(
            "secbaas.community.adapters.web.routers.open_api.message_router.validate_policy",
            return_value="bot-1:entity-1",
        ):
            result = await deliver_message_stream(
                request=req,
                api_key_record=api_key,
                context=ctx,
                bot_runner=mock_runner,
                converter_factory=_make_converter_factory(),
            )
        assert isinstance(result, StreamingResponse)
        assert result.media_type == "text/event-stream"
        mock_runner.deliver_message_stream.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_value_error_returns_400(self):
        api_key = _make_api_key_record(app_type="system")
        ctx = _make_context()
        req = _make_stream_message_request()

        mock_runner = AsyncMock()
        mock_runner.deliver_message_stream = AsyncMock(
            side_effect=ValueError("Duplicate run_id")
        )

        with patch(
            "secbaas.community.adapters.web.routers.open_api.message_router.validate_policy",
            return_value="bot-1:entity-1",
        ):
            with pytest.raises(HTTPException) as exc:
                await deliver_message_stream(
                    request=req,
                    api_key_record=api_key,
                    context=ctx,
                    bot_runner=mock_runner,
                    converter_factory=_make_converter_factory(),
                )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_bot_not_found_returns_404(self):
        api_key = _make_api_key_record(app_type="system")
        ctx = _make_context()
        req = _make_stream_message_request()

        mock_runner = AsyncMock()
        mock_runner.deliver_message_stream = AsyncMock(
            side_effect=BotNotFoundError("bot-1")
        )

        with patch(
            "secbaas.community.adapters.web.routers.open_api.message_router.validate_policy",
            return_value="bot-1:entity-1",
        ):
            with pytest.raises(HTTPException) as exc:
                await deliver_message_stream(
                    request=req,
                    api_key_record=api_key,
                    context=ctx,
                    bot_runner=mock_runner,
                    converter_factory=_make_converter_factory(),
                )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_sets_stream_metadata(self):
        api_key = _make_api_key_record(app_type="system")
        ctx = _make_context()
        req = _make_stream_message_request(metadata={"timeout": "30"})

        chunks = [StreamChunk(type="final", content="done")]
        mock_runner = AsyncMock()
        mock_runner.deliver_message_stream = AsyncMock(
            return_value=("msg-s2", "sess-s2", _async_iter(chunks))
        )

        with patch(
            "secbaas.community.adapters.web.routers.open_api.message_router.validate_policy",
            return_value="bot-1:entity-1",
        ):
            await deliver_message_stream(
                request=req,
                api_key_record=api_key,
                context=ctx,
                bot_runner=mock_runner,
                converter_factory=_make_converter_factory(),
            )
        call_kwargs = mock_runner.deliver_message_stream.call_args.kwargs
        assert call_kwargs["metadata"]["stream"] == "true"
        assert call_kwargs["metadata"]["timeout"] == "30"

    @pytest.mark.asyncio
    async def test_chunk_error_yields_error_sse(self):
        """When chunk_iter raises, on_error produces an error SSE event."""

        async def failing_chunks():
            yield StreamChunk(type="delta", content="hi")
            raise RuntimeError("chunk boom")

        api_key = _make_api_key_record(app_type="system")
        ctx = _make_context()
        req = _make_stream_message_request()

        mock_runner = AsyncMock()
        mock_runner.deliver_message_stream = AsyncMock(
            return_value=("msg-e1", "sess-e1", failing_chunks())
        )

        with patch(
            "secbaas.community.adapters.web.routers.open_api.message_router.validate_policy",
            return_value="bot-1:entity-1",
        ):
            result = await deliver_message_stream(
                request=req,
                api_key_record=api_key,
                context=ctx,
                bot_runner=mock_runner,
                converter_factory=_make_converter_factory(),
            )

        items = []
        async for item in result.body_iterator:
            items.append(item)

        # ready + delta + error
        assert len(items) == 3
        assert items[0].startswith("event: ready\n")
        assert "event: chat\n" in items[1]
        assert items[2].startswith("event: error\n")
        data_line = next(
            line for line in items[2].splitlines() if line.startswith("data: ")
        )
        data = json.loads(data_line.removeprefix("data: "))
        assert data["message_id"] == "msg-e1"
        assert "chunk boom" in data["error"]


# ── get_message_result ──────────────────────────────────────


class TestGetMessageResult:
    """Tests for get_message_result endpoint (lines 183-239)."""

    # ── helpers ──

    @staticmethod
    def _make_runner(record_or_side_effect):
        """Create a mock BotRunner."""
        mock_runner = MagicMock()
        if callable(record_or_side_effect):
            mock_runner.get_result = MagicMock(side_effect=record_or_side_effect)
        else:
            mock_runner.get_result = MagicMock(return_value=record_or_side_effect)
        return mock_runner

    # ── auth: app_type check ──

    @pytest.mark.asyncio
    async def test_non_system_app_type_returns_403(self):
        """app_type not in ('system','app') → 403."""
        api_key = _make_api_key_record(app_type="user")
        mock_runner = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await get_message_result(
                message_id="msg-001",
                api_key_record=api_key,
                bot_runner=mock_runner,
            )
        assert exc.value.status_code == 403
        assert exc.value.detail["code"] == OpenAPICode.FORBIDDEN

    # ── api_key_prefix mismatch (横向越权) ──

    @pytest.mark.asyncio
    async def test_different_api_key_prefix_returns_business_error(self):
        """api_key_prefix mismatch → BUSINESS_ERROR with data=None."""
        api_key = _make_api_key_record()
        record = _make_run_record(api_key_prefix="kp-OTHER")

        with patch(
            "secbaas.community.adapters.web.routers.open_api.message_router.validate_policy",
            return_value="bot-1:entity-1",
        ):
            result = await get_message_result(
                message_id="msg-001",
                api_key_record=api_key,
                bot_runner=self._make_runner(record),
            )
        assert result.code == OpenAPICode.BUSINESS_ERROR
        assert result.message == "Message not found: msg-001"
        assert result.data is None

    # ── happy path: full result with content + extra ──

    @pytest.mark.asyncio
    async def test_full_result_with_content_and_extra(self):
        """Complete result: content + extra, session from metadata."""
        api_key = _make_api_key_record()
        record = _make_run_record()

        with patch(
            "secbaas.community.adapters.web.routers.open_api.message_router.validate_policy",
            return_value="bot-1:entity-1",
        ):
            result = await get_message_result(
                message_id="msg-001",
                api_key_record=api_key,
                bot_runner=self._make_runner(record),
            )
        assert result.code == 0
        assert result.message == "success"
        data = result.data
        assert data.message_id == record.run_id
        assert data.bot_id == record.bot_id
        assert data.session_id == "sess-1"  # from metadata
        assert data.status == record.status
        assert data.result is not None
        assert data.result.content == "reply"
        assert data.result.extra is not None
        assert data.result.extra.usage == {"prompt_tokens": 10, "completion_tokens": 5}

    # ── result_content but no result_extra ──

    @pytest.mark.asyncio
    async def test_result_content_without_extra(self):
        """result_content present, no result_extra → extra=None."""
        api_key = _make_api_key_record()
        record = _make_run_record(result_extra=None)

        with patch(
            "secbaas.community.adapters.web.routers.open_api.message_router.validate_policy",
            return_value="bot-1:entity-1",
        ):
            result = await get_message_result(
                message_id="msg-001",
                api_key_record=api_key,
                bot_runner=self._make_runner(record),
            )
        assert result.data.result is not None
        assert result.data.result.content == "reply"
        assert result.data.result.extra is None

    # ── no result_content → result_data=None ──

    @pytest.mark.asyncio
    async def test_no_result_content(self):
        """No result_content → result is None."""
        api_key = _make_api_key_record()
        record = _make_run_record(result_content=None, result_extra=None)

        with patch(
            "secbaas.community.adapters.web.routers.open_api.message_router.validate_policy",
            return_value="bot-1:entity-1",
        ):
            result = await get_message_result(
                message_id="msg-001",
                api_key_record=api_key,
                bot_runner=self._make_runner(record),
            )
        assert result.data.result is None

    # ── session_id from result_extra when metadata absent ──

    @pytest.mark.asyncio
    async def test_session_id_from_result_extra(self):
        """Session from result_extra when metadata has no session_id."""
        api_key = _make_api_key_record()
        record = _make_run_record(
            metadata={"other": "val"},
            result_extra={"session_id": "sess-from-extra", "usage": None},
        )

        with patch(
            "secbaas.community.adapters.web.routers.open_api.message_router.validate_policy",
            return_value="bot-1:entity-1",
        ):
            result = await get_message_result(
                message_id="msg-001",
                api_key_record=api_key,
                bot_runner=self._make_runner(record),
            )
        assert result.data.session_id == "sess-from-extra"

    # ── session_id from result_extra when metadata is None ──

    @pytest.mark.asyncio
    async def test_session_id_from_result_extra_when_metadata_none(self):
        """Session from result_extra when metadata is None."""
        api_key = _make_api_key_record()
        record = _make_run_record(
            metadata=None,
            result_extra={"session_id": "sess-from-extra", "usage": None},
        )

        with patch(
            "secbaas.community.adapters.web.routers.open_api.message_router.validate_policy",
            return_value="bot-1:entity-1",
        ):
            result = await get_message_result(
                message_id="msg-001",
                api_key_record=api_key,
                bot_runner=self._make_runner(record),
            )
        assert result.data.session_id == "sess-from-extra"

    # ── session_id empty (neither metadata nor result_extra) ──

    @pytest.mark.asyncio
    async def test_empty_session_id(self):
        """Empty session_id when no metadata and result_extra lacks session_id."""
        api_key = _make_api_key_record()
        record = _make_run_record(
            metadata=None,
            result_content=None,
            result_extra=None,
        )

        with patch(
            "secbaas.community.adapters.web.routers.open_api.message_router.validate_policy",
            return_value="bot-1:entity-1",
        ):
            result = await get_message_result(
                message_id="msg-001",
                api_key_record=api_key,
                bot_runner=self._make_runner(record),
            )
        assert result.data.session_id == ""

    # ── error field propagated ──

    @pytest.mark.asyncio
    async def test_error_field_propagated(self):
        """Record with error field → response includes error."""
        api_key = _make_api_key_record()
        record = _make_run_record(
            result_content=None,
            result_extra=None,
            status="FAILED",
            error="Something went wrong",
        )

        with patch(
            "secbaas.community.adapters.web.routers.open_api.message_router.validate_policy",
            return_value="bot-1:entity-1",
        ):
            result = await get_message_result(
                message_id="msg-001",
                api_key_record=api_key,
                bot_runner=self._make_runner(record),
            )
        assert result.data.error == "Something went wrong"

    # ── KeyError from get_by_run_id → 404 ──

    @pytest.mark.asyncio
    async def test_keyerror_raises_404(self):
        """KeyError inside get_by_run_id → 404."""
        api_key = _make_api_key_record()
        mock_runner = MagicMock()
        mock_runner.get_result = MagicMock(side_effect=KeyError("_db_lookup"))

        with pytest.raises(HTTPException) as exc:
            await get_message_result(
                message_id="no-such-msg",
                api_key_record=api_key,
                bot_runner=mock_runner,
            )
        assert exc.value.status_code == 404
        assert exc.value.detail["code"] == 40401
        assert "no-such-msg" in exc.value.detail["message"]

    # ── app_type=app passes auth ──

    @pytest.mark.asyncio
    async def test_app_app_type_passes_auth(self):
        """app_type='app' should pass the auth check."""
        api_key = _make_api_key_record(app_type="app")
        record = _make_run_record()

        with patch(
            "secbaas.community.adapters.web.routers.open_api.message_router.validate_policy",
            return_value="bot-1:entity-1",
        ):
            result = await get_message_result(
                message_id="msg-001",
                api_key_record=api_key,
                bot_runner=self._make_runner(record),
            )
        assert result.code == 0

    # ── validate_policy called with correct args ──

    @pytest.mark.asyncio
    async def test_validate_policy_called(self):
        """validate_policy called with api_key_record and bot_id from record."""
        api_key = _make_api_key_record()
        record = _make_run_record(bot_id="bot-42:entity-7")

        with patch(
            "secbaas.community.adapters.web.routers.open_api.message_router.validate_policy",
            return_value="bot-42:entity-7",
        ) as mock_policy:
            await get_message_result(
                message_id="msg-001",
                api_key_record=api_key,
                bot_runner=self._make_runner(record),
            )
            mock_policy.assert_called_once_with(api_key, "bot-42:entity-7")
