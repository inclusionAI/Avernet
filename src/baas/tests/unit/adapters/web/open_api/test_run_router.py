"""Unit tests for run_router.py.

Uses MagicMock/AsyncMock to invoke handler functions directly,
NOT TestClient. Covers run_chat and get_run_result endpoints.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, status

from secbaas.community.adapters.web.routers.open_api.model import RunRequest
from secbaas.community.adapters.web.routers.open_api.run_router import (
    cancel_run,
    get_run_result,
    run_chat,
)
from secbaas.community.api.api_gateway import APIKeyRecord
from secbaas.community.api.bot_runtime import (
    BotChatContext,
    BotNotAvailableError,
    BotNotFoundError,
    BotServiceError,
)
from secbaas.community.api.open_api import OpenAPICode
from secbaas.community.core.repository.bot_run import BotRunRecord

# ── helpers ──────────────────────────────────────────────────


def _make_api_key_record(app_id="bot-1:entity-1", api_key_prefix="kp-001"):
    return APIKeyRecord(
        id=1,
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
        api_key_hash="h",
        api_key_prefix=api_key_prefix,
        key_name="k",
        app_id=app_id,
        app_type="system",
        description=None,
        rate_limit_rpm=None,
        rate_limit_rpd=None,
        status="ACTIVE",
        owner="o",
        tenant="t1",
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
        "run_id": "run-001",
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


def _make_run_request(message="hello", metadata=None):
    return RunRequest(message=message, metadata=metadata)


# ── run_chat ─────────────────────────────────────────────────


class TestRunChat:
    @pytest.mark.asyncio
    async def test_success_minimal_metadata(self):
        """Happy path: minimal metadata, returns RunResponse with run_id."""
        api_key = _make_api_key_record()
        ctx = _make_context()
        req = _make_run_request()

        mock_runner = AsyncMock()
        mock_runner.chat = AsyncMock(return_value="run-abc123")

        result = await run_chat(
            request=req,
            api_key_record=api_key,
            context=ctx,
            bot_runner=mock_runner,
        )

        assert result.code == 0
        assert result.message == "success"
        assert result.data.run_id == "run-abc123"

    @pytest.mark.asyncio
    async def test_metadata_none_passed_as_empty_dict(self):
        """request.metadata is None → treated as {}."""
        api_key = _make_api_key_record()
        ctx = _make_context()
        req = _make_run_request(metadata=None)

        mock_runner = AsyncMock()
        mock_runner.chat = AsyncMock(return_value="run-xyz")

        result = await run_chat(
            request=req,
            api_key_record=api_key,
            context=ctx,
            bot_runner=mock_runner,
        )

        assert result.code == 0
        assert result.data.run_id == "run-xyz"

    @pytest.mark.asyncio
    async def test_lifecycle_stage_from_metadata_bot_options(self):
        """lifecycle_stage extracted from metadata.bot_options dict."""
        api_key = _make_api_key_record()
        ctx = _make_context()
        req = _make_run_request(metadata={"bot_options": {"lifecycle_stage": "draft"}})

        mock_runner = AsyncMock()
        mock_runner.chat = AsyncMock(return_value="run-draft")

        result = await run_chat(
            request=req,
            api_key_record=api_key,
            context=ctx,
            bot_runner=mock_runner,
        )

        assert result.data.run_id == "run-draft"

    @pytest.mark.asyncio
    async def test_lifecycle_stage_bot_options_not_dict(self):
        """bot_options is not a dict → lifecycle_stage is None."""
        api_key = _make_api_key_record()
        ctx = _make_context()
        req = _make_run_request(metadata={"bot_options": "not-a-dict"})

        mock_runner = AsyncMock()
        mock_runner.chat = AsyncMock(return_value="run-bad")

        result = await run_chat(
            request=req,
            api_key_record=api_key,
            context=ctx,
            bot_runner=mock_runner,
        )

        assert result.code == 0
        assert result.data.run_id == "run-bad"

    @pytest.mark.asyncio
    async def test_bot_not_found_returns_404(self):
        """BotNotFoundError → 404."""
        api_key = _make_api_key_record()
        ctx = _make_context()
        req = _make_run_request()

        mock_runner = AsyncMock()
        mock_runner.chat = AsyncMock(side_effect=BotNotFoundError("bot-1"))

        with pytest.raises(HTTPException) as exc:
            await run_chat(
                request=req,
                api_key_record=api_key,
                context=ctx,
                bot_runner=mock_runner,
            )

        assert exc.value.status_code == status.HTTP_404_NOT_FOUND
        assert exc.value.detail["code"] == 60001

    @pytest.mark.asyncio
    async def test_bot_not_available_returns_503(self):
        """BotNotAvailableError → 503."""
        api_key = _make_api_key_record()
        ctx = _make_context()
        req = _make_run_request()

        mock_runner = AsyncMock()
        mock_runner.chat = AsyncMock(
            side_effect=BotNotAvailableError("bot-1", "INACTIVE")
        )

        with pytest.raises(HTTPException) as exc:
            await run_chat(
                request=req,
                api_key_record=api_key,
                context=ctx,
                bot_runner=mock_runner,
            )

        assert exc.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert exc.value.detail["code"] == 60001

    @pytest.mark.asyncio
    async def test_bot_service_error_returns_400(self):
        """BotServiceError → 400."""
        api_key = _make_api_key_record()
        ctx = _make_context()
        req = _make_run_request()

        mock_runner = AsyncMock()
        mock_runner.chat = AsyncMock(side_effect=BotServiceError("tenant is required"))

        with pytest.raises(HTTPException) as exc:
            await run_chat(
                request=req,
                api_key_record=api_key,
                context=ctx,
                bot_runner=mock_runner,
            )

        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
        assert exc.value.detail["code"] == OpenAPICode.BUSINESS_ERROR

    @pytest.mark.asyncio
    async def test_generic_exception_returns_500(self):
        """Unexpected Exception → 500."""
        api_key = _make_api_key_record()
        ctx = _make_context()
        req = _make_run_request()

        mock_runner = AsyncMock()
        mock_runner.chat = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(HTTPException) as exc:
            await run_chat(
                request=req,
                api_key_record=api_key,
                context=ctx,
                bot_runner=mock_runner,
            )

        assert exc.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Internal server error" in exc.value.detail["message"]


# ── get_run_result ───────────────────────────────────────────


class TestGetRunResult:
    """Tests for get_run_result — all branches (lines 148-199)."""

    @pytest.mark.asyncio
    async def test_success_full_data(self):
        """Happy path: matching record with result_content, result_extra, session_id."""
        api_key = _make_api_key_record()
        record = _make_run_record()
        mock_runner = MagicMock()
        mock_runner.get_result = MagicMock(return_value=record)

        result = await get_run_result(
            run_id="run-001",
            api_key_record=api_key,
            bot_runner=mock_runner,
        )
        assert result.code == 0
        assert result.message == "success"
        assert result.data.run_id == "run-001"
        assert result.data.bot_id == "bot-1:entity-1"
        assert result.data.session_id == "sess-1"
        assert result.data.status == "COMPLETED"
        assert result.data.result is not None
        assert result.data.result.content == "reply"
        assert result.data.result.extra is not None
        assert result.data.result.extra.usage == {
            "prompt_tokens": 10,
            "completion_tokens": 5,
        }
        assert result.data.error is None

    @pytest.mark.asyncio
    async def test_auth_fail_api_key_prefix_mismatch(self):
        """api_key_prefix mismatch → returns business error (not 404)."""
        api_key = _make_api_key_record(api_key_prefix="kp-999")  # different
        record = _make_run_record(api_key_prefix="kp-001")  # stored
        mock_runner = MagicMock()
        mock_runner.get_result = MagicMock(return_value=record)

        result = await get_run_result(
            run_id="run-001",
            api_key_record=api_key,
            bot_runner=mock_runner,
        )
        assert result.code == OpenAPICode.BUSINESS_ERROR
        assert "Run not found" in result.message
        assert result.data is None

    @pytest.mark.asyncio
    async def test_auth_fail_bot_id_mismatch(self):
        """bot_id mismatch → returns business error (not 404)."""
        api_key = _make_api_key_record(app_id="bot-other:entity-2")  # different
        record = _make_run_record(bot_id="bot-1:entity-1")  # stored
        mock_runner = MagicMock()
        mock_runner.get_result = MagicMock(return_value=record)

        result = await get_run_result(
            run_id="run-001",
            api_key_record=api_key,
            bot_runner=mock_runner,
        )
        assert result.code == OpenAPICode.BUSINESS_ERROR
        assert "Run not found" in result.message
        assert result.data is None

    @pytest.mark.asyncio
    async def test_session_id_from_result_extra_fallback(self):
        """session_id comes from result_extra when metadata has no session_id."""
        api_key = _make_api_key_record()
        record = _make_run_record(
            metadata=None,  # no metadata at all
            result_extra={
                "session_id": "sess-from-extra",
                "usage": {"prompt_tokens": 5},
            },
        )
        mock_runner = MagicMock()
        mock_runner.get_result = MagicMock(return_value=record)

        result = await get_run_result(
            run_id="run-001",
            api_key_record=api_key,
            bot_runner=mock_runner,
        )
        assert result.code == 0
        assert result.data.session_id == "sess-from-extra"

    @pytest.mark.asyncio
    async def test_session_id_from_metadata_when_result_extra_lacks_it(self):
        """session_id from metadata even when result_extra exists but has no session_id."""
        api_key = _make_api_key_record()
        record = _make_run_record(
            metadata={"session_id": "sess-from-meta"},
            result_extra={"usage": {"prompt_tokens": 5}},
        )
        mock_runner = MagicMock()
        mock_runner.get_result = MagicMock(return_value=record)

        result = await get_run_result(
            run_id="run-001",
            api_key_record=api_key,
            bot_runner=mock_runner,
        )
        assert result.code == 0
        assert result.data.session_id == "sess-from-meta"

    @pytest.mark.asyncio
    async def test_session_id_empty_when_neither_has_it(self):
        """session_id is '' when metadata is None and result_extra is None."""
        api_key = _make_api_key_record()
        record = _make_run_record(
            metadata=None,
            result_extra=None,
            result_content=None,
        )
        mock_runner = MagicMock()
        mock_runner.get_result = MagicMock(return_value=record)

        result = await get_run_result(
            run_id="run-001",
            api_key_record=api_key,
            bot_runner=mock_runner,
        )
        assert result.code == 0
        assert result.data.session_id == ""

    @pytest.mark.asyncio
    async def test_session_id_empty_when_metadata_lacks_session_id_and_no_result_extra(
        self,
    ):
        """session_id is '' when metadata dict has no 'session_id' key."""
        api_key = _make_api_key_record()
        record = _make_run_record(
            metadata={"other_key": "value"},
            result_extra=None,
            result_content=None,
        )
        mock_runner = MagicMock()
        mock_runner.get_result = MagicMock(return_value=record)

        result = await get_run_result(
            run_id="run-001",
            api_key_record=api_key,
            bot_runner=mock_runner,
        )
        assert result.code == 0
        assert result.data.session_id == ""

    @pytest.mark.asyncio
    async def test_result_data_none_when_result_content_is_none(self):
        """result_data is None when result_content is None."""
        api_key = _make_api_key_record()
        record = _make_run_record(
            result_content=None,
            result_extra=None,
            metadata=None,
        )
        mock_runner = MagicMock()
        mock_runner.get_result = MagicMock(return_value=record)

        result = await get_run_result(
            run_id="run-001",
            api_key_record=api_key,
            bot_runner=mock_runner,
        )
        assert result.code == 0
        assert result.data.result is None

    @pytest.mark.asyncio
    async def test_result_data_none_when_result_content_is_empty_string(self):
        """result_data is None when result_content is '' (falsy)."""
        api_key = _make_api_key_record()
        record = _make_run_record(
            result_content="",
            result_extra=None,
            metadata=None,
        )
        mock_runner = MagicMock()
        mock_runner.get_result = MagicMock(return_value=record)

        result = await get_run_result(
            run_id="run-001",
            api_key_record=api_key,
            bot_runner=mock_runner,
        )
        assert result.code == 0
        assert result.data.result is None

    @pytest.mark.asyncio
    async def test_result_data_with_content_no_extra(self):
        """result_data has content but extra is None when result_extra is None."""
        api_key = _make_api_key_record()
        record = _make_run_record(
            result_content="reply text",
            result_content_long="reply text long",
            result_extra=None,
            metadata=None,
        )
        mock_runner = MagicMock()
        mock_runner.get_result = MagicMock(return_value=record)

        result = await get_run_result(
            run_id="run-001",
            api_key_record=api_key,
            bot_runner=mock_runner,
        )
        assert result.code == 0
        assert result.data.result is not None
        assert result.data.result.content == "reply text"
        assert result.data.result.extra is None

    @pytest.mark.asyncio
    async def test_key_error_returns_404(self):
        """KeyError from get_by_run_id → 404 HTTPException."""
        api_key = _make_api_key_record()
        mock_runner = MagicMock()
        mock_runner.get_result = MagicMock(side_effect=KeyError("run-404"))

        with pytest.raises(HTTPException) as exc:
            await get_run_result(
                run_id="run-404",
                api_key_record=api_key,
                bot_runner=mock_runner,
            )
        assert exc.value.status_code == status.HTTP_404_NOT_FOUND
        assert exc.value.detail["code"] == 40401
        assert "run-404" in exc.value.detail["message"]


# ── cancel_run ───────────────────────────────────────────────


class TestCancelRun:
    """Tests for cancel_run endpoint — POST /openapi/v1/runs/{run_id}/cancel."""

    @pytest.mark.asyncio
    async def test_cancel_success_returns_aborted(self):
        """Happy path: matching ownership, PENDING run → 200, code=0, status=ABORTED."""
        from datetime import datetime

        api_key = _make_api_key_record()
        pending_record = _make_run_record(status="PENDING")
        aborted_record = _make_run_record(status="ABORTED", completed_at=datetime.now())

        mock_runner = MagicMock()
        mock_runner.get_result = MagicMock(return_value=pending_record)
        mock_runner.cancel_run = AsyncMock(return_value=aborted_record)

        result = await cancel_run(
            run_id="run-001",
            api_key_record=api_key,
            bot_runner=mock_runner,
        )

        assert result.code == 0
        assert result.message == "success"
        assert result.data.run_id == "run-001"
        assert result.data.bot_id == "bot-1:entity-1"
        assert result.data.session_id == "sess-1"
        assert result.data.status == "ABORTED"
        assert result.data.completed_at is not None
        mock_runner.cancel_run.assert_awaited_once_with(run_id="run-001")

    @pytest.mark.asyncio
    async def test_cancel_terminal_idempotent(self):
        """Already-COMPLETED run → returns COMPLETED status, cancel_run still invoked
        (idempotency is enforced inside BotRunner.cancel_run, not the router)."""
        api_key = _make_api_key_record()
        completed_record = _make_run_record(status="COMPLETED")

        mock_runner = MagicMock()
        mock_runner.get_result = MagicMock(return_value=completed_record)
        mock_runner.cancel_run = AsyncMock(return_value=completed_record)

        result = await cancel_run(
            run_id="run-001",
            api_key_record=api_key,
            bot_runner=mock_runner,
        )

        assert result.code == 0
        assert result.data.status == "COMPLETED"
        mock_runner.cancel_run.assert_awaited_once_with(run_id="run-001")

    @pytest.mark.asyncio
    async def test_cancel_not_found_returns_404(self):
        """KeyError from get_result → 404 HTTPException."""
        api_key = _make_api_key_record()
        mock_runner = MagicMock()
        mock_runner.get_result = MagicMock(side_effect=KeyError("run-404"))
        mock_runner.cancel_run = AsyncMock()

        with pytest.raises(HTTPException) as exc:
            await cancel_run(
                run_id="run-404",
                api_key_record=api_key,
                bot_runner=mock_runner,
            )

        assert exc.value.status_code == status.HTTP_404_NOT_FOUND
        assert exc.value.detail["code"] == 40401
        assert "run-404" in exc.value.detail["message"]
        # cancel_run must not be awaited when the run cannot be found
        mock_runner.cancel_run.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancel_ownership_mismatch_api_key_prefix(self):
        """api_key_prefix mismatch → business error, data=None (no existence leak)."""
        api_key = _make_api_key_record(api_key_prefix="kp-999")
        record = _make_run_record(api_key_prefix="kp-001")

        mock_runner = MagicMock()
        mock_runner.get_result = MagicMock(return_value=record)
        mock_runner.cancel_run = AsyncMock()

        result = await cancel_run(
            run_id="run-001",
            api_key_record=api_key,
            bot_runner=mock_runner,
        )

        assert result.code == OpenAPICode.BUSINESS_ERROR
        assert "Run not found" in result.message
        assert result.data is None
        # Must NOT abort a run the caller doesn't own
        mock_runner.cancel_run.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancel_ownership_mismatch_bot_id(self):
        """bot_id mismatch → business error, data=None."""
        api_key = _make_api_key_record(app_id="bot-other:entity-2")
        record = _make_run_record(bot_id="bot-1:entity-1")

        mock_runner = MagicMock()
        mock_runner.get_result = MagicMock(return_value=record)
        mock_runner.cancel_run = AsyncMock()

        result = await cancel_run(
            run_id="run-001",
            api_key_record=api_key,
            bot_runner=mock_runner,
        )

        assert result.code == OpenAPICode.BUSINESS_ERROR
        assert "Run not found" in result.message
        assert result.data is None
        mock_runner.cancel_run.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancel_bot_service_error_returns_400(self):
        """BotServiceError from cancel_run → 400."""
        api_key = _make_api_key_record()
        record = _make_run_record(status="PENDING")

        mock_runner = MagicMock()
        mock_runner.get_result = MagicMock(return_value=record)
        mock_runner.cancel_run = AsyncMock(side_effect=BotServiceError("engine down"))

        with pytest.raises(HTTPException) as exc:
            await cancel_run(
                run_id="run-001",
                api_key_record=api_key,
                bot_runner=mock_runner,
            )

        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
        assert exc.value.detail["code"] == OpenAPICode.BUSINESS_ERROR

    @pytest.mark.asyncio
    async def test_cancel_unexpected_error_returns_500(self):
        """Unexpected Exception from cancel_run → 500."""
        api_key = _make_api_key_record()
        record = _make_run_record(status="PENDING")

        mock_runner = MagicMock()
        mock_runner.get_result = MagicMock(return_value=record)
        mock_runner.cancel_run = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(HTTPException) as exc:
            await cancel_run(
                run_id="run-001",
                api_key_record=api_key,
                bot_runner=mock_runner,
            )

        assert exc.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Internal server error" in exc.value.detail["message"]

    @pytest.mark.asyncio
    async def test_cancel_session_id_empty_when_neither_has_it(self):
        """session_id is '' when metadata and result_extra both lack it."""
        api_key = _make_api_key_record()
        record = _make_run_record(status="PENDING")
        # Override the fixture's record to have no session_id anywhere
        record.metadata = {"other": "value"}
        record.result_extra = None
        aborted_record = _make_run_record(
            status="ABORTED", metadata={"other": "value"}, result_extra=None
        )

        mock_runner = MagicMock()
        mock_runner.get_result = MagicMock(return_value=record)
        mock_runner.cancel_run = AsyncMock(return_value=aborted_record)

        result = await cancel_run(
            run_id="run-001",
            api_key_record=api_key,
            bot_runner=mock_runner,
        )

        assert result.code == 0
        assert result.data.session_id == ""
        assert result.data.status == "ABORTED"
