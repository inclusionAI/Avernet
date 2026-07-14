"""Unit tests for plugins/auth/oauth/_oauth_client.py."""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from secbaas.community.plugins.auth.oauth._oauth_client import OAuthClient
from secbaas.community.spi.auth import AuthUser


class TestOAuthClientInit:
    @patch("secbaas.community.config.ConfigLoader.load")
    def test_defaults_when_no_config(self, mock_load, monkeypatch):
        monkeypatch.delenv("BCS_PORT", raising=False)
        mock_cfg = MagicMock()
        mock_cfg.user_config = {}
        mock_load.return_value = mock_cfg

        client = OAuthClient()
        assert client._base_url == "http://127.0.0.1:21000"
        assert client._user_path == "/auth/user"
        assert client._timeout == 10

    @patch("secbaas.community.config.ConfigLoader.load")
    def test_uses_bcs_port_env_for_default_base_url(self, mock_load, monkeypatch):
        monkeypatch.setenv("BCS_PORT", "29999")
        mock_cfg = MagicMock()
        mock_cfg.user_config = {}
        mock_load.return_value = mock_cfg

        client = OAuthClient()
        assert client._base_url == "http://127.0.0.1:29999"

    @patch("secbaas.community.config.ConfigLoader.load")
    def test_uses_custom_config(self, mock_load):
        mock_cfg = MagicMock()
        mock_cfg.user_config = {
            "oauth": {
                "base_url": "http://bcs.example.com:21000",
                "user_path": "/custom/user",
                "timeout": 5,
            }
        }
        mock_load.return_value = mock_cfg

        client = OAuthClient()
        assert client._base_url == "http://bcs.example.com:21000"
        assert client._user_path == "/custom/user"
        assert client._timeout == 5


class _FakeResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        return self._json


class _FakeAsyncClient:
    """Fake httpx.AsyncClient: async ctx manager + recording .get()."""

    def __init__(self, response=None, exc=None, captured=None):
        self._response = response
        self._exc = exc
        self._captured = captured if captured is not None else {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, headers=None):
        self._captured["url"] = url
        self._captured["headers"] = headers
        if self._exc is not None:
            raise self._exc
        return self._response


def _make_client():
    with patch("secbaas.community.config.ConfigLoader.load") as mock_load:
        mock_cfg = MagicMock()
        mock_cfg.user_config = {
            "oauth": {"base_url": "http://bcs.local:21000", "user_path": "/auth/user"}
        }
        mock_load.return_value = mock_cfg
        return OAuthClient()


class TestGetLoginUser:
    @pytest.mark.asyncio
    async def test_missing_cookie_raises_401(self):
        client = _make_client()
        with pytest.raises(HTTPException) as exc:
            await client.get_login_user(cookie=None)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_success_maps_user_fields(self):
        client = _make_client()
        resp = _FakeResponse(
            200,
            {
                "user_id": "u-123",
                "name": "Alice",
                "provider": "google",
                "avatar": "http://a/avatar.png",
            },
        )
        captured = {}
        fake = _FakeAsyncClient(response=resp, captured=captured)
        with patch(
            "secbaas.community.plugins.auth.oauth._oauth_client.httpx.AsyncClient",
            return_value=fake,
        ):
            user = await client.get_login_user(cookie="bcs_session=jwt; other=1")

        assert isinstance(user, AuthUser)
        assert user.id == "u-123"
        assert user.staffId == "u-123"
        assert user.operatorName == "u-123"
        assert user.nickName == "Alice"
        assert captured["url"] == "http://bcs.local:21000/auth/user"
        assert captured["headers"]["Cookie"] == "bcs_session=jwt; other=1"

    @pytest.mark.asyncio
    async def test_success_without_name(self):
        client = _make_client()
        resp = _FakeResponse(200, {"user_id": "u-1", "name": None, "provider": "x"})
        with patch(
            "secbaas.community.plugins.auth.oauth._oauth_client.httpx.AsyncClient",
            return_value=_FakeAsyncClient(response=resp),
        ):
            user = await client.get_login_user(cookie="bcs_session=jwt")
        assert user.nickName is None

    @pytest.mark.asyncio
    async def test_http_401_raises_http_exception(self):
        client = _make_client()
        resp = _FakeResponse(401, {"error": "not authenticated"})
        with patch(
            "secbaas.community.plugins.auth.oauth._oauth_client.httpx.AsyncClient",
            return_value=_FakeAsyncClient(response=resp),
        ):
            with pytest.raises(HTTPException) as exc:
                await client.get_login_user(cookie="bcs_session=bad")
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_http_404_raises_runtime_error(self):
        client = _make_client()
        resp = _FakeResponse(404, text="not found")
        with patch(
            "secbaas.community.plugins.auth.oauth._oauth_client.httpx.AsyncClient",
            return_value=_FakeAsyncClient(response=resp),
        ):
            with pytest.raises(RuntimeError, match="bcs auth http error 404"):
                await client.get_login_user(cookie="bcs_session=jwt")

    @pytest.mark.asyncio
    async def test_missing_user_id_raises_runtime_error(self):
        client = _make_client()
        resp = _FakeResponse(200, {"user_id": None, "name": "x"})
        with patch(
            "secbaas.community.plugins.auth.oauth._oauth_client.httpx.AsyncClient",
            return_value=_FakeAsyncClient(response=resp),
        ):
            with pytest.raises(RuntimeError, match="missing user_id"):
                await client.get_login_user(cookie="bcs_session=jwt")

    @pytest.mark.asyncio
    async def test_network_error_raises_runtime_error(self):
        client = _make_client()
        fake = _FakeAsyncClient(exc=httpx.ConnectError("refused"))
        with patch(
            "secbaas.community.plugins.auth.oauth._oauth_client.httpx.AsyncClient",
            return_value=fake,
        ):
            with pytest.raises(RuntimeError, match="bcs auth request error"):
                await client.get_login_user(cookie="bcs_session=jwt")

    @pytest.mark.asyncio
    async def test_invalid_json_body_raises_runtime_error(self):
        client = _make_client()

        class _BadJsonResponse:
            status_code = 200
            text = "<html>not json</html>"

            def json(self):
                raise ValueError("Expecting value")

        with patch(
            "secbaas.community.plugins.auth.oauth._oauth_client.httpx.AsyncClient",
            return_value=_FakeAsyncClient(response=_BadJsonResponse()),
        ):
            with pytest.raises(RuntimeError, match="bcs auth invalid json"):
                await client.get_login_user(cookie="bcs_session=jwt")

    @pytest.mark.asyncio
    async def test_non_object_json_body_raises_runtime_error(self):
        client = _make_client()
        resp = _FakeResponse(200, json_data=["not", "a", "dict"])
        with patch(
            "secbaas.community.plugins.auth.oauth._oauth_client.httpx.AsyncClient",
            return_value=_FakeAsyncClient(response=resp),
        ):
            with pytest.raises(RuntimeError, match="response not an object"):
                await client.get_login_user(cookie="bcs_session=jwt")
