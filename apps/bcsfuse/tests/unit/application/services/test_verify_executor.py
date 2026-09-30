"""VerifyExecutor 单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.application.services.verify_executor import VerifyExecutor
from src.domain.models.verify_dto import CapabilityProbes, DimensionProbe, DimensionResult


def _make_probes() -> list[CapabilityProbes]:
    return [
        CapabilityProbes(
            capability_name="coding",
            dimensions=[
                DimensionProbe(dimension="syntax", probe_prompt="Write a decorator"),
                DimensionProbe(dimension="algo", probe_prompt="Implement BFS"),
                DimensionProbe(dimension="debug", probe_prompt="Find the bug"),
            ],
        )
    ]


class TestVerifyExecutorExecute:
    @pytest.mark.asyncio
    async def test_successful_probe_returns_results(self) -> None:
        executor = VerifyExecutor(
            bcn_chat_base_url="https://bcn.example.com",
            bcn_chat_token="test-token",
            timeout=10,
        )

        mock_resp = AsyncMock()
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = lambda: {"response": {"content": "I can code"}}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("src.application.services.verify_executor.httpx.AsyncClient", return_value=mock_client):
            results = await executor.execute("w1", _make_probes())

        assert len(results) == 3
        assert all(not r.failed for r in results)

    @pytest.mark.asyncio
    async def test_empty_response_marks_failed(self) -> None:
        executor = VerifyExecutor(
            bcn_chat_base_url="https://bcn.example.com",
            bcn_chat_token="test-token",
            timeout=10,
        )

        mock_resp = AsyncMock()
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = lambda: {"response": {"content": ""}}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("src.application.services.verify_executor.httpx.AsyncClient", return_value=mock_client):
            results = await executor.execute("w1", _make_probes())

        assert len(results) == 3
        assert all(r.failed for r in results)

    @pytest.mark.asyncio
    async def test_network_error_marks_failed(self) -> None:
        executor = VerifyExecutor(
            bcn_chat_base_url="https://bcn.example.com",
            bcn_chat_token="test-token",
            timeout=10,
        )

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=Exception("connection refused"))

        with patch("src.application.services.verify_executor.httpx.AsyncClient", return_value=mock_client):
            results = await executor.execute("w1", _make_probes())

        assert len(results) == 3
        assert all(r.failed for r in results)


class TestVerifyExecutorInit:
    def test_strips_trailing_slash(self) -> None:
        executor = VerifyExecutor(
            bcn_chat_base_url="https://bcn.example.com/",
            bcn_chat_token="tok",
        )
        assert executor._base_url == "https://bcn.example.com"

    def test_build_headers_with_cookie(self) -> None:
        executor = VerifyExecutor(
            bcn_chat_base_url="https://bcn.example.com",
            bcn_chat_token="",
            bcn_chat_cookie="session=abc123; token=xyz",
        )
        headers = executor._build_headers()
        assert "Cookie" in headers
        assert headers["Cookie"] == "session=abc123; token=xyz"
        assert "Authorization" not in headers

    def test_build_headers_with_token_and_cookie(self) -> None:
        executor = VerifyExecutor(
            bcn_chat_base_url="https://bcn.example.com",
            bcn_chat_token="bearer-tok",
            bcn_chat_cookie="session=abc",
        )
        headers = executor._build_headers()
        assert "Cookie" in headers
        assert "Authorization" in headers

    def test_missing_both_token_and_cookie_skips_probes(self) -> None:
        executor = VerifyExecutor(
            bcn_chat_base_url="https://bcn.example.com",
            bcn_chat_token="",
            bcn_chat_cookie="",
        )
        results = executor.execute("w1", _make_probes())
        # This is async but we can check it returns a coroutine
        import asyncio
        results = asyncio.get_event_loop().run_until_complete(results)
        assert len(results) == 3
        assert all(r.failed for r in results)


class TestVerifyExecutorAuthError:
    @pytest.mark.asyncio
    async def test_bcn_auth_error_marks_failed(self) -> None:
        executor = VerifyExecutor(
            bcn_chat_base_url="https://bcn.example.com",
            bcn_chat_token="bad-token",
            timeout=10,
        )

        mock_resp = AsyncMock()
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = lambda: {
            "buserviceErrorCode": "USER_NOT_LOGIN",
            "actionType": "LOGIN",
            "help": "请先完成身份验证",
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("src.application.services.verify_executor.httpx.AsyncClient", return_value=mock_client):
            results = await executor.execute("w1", _make_probes())

        assert len(results) == 3
        assert all(r.failed for r in results)

    @pytest.mark.asyncio
    async def test_data_field_response(self) -> None:
        executor = VerifyExecutor(
            bcn_chat_base_url="https://bcn.example.com",
            bcn_chat_token="test-token",
            timeout=10,
        )

        mock_resp = AsyncMock()
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = lambda: {"data": {"content": "data field response"}}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("src.application.services.verify_executor.httpx.AsyncClient", return_value=mock_client):
            results = await executor.execute("w1", _make_probes())

        assert len(results) == 3
        assert all(not r.failed for r in results)
        assert results[0].response_content == "data field response"

    @pytest.mark.asyncio
    async def test_missing_base_url_skips_probes(self) -> None:
        executor = VerifyExecutor(
            bcn_chat_base_url="",
            bcn_chat_token="",
            timeout=10,
        )
        results = await executor.execute("w1", _make_probes())
        assert len(results) == 3
        assert all(r.failed for r in results)