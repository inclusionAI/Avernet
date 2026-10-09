# Copyright (c) 2004-2026, Ant Group.
# All Rights Reserved.

"""Unit tests for HttpCallback (PostRunCallback implementation)."""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from secbaas.community.core.repository.bot_run import BotRunRecord
from secbaas.community.core.service.callback import (
    CallbackPayload,
    CallbackResult,
    HttpCallback,
)

# ── helpers ──────────────────────────────────────────────────


def _make_record(**overrides):
    defaults = {
        "id": 1,
        "gmt_create": datetime.now(),
        "gmt_modified": datetime.now(),
        "run_id": "run-001",
        "bot_id": "bot-1",
        "api_key_prefix": "kp-001",
        "message": "hello",
        "message_long": "hello",
        "metadata": {"callback_url": "http://example.com/cb"},
        "status": "COMPLETED",
        "result_content": "reply",
        "result_content_long": "reply content",
        "result_extra": {"session_id": "sess-001", "usage": {"prompt_tokens": 10}},
        "error": None,
        "completed_at": datetime.now(),
    }
    defaults.update(overrides)
    return BotRunRecord(**defaults)


def _make_repo(record=None):
    repo = MagicMock()
    repo.get_by_run_id = MagicMock(return_value=record)
    return repo


def _make_response(status_code=200, text="OK"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


# ── __call__ ─────────────────────────────────────────────────


class TestHttpCallbackCall:
    @pytest.mark.asyncio
    async def test_record_not_found(self):
        repo = _make_repo(record=None)
        cb = HttpCallback(repo)
        await cb("nonexistent-run-id")
        repo.get_by_run_id.assert_called_once_with(run_id="nonexistent-run-id")

    @pytest.mark.asyncio
    async def test_no_callback_url_in_metadata(self):
        repo = _make_repo(record=_make_record(metadata={"other": "val"}))
        cb = HttpCallback(repo)
        with patch.object(cb, "_send", new=AsyncMock()) as mock_send:
            await cb("run-001")
        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_none_metadata(self):
        repo = _make_repo(record=_make_record(metadata=None))
        cb = HttpCallback(repo)
        with patch.object(cb, "_send", new=AsyncMock()) as mock_send:
            await cb("run-001")
        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_callback_url(self):
        repo = _make_repo(record=_make_record(metadata={"callback_url": ""}))
        cb = HttpCallback(repo)
        with patch.object(cb, "_send", new=AsyncMock()) as mock_send:
            await cb("run-001")
        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_successful_send(self):
        record = _make_record()
        repo = _make_repo(record=record)
        cb = HttpCallback(repo)
        mock_result = CallbackResult(success=True, status_code=200, message="")
        with patch.object(
            cb, "_send", new=AsyncMock(return_value=mock_result)
        ) as mock_send:
            await cb("run-001")

        mock_send.assert_awaited_once()
        url, payload = mock_send.call_args[0]
        assert url == "http://example.com/cb"
        assert isinstance(payload, CallbackPayload)
        assert payload.run_id == "run-001"
        assert payload.bot_id == "bot-1"
        assert payload.status == "COMPLETED"
        assert payload.result == "reply"
        assert payload.error is None
        assert payload.metadata == {"callback_url": "http://example.com/cb"}
        assert payload.session_id == "sess-001"

    @pytest.mark.asyncio
    async def test_session_id_from_result_extra(self):
        record = _make_record(result_extra={"session_id": "sess-xyz", "other": 1})
        repo = _make_repo(record=record)
        cb = HttpCallback(repo)
        mock_result = CallbackResult(success=True, status_code=200, message="")
        with patch.object(
            cb, "_send", new=AsyncMock(return_value=mock_result)
        ) as mock_send:
            await cb("run-001")

        payload = mock_send.call_args[0][1]
        assert payload.session_id == "sess-xyz"

    @pytest.mark.asyncio
    async def test_session_id_none_when_result_extra_missing_session_id(self):
        record = _make_record(result_extra={"usage": {"prompt_tokens": 10}})
        repo = _make_repo(record=record)
        cb = HttpCallback(repo)
        mock_result = CallbackResult(success=True, status_code=200, message="")
        with patch.object(
            cb, "_send", new=AsyncMock(return_value=mock_result)
        ) as mock_send:
            await cb("run-001")

        payload = mock_send.call_args[0][1]
        assert payload.session_id is None

    @pytest.mark.asyncio
    async def test_session_id_none_when_result_extra_is_none(self):
        record = _make_record(result_extra=None)
        repo = _make_repo(record=record)
        cb = HttpCallback(repo)
        mock_result = CallbackResult(success=True, status_code=200, message="")
        with patch.object(
            cb, "_send", new=AsyncMock(return_value=mock_result)
        ) as mock_send:
            await cb("run-001")

        payload = mock_send.call_args[0][1]
        assert payload.session_id is None

    @pytest.mark.asyncio
    async def test_send_failure_logs_error(self):
        record = _make_record()
        repo = _make_repo(record=record)
        cb = HttpCallback(repo)
        mock_result = CallbackResult(
            success=False, status_code=500, message="server error"
        )
        with patch.object(cb, "_send", new=AsyncMock(return_value=mock_result)):
            await cb("run-001")


# ── _send (retry logic) ──────────────────────────────────────


class TestHttpCallbackSend:
    @pytest.mark.asyncio
    async def test_2xx_no_retry(self):
        cb = HttpCallback(_make_repo())
        first = CallbackResult(success=True, status_code=200, message="")
        with patch.object(
            cb, "_send_once", new=AsyncMock(return_value=first)
        ) as mock_once:
            result = await cb._send(
                "http://example.com/cb",
                CallbackPayload(
                    run_id="r1",
                    bot_id="b1",
                    status="COMPLETED",
                ),
            )
        mock_once.assert_awaited_once()
        assert result.success is True

    @pytest.mark.asyncio
    async def test_4xx_no_retry(self):
        cb = HttpCallback(_make_repo())
        first = CallbackResult(success=False, status_code=404, message="not found")
        with patch.object(
            cb, "_send_once", new=AsyncMock(return_value=first)
        ) as mock_once:
            result = await cb._send(
                "http://example.com/cb",
                CallbackPayload(
                    run_id="r1",
                    bot_id="b1",
                    status="COMPLETED",
                ),
            )
        mock_once.assert_awaited_once()
        assert result.status_code == 404

    @pytest.mark.asyncio
    async def test_5xx_retries_once(self):
        cb = HttpCallback(_make_repo())
        first = CallbackResult(success=False, status_code=503, message="unavailable")
        second = CallbackResult(success=True, status_code=200, message="")
        with patch.object(
            cb, "_send_once", new=AsyncMock(side_effect=[first, second])
        ) as mock_once:
            result = await cb._send(
                "http://example.com/cb",
                CallbackPayload(
                    run_id="r1",
                    bot_id="b1",
                    status="COMPLETED",
                ),
            )
        assert mock_once.await_count == 2
        assert result.success is True
        assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_5xx_retry_also_fails(self):
        cb = HttpCallback(_make_repo())
        first = CallbackResult(success=False, status_code=500, message="err1")
        second = CallbackResult(success=False, status_code=500, message="err2")
        with patch.object(
            cb, "_send_once", new=AsyncMock(side_effect=[first, second])
        ) as mock_once:
            result = await cb._send(
                "http://example.com/cb",
                CallbackPayload(
                    run_id="r1",
                    bot_id="b1",
                    status="COMPLETED",
                ),
            )
        assert mock_once.await_count == 2
        assert result.success is False
        assert result.status_code == 500

    @pytest.mark.asyncio
    async def test_none_status_code_no_retry(self):
        cb = HttpCallback(_make_repo())
        first = CallbackResult(success=False, status_code=None, message="network error")
        with patch.object(
            cb, "_send_once", new=AsyncMock(return_value=first)
        ) as mock_once:
            result = await cb._send(
                "http://example.com/cb",
                CallbackPayload(
                    run_id="r1",
                    bot_id="b1",
                    status="COMPLETED",
                ),
            )
        mock_once.assert_awaited_once()
        assert result.success is False
        assert result.status_code is None


# ── _send_once (HTTP POST) ───────────────────────────────────


class TestHttpCallbackSendOnce:
    @pytest.mark.asyncio
    async def test_success_2xx(self):
        cb = HttpCallback(_make_repo())
        mock_resp = _make_response(status_code=200, text="OK")
        with patch(
            "secbaas.community.core.service.callback._http_callback.httpx.AsyncClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            result = await cb._send_once(
                "http://example.com/cb",
                CallbackPayload(
                    run_id="r1",
                    bot_id="b1",
                    status="COMPLETED",
                ),
            )

        assert result.success is True
        assert result.status_code == 200
        assert result.message == ""

    @pytest.mark.asyncio
    async def test_success_201(self):
        cb = HttpCallback(_make_repo())
        mock_resp = _make_response(status_code=201, text="Created")
        with patch(
            "secbaas.community.core.service.callback._http_callback.httpx.AsyncClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            result = await cb._send_once(
                "http://example.com/cb",
                CallbackPayload(
                    run_id="r1",
                    bot_id="b1",
                    status="COMPLETED",
                ),
            )

        assert result.success is True
        assert result.status_code == 201

    @pytest.mark.asyncio
    async def test_4xx_failure(self):
        cb = HttpCallback(_make_repo())
        mock_resp = _make_response(status_code=404, text="Not Found")
        with patch(
            "secbaas.community.core.service.callback._http_callback.httpx.AsyncClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            result = await cb._send_once(
                "http://example.com/cb",
                CallbackPayload(
                    run_id="r1",
                    bot_id="b1",
                    status="COMPLETED",
                ),
            )

        assert result.success is False
        assert result.status_code == 404
        assert "HTTP 404" in result.message

    @pytest.mark.asyncio
    async def test_5xx_failure(self):
        cb = HttpCallback(_make_repo())
        mock_resp = _make_response(status_code=503, text="Service Unavailable")
        with patch(
            "secbaas.community.core.service.callback._http_callback.httpx.AsyncClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            result = await cb._send_once(
                "http://example.com/cb",
                CallbackPayload(
                    run_id="r1",
                    bot_id="b1",
                    status="COMPLETED",
                ),
            )

        assert result.success is False
        assert result.status_code == 503
        assert "HTTP 503" in result.message

    @pytest.mark.asyncio
    async def test_http_error_exception(self):
        cb = HttpCallback(_make_repo())
        with patch(
            "secbaas.community.core.service.callback._http_callback.httpx.AsyncClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(
                side_effect=httpx.ConnectError("connection refused")
            )
            mock_client_cls.return_value = mock_client

            result = await cb._send_once(
                "http://example.com/cb",
                CallbackPayload(
                    run_id="r1",
                    bot_id="b1",
                    status="COMPLETED",
                ),
            )

        assert result.success is False
        assert result.status_code is None
        assert "connection refused" in result.message

    @pytest.mark.asyncio
    async def test_timeout_error_exception(self):
        cb = HttpCallback(_make_repo())
        with patch(
            "secbaas.community.core.service.callback._http_callback.httpx.AsyncClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(side_effect=httpx.ReadTimeout("read timeout"))
            mock_client_cls.return_value = mock_client

            result = await cb._send_once(
                "http://example.com/cb",
                CallbackPayload(
                    run_id="r1",
                    bot_id="b1",
                    status="COMPLETED",
                ),
            )

        assert result.success is False
        assert result.status_code is None
        assert "read timeout" in result.message

    @pytest.mark.asyncio
    async def test_payload_serialized_as_json(self):
        cb = HttpCallback(_make_repo())
        mock_resp = _make_response(status_code=200, text="OK")
        payload = CallbackPayload(
            run_id="r1",
            bot_id="b1",
            status="COMPLETED",
            result="done",
            error=None,
            metadata={"key": "val"},
            session_id="s-1",
        )
        with patch(
            "secbaas.community.core.service.callback._http_callback.httpx.AsyncClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            await cb._send_once("http://example.com/cb", payload)

        post_kwargs = mock_client.post.call_args.kwargs
        body = json.loads(post_kwargs["content"])
        assert body["run_id"] == "r1"
        assert body["bot_id"] == "b1"
        assert body["status"] == "COMPLETED"
        assert body["result"] == "done"
        assert body["error"] is None
        assert body["metadata"] == {"key": "val"}
        assert body["session_id"] == "s-1"
        assert post_kwargs["headers"]["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_custom_timeout(self):
        cb = HttpCallback(_make_repo(), default_timeout=30.0)
        mock_resp = _make_response(status_code=200, text="OK")
        with patch(
            "secbaas.community.core.service.callback._http_callback.httpx.AsyncClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            await cb._send_once(
                "http://example.com/cb",
                CallbackPayload(
                    run_id="r1",
                    bot_id="b1",
                    status="COMPLETED",
                ),
            )

        mock_client_cls.assert_called_once_with(timeout=30.0)


# ── origin / Origin header / response body log ──────────────


class TestHttpCallbackOrigin:
    @pytest.mark.asyncio
    async def test_origin_header_set_when_origin_provided(self):
        cb = HttpCallback(_make_repo(), origin="https://my.origin")
        mock_resp = _make_response(status_code=200, text="OK")
        with patch(
            "secbaas.community.core.service.callback._http_callback.httpx.AsyncClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            await cb._send_once(
                "http://example.com/cb",
                CallbackPayload(
                    run_id="r1",
                    bot_id="b1",
                    status="COMPLETED",
                ),
            )

        headers = mock_client.post.call_args.kwargs["headers"]
        assert headers["Origin"] == "https://my.origin"
        assert headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_no_origin_header_when_origin_is_none(self):
        cb = HttpCallback(_make_repo())
        mock_resp = _make_response(status_code=200, text="OK")
        with patch(
            "secbaas.community.core.service.callback._http_callback.httpx.AsyncClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            await cb._send_once(
                "http://example.com/cb",
                CallbackPayload(
                    run_id="r1",
                    bot_id="b1",
                    status="COMPLETED",
                ),
            )

        headers = mock_client.post.call_args.kwargs["headers"]
        assert "Origin" not in headers

    @pytest.mark.asyncio
    async def test_response_body_logged(self):
        cb = HttpCallback(_make_repo())
        mock_resp = _make_response(status_code=200, text="all good")
        with (
            patch(
                "secbaas.community.core.service.callback._http_callback.httpx.AsyncClient"
            ) as mock_client_cls,
            patch(
                "secbaas.community.core.service.callback._http_callback.logger"
            ) as mock_logger,
        ):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            await cb._send_once(
                "http://example.com/cb",
                CallbackPayload(
                    run_id="r1",
                    bot_id="b1",
                    status="COMPLETED",
                ),
            )

        resp_log_call = mock_logger.info.call_args_list[0]
        assert (
            resp_log_call.args[0]
            == "[callback] response: run_id=%s, url=%s, status=%s, body=%s"
        )
        assert "all good" in resp_log_call.args

    @pytest.mark.asyncio
    async def test_response_body_logged_on_failure(self):
        cb = HttpCallback(_make_repo())
        mock_resp = _make_response(status_code=500, text="server error")
        with (
            patch(
                "secbaas.community.core.service.callback._http_callback.httpx.AsyncClient"
            ) as mock_client_cls,
            patch(
                "secbaas.community.core.service.callback._http_callback.logger"
            ) as mock_logger,
        ):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            await cb._send_once(
                "http://example.com/cb",
                CallbackPayload(
                    run_id="r1",
                    bot_id="b1",
                    status="COMPLETED",
                ),
            )

        log_messages = [call.args[0] for call in mock_logger.info.call_args_list]
        assert any("response:" in msg for msg in log_messages)
        assert any(
            "failed:" in call.args[0] for call in mock_logger.error.call_args_list
        )
