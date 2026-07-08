"""Unit tests for AsyncSessionClient.

Covers:
- SessionInfo dataclass: creation, defaults, optional fields
- MessageInfo dataclass: creation, defaults
- AsyncSessionClient.__init__: default engine, trailing slash stripping
- AsyncSessionClient._get_session: create, reuse, recreate on close
- AsyncSessionClient._request: success, HTTP error, success=false
- AsyncSessionClient.create_session: success, no data returned
- AsyncSessionClient.get_session: found, not found
- AsyncSessionClient.list_sessions: with/without filters, empty list
- AsyncSessionClient.update_session: success
- AsyncSessionClient.delete_session: success
- AsyncSessionClient.get_messages: success, empty, with offset
- AsyncSessionClient.clear_messages: success
- AsyncSessionClient.close: normal close, already closed, no session
- AsyncSessionClient.__aenter__ / __aexit__: context manager
"""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from secbaas.core.service.bot_run._async_session_client import (
    AsyncSessionClient,
    MessageInfo,
    SessionInfo,
)

# ==================== Helpers ====================


def _make_mock_session(status: int, json_body: dict) -> AsyncMock:
    """Create a mock aiohttp.ClientSession that returns the given response.

    Mocks session.request to return an async context manager whose __aenter__
    returns a mock response with .status and .json().
    """
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.json = AsyncMock(return_value=json_body)

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_session = AsyncMock()
    mock_session.closed = False
    mock_session.request = MagicMock(return_value=mock_ctx)

    return mock_session


def _make_session_data(**overrides) -> dict:
    defaults = {
        "id": "session-001",
        "title": "Test Session",
        "user_id": "user-123",
        "agent_id": "agent-456",
        "model": "gpt-4",
        "gmt_created": "2024-01-01T00:00:00",
        "gmt_modified": "2024-01-01T01:00:00",
        "message_count": 5,
        "last_message": {"role": "user", "content": "hello"},
    }
    defaults.update(overrides)
    return defaults


def _make_message_data(**overrides) -> dict:
    defaults = {
        "id": "msg-001",
        "session_id": "session-001",
        "role": "user",
        "content": "Hello, world",
        "metadata": {"key": "value"},
        "gmt_created": "2024-01-01T00:00:00",
        "history_meta": {"turn": 1},
    }
    defaults.update(overrides)
    return defaults


# ==================== SessionInfo Tests ====================


class TestSessionInfo:
    """Tests for SessionInfo dataclass."""

    def test_creation_with_required_fields(self):
        """SessionInfo with only required fields (id, title)."""
        info = SessionInfo(id="s1", title="My Session")
        assert info.id == "s1"
        assert info.title == "My Session"
        assert info.user_id is None
        assert info.agent_id is None
        assert info.model is None
        assert info.created_at is None
        assert info.updated_at is None
        assert info.message_count == 0
        assert info.last_message is None

    def test_creation_with_all_fields(self):
        """SessionInfo with all fields populated."""
        info = SessionInfo(
            id="s1",
            title="Full Session",
            user_id="u1",
            agent_id="a1",
            model="claude-3",
            created_at="2024-01-01",
            updated_at="2024-01-02",
            message_count=10,
            last_message={"role": "assistant", "content": "done"},
        )
        assert info.id == "s1"
        assert info.title == "Full Session"
        assert info.user_id == "u1"
        assert info.agent_id == "a1"
        assert info.model == "claude-3"
        assert info.created_at == "2024-01-01"
        assert info.updated_at == "2024-01-02"
        assert info.message_count == 10
        assert info.last_message == {"role": "assistant", "content": "done"}

    def test_message_count_defaults_to_zero(self):
        """message_count defaults to 0."""
        info = SessionInfo(id="s1", title="T")
        assert info.message_count == 0

    def test_last_message_defaults_to_none(self):
        """last_message defaults to None."""
        info = SessionInfo(id="s1", title="T")
        assert info.last_message is None

    def test_all_optional_fields_default_to_none(self):
        """Optional fields default to None."""
        info = SessionInfo(id="s1", title="T")
        assert info.user_id is None
        assert info.agent_id is None
        assert info.model is None
        assert info.created_at is None
        assert info.updated_at is None


# ==================== MessageInfo Tests ====================


class TestMessageInfo:
    """Tests for MessageInfo dataclass."""

    def test_creation_with_required_fields(self):
        """MessageInfo with only required fields (id, session_id, role, content)."""
        info = MessageInfo(id="m1", session_id="s1", role="user", content="hello")
        assert info.id == "m1"
        assert info.session_id == "s1"
        assert info.role == "user"
        assert info.content == "hello"
        assert info.meta is None
        assert info.created_at is None
        assert info.history_meta is None

    def test_creation_with_all_fields(self):
        """MessageInfo with all fields populated."""
        info = MessageInfo(
            id="m1",
            session_id="s1",
            role="assistant",
            content="Hi there",
            meta={"source": "api"},
            created_at="2024-01-01",
            history_meta={"version": 2},
        )
        assert info.id == "m1"
        assert info.session_id == "s1"
        assert info.role == "assistant"
        assert info.content == "Hi there"
        assert info.meta == {"source": "api"}
        assert info.created_at == "2024-01-01"
        assert info.history_meta == {"version": 2}

    def test_optional_fields_default_to_none(self):
        """Optional fields default to None."""
        info = MessageInfo(id="m1", session_id="s1", role="user", content="hello")
        assert info.meta is None
        assert info.created_at is None
        assert info.history_meta is None


# ==================== AsyncSessionClient.__init__ Tests ====================


class TestAsyncSessionClientInit:
    """Tests for AsyncSessionClient.__init__."""

    def test_default_initialization(self):
        """Default initialization with base_url only."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        assert client.base_url == "https://api.example.com"
        assert client.headers == {}
        assert client.timeout == 30.0
        assert client.engine is None
        assert client._session is None

    def test_custom_headers_and_timeout(self):
        """Custom headers and timeout."""
        headers = {"Authorization": "Bearer token"}
        client = AsyncSessionClient(
            base_url="https://api.example.com",
            headers=headers,
            timeout=60.0,
        )
        assert client.headers == headers
        assert client.timeout == 60.0

    def test_custom_engine(self):
        """Custom engine parameter."""
        client = AsyncSessionClient(
            base_url="https://api.example.com",
            engine="openclaw",
        )
        assert client.engine == "openclaw"

    def test_strips_trailing_slash(self):
        """Trailing slash in base_url is stripped."""
        client = AsyncSessionClient(base_url="https://api.example.com/")
        assert client.base_url == "https://api.example.com"

    def test_multiple_trailing_slashes_stripped(self):
        """Multiple trailing slashes are stripped."""
        client = AsyncSessionClient(base_url="https://api.example.com///")
        assert client.base_url == "https://api.example.com"

    def test_no_trailing_slash_unchanged(self):
        """No trailing slash, base_url unchanged."""
        client = AsyncSessionClient(base_url="https://api.example.com/api")
        assert client.base_url == "https://api.example.com/api"


# ==================== AsyncSessionClient._get_session Tests ====================


class TestGetSession:
    """Tests for AsyncSessionClient._get_session."""

    @pytest.mark.asyncio
    async def test_creates_new_session(self):
        """_get_session creates a new aiohttp.ClientSession when none exists."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        with patch("aiohttp.ClientSession") as mock_cls:
            mock_session = AsyncMock()
            mock_cls.return_value = mock_session

            result = await client._get_session()

            mock_cls.assert_called_once()
            assert result is mock_session
            assert client._session is mock_session

    @pytest.mark.asyncio
    async def test_reuses_existing_session(self):
        """_get_session reuses existing session if not closed."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        mock_session = AsyncMock()
        mock_session.closed = False
        client._session = mock_session

        with patch("aiohttp.ClientSession") as mock_cls:
            result = await client._get_session()

            mock_cls.assert_not_called()
            assert result is mock_session

    @pytest.mark.asyncio
    async def test_creates_new_when_closed(self):
        """_get_session creates new session when existing one is closed."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        old_session = AsyncMock()
        old_session.closed = True
        client._session = old_session

        new_session = AsyncMock()
        with patch("aiohttp.ClientSession", return_value=new_session):
            result = await client._get_session()

            assert result is new_session
            assert client._session is new_session

    @pytest.mark.asyncio
    async def test_passes_headers_and_timeout(self):
        """_get_session passes headers and timeout to ClientSession."""
        headers = {"X-Custom": "value"}
        client = AsyncSessionClient(
            base_url="https://api.example.com",
            headers=headers,
            timeout=15.0,
        )

        with patch("aiohttp.ClientSession") as mock_cls:
            mock_session = AsyncMock()
            mock_cls.return_value = mock_session
            await client._get_session()

            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["headers"] == headers
            assert isinstance(call_kwargs["timeout"], aiohttp.ClientTimeout)
            assert call_kwargs["timeout"].total == 15.0


# ==================== AsyncSessionClient._request Tests ====================


class TestRequest:
    """Tests for AsyncSessionClient._request."""

    @pytest.mark.asyncio
    async def test_successful_get_request(self):
        """_request returns body on successful GET with success=true."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        mock_session = _make_mock_session(
            status=200, json_body={"success": True, "data": {"key": "value"}}
        )
        client._session = mock_session

        with patch(
            "secbaas.core.service.bot_run._async_session_client.is_dev",
            return_value=False,
        ):
            result = await client._request("GET", "/api/test")

        assert result == {"success": True, "data": {"key": "value"}}
        mock_session.request.assert_called_once_with(
            "GET", "https://api.example.com/api/test", json=None, headers=None
        )

    @pytest.mark.asyncio
    async def test_successful_post_request_with_body(self):
        """_request sends JSON body on POST request."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        mock_session = _make_mock_session(status=200, json_body={"success": True})
        client._session = mock_session

        body = {"title": "New Session"}
        with patch(
            "secbaas.core.service.bot_run._async_session_client.is_dev",
            return_value=False,
        ):
            result = await client._request("POST", "/api/sessions", json=body)

        assert result == {"success": True}
        mock_session.request.assert_called_once_with(
            "POST", "https://api.example.com/api/sessions", json=body, headers=None
        )

    @pytest.mark.asyncio
    async def test_http_error_raises_client_response_error(self):
        """_request raises aiohttp.ClientResponseError on HTTP 400+ status."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        mock_session = _make_mock_session(
            status=400, json_body={"detail": "Bad request"}
        )
        client._session = mock_session

        with pytest.raises(aiohttp.ClientResponseError) as exc_info:
            await client._request("GET", "/api/test")
        assert exc_info.value.status == 400
        assert "Bad request" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_http_error_falls_back_to_str_body(self):
        """_request uses str(body) when response has no detail field."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        mock_session = _make_mock_session(
            status=500, json_body={"error_code": "SERVER_ERROR"}
        )
        client._session = mock_session

        with pytest.raises(aiohttp.ClientResponseError) as exc_info:
            await client._request("GET", "/api/test")
        assert exc_info.value.status == 500

    @pytest.mark.asyncio
    async def test_success_false_raises_runtime_error(self):
        """_request raises RuntimeError when success=false in response body."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        mock_session = _make_mock_session(
            status=200, json_body={"success": False, "message": "Invalid request"}
        )
        client._session = mock_session

        with pytest.raises(RuntimeError, match="Invalid request"):
            await client._request("GET", "/api/test")

    @pytest.mark.asyncio
    async def test_success_false_default_message(self):
        """_request uses 'Unknown error' when success=false and no message."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        mock_session = _make_mock_session(status=200, json_body={"success": False})
        client._session = mock_session

        with pytest.raises(RuntimeError, match="Unknown error"):
            await client._request("GET", "/api/test")


# ==================== AsyncSessionClient.create_session Tests ====================


class TestCreateSession:
    """Tests for AsyncSessionClient.create_session."""

    @pytest.mark.asyncio
    async def test_successful_create(self):
        """create_session returns SessionInfo on success."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        session_data = _make_session_data()
        mock_session = _make_mock_session(
            status=200,
            json_body={"success": True, "data": session_data},
        )
        client._session = mock_session

        result = await client.create_session(
            title="Test Session",
            user_id="user-123",
            model="gpt-4",
        )

        assert isinstance(result, SessionInfo)
        assert result.id == "session-001"
        assert result.title == "Test Session"
        assert result.user_id == "user-123"
        assert result.model == "gpt-4"
        assert result.message_count == 5

        # Verify POST request
        mock_session.request.assert_called_once()
        call_args = mock_session.request.call_args
        assert call_args[0][0] == "POST"
        assert "api/sessions" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_create_session_with_engine(self):
        """create_session includes engine in POST body."""
        client = AsyncSessionClient(
            base_url="https://api.example.com", engine="openclaw"
        )
        session_data = _make_session_data()
        mock_session = _make_mock_session(
            status=200,
            json_body={"success": True, "data": session_data},
        )
        client._session = mock_session

        await client.create_session(title="Test")

        call_args = mock_session.request.call_args
        sent_json = call_args.kwargs["json"]
        assert sent_json["engine"] == "openclaw"

    @pytest.mark.asyncio
    async def test_create_session_method_engine_overrides_instance_engine(self):
        """Method-level engine parameter overrides instance engine."""
        client = AsyncSessionClient(
            base_url="https://api.example.com", engine="openclaw"
        )
        session_data = _make_session_data()
        mock_session = _make_mock_session(
            status=200,
            json_body={"success": True, "data": session_data},
        )
        client._session = mock_session

        await client.create_session(title="Test", engine="claude")

        call_args = mock_session.request.call_args
        sent_json = call_args.kwargs["json"]
        assert sent_json["engine"] == "claude"

    @pytest.mark.asyncio
    async def test_create_session_no_data_raises_runtime_error(self):
        """create_session raises RuntimeError when response has no data."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        mock_session = _make_mock_session(
            status=200,
            json_body={"success": True, "data": None},
        )
        client._session = mock_session

        with pytest.raises(RuntimeError, match="No session data returned"):
            await client.create_session(title="Test")

    @pytest.mark.asyncio
    async def test_create_session_none_fields_not_sent(self):
        """None values are stripped from the request body."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        session_data = _make_session_data()
        mock_session = _make_mock_session(
            status=200,
            json_body={"success": True, "data": session_data},
        )
        client._session = mock_session

        await client.create_session(title="Test")  # no user_id, agent_id, model, engine

        call_args = mock_session.request.call_args
        sent_json = call_args.kwargs["json"]
        assert "user_id" not in sent_json
        assert "agent_id" not in sent_json
        assert "model" not in sent_json
        assert "engine" not in sent_json
        assert sent_json["title"] == "Test"


# ==================== AsyncSessionClient.get_session Tests ====================


class TestGetSessionInfo:
    """Tests for AsyncSessionClient.get_session."""

    @pytest.mark.asyncio
    async def test_get_session_success(self):
        """get_session returns SessionInfo for a valid session_id."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        session_data = _make_session_data(id="session-target")
        mock_session = _make_mock_session(
            status=200,
            json_body={"success": True, "data": session_data},
        )
        client._session = mock_session

        result = await client.get_session("session-target")

        assert result.id == "session-target"

        # Verify base64 encoding in URL
        encoded = base64.b64encode(b"session-target").decode()
        call_arg = mock_session.request.call_args[0][1]
        assert f"/api/sessions/{encoded}" in call_arg

    @pytest.mark.asyncio
    async def test_get_session_not_found_raises_runtime_error(self):
        """get_session raises RuntimeError when response data is None."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        mock_session = _make_mock_session(
            status=200,
            json_body={"success": True, "data": None},
        )
        client._session = mock_session

        with pytest.raises(RuntimeError, match="No session data returned"):
            await client.get_session("nonexistent")

    @pytest.mark.asyncio
    async def test_get_session_with_engine_param(self):
        """get_session passes engine as query parameter."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        session_data = _make_session_data(id="session-target")
        mock_session = _make_mock_session(
            status=200,
            json_body={"success": True, "data": session_data},
        )
        client._session = mock_session

        await client.get_session("session-target", engine="openclaw")

        call_arg = mock_session.request.call_args[0][1]
        assert "engine=openclaw" in call_arg


# ==================== AsyncSessionClient.list_sessions Tests ====================


class TestListSessions:
    """Tests for AsyncSessionClient.list_sessions."""

    @pytest.mark.asyncio
    async def test_list_with_filters(self):
        """list_sessions returns list of SessionInfo with filters applied."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        session_data = _make_session_data()
        mock_session = _make_mock_session(
            status=200,
            json_body={"success": True, "data": [session_data, session_data]},
        )
        client._session = mock_session

        result = await client.list_sessions(user_id="user-123", limit=10)

        assert len(result) == 2
        assert all(isinstance(s, SessionInfo) for s in result)
        assert result[0].id == "session-001"

        # Verify query parameters
        call_arg = mock_session.request.call_args[0][1]
        assert "user_id=user-123" in call_arg
        assert "limit=10" in call_arg

    @pytest.mark.asyncio
    async def test_list_empty(self):
        """list_sessions returns empty list when no sessions exist."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        mock_session = _make_mock_session(
            status=200,
            json_body={"success": True, "data": []},
        )
        client._session = mock_session

        result = await client.list_sessions()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_with_agent_id_filter(self):
        """list_sessions with agent_id filter."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        mock_session = _make_mock_session(
            status=200,
            json_body={"success": True, "data": []},
        )
        client._session = mock_session

        await client.list_sessions(agent_id="agent-456")

        call_arg = mock_session.request.call_args[0][1]
        assert "agent_id=agent-456" in call_arg

    @pytest.mark.asyncio
    async def test_list_with_offset(self):
        """list_sessions with offset parameter."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        mock_session = _make_mock_session(
            status=200,
            json_body={"success": True, "data": []},
        )
        client._session = mock_session

        await client.list_sessions(offset=10)

        call_arg = mock_session.request.call_args[0][1]
        assert "offset=10" in call_arg

    @pytest.mark.asyncio
    async def test_list_uses_default_engine(self):
        """list_sessions uses instance engine when no engine override provided."""
        client = AsyncSessionClient(
            base_url="https://api.example.com", engine="openclaw"
        )
        mock_session = _make_mock_session(
            status=200,
            json_body={"success": True, "data": []},
        )
        client._session = mock_session

        await client.list_sessions()

        call_arg = mock_session.request.call_args[0][1]
        assert "engine=openclaw" in call_arg


# ==================== AsyncSessionClient.update_session Tests ====================


class TestUpdateSession:
    """Tests for AsyncSessionClient.update_session."""

    @pytest.mark.asyncio
    async def test_update_session_success(self):
        """update_session returns updated SessionInfo."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        updated_data = _make_session_data(title="Updated Title", model="claude-3")
        mock_session = _make_mock_session(
            status=200,
            json_body={"success": True, "data": updated_data},
        )
        client._session = mock_session

        result = await client.update_session(
            session_id="session-001",
            title="Updated Title",
            model="claude-3",
        )

        assert result.title == "Updated Title"
        assert result.model == "claude-3"

        # Verify POST with update path and params
        call_args = mock_session.request.call_args
        assert call_args[0][0] == "POST"
        assert "session-001/update" in call_args[0][1]
        assert "title=Updated Title" in call_args[0][1]
        assert "model=claude-3" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_update_session_strips_none_params(self):
        """update_session strips None values from params."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        session_data = _make_session_data()
        mock_session = _make_mock_session(
            status=200,
            json_body={"success": True, "data": session_data},
        )
        client._session = mock_session

        await client.update_session(session_id="session-001", title="T")

        call_arg = mock_session.request.call_args[0][1]
        assert "model=" not in call_arg

    @pytest.mark.asyncio
    async def test_update_session_no_data_raises_runtime_error(self):
        """update_session raises RuntimeError when response has no data."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        mock_session = _make_mock_session(
            status=200,
            json_body={"success": True, "data": None},
        )
        client._session = mock_session

        with pytest.raises(RuntimeError, match="No session data returned"):
            await client.update_session(session_id="session-001", title="T")


# ==================== AsyncSessionClient.delete_session Tests ====================


class TestDeleteSession:
    """Tests for AsyncSessionClient.delete_session."""

    @pytest.mark.asyncio
    async def test_delete_session_success(self):
        """delete_session returns True on success."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        mock_session = _make_mock_session(
            status=200,
            json_body={"success": True},
        )
        client._session = mock_session

        result = await client.delete_session("session-001")

        assert result is True

        # Verify DELETE request
        call_args = mock_session.request.call_args
        assert call_args[0][0] == "DELETE"
        assert "session-001" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_delete_session_with_force(self):
        """delete_session with force=True includes force in params."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        mock_session = _make_mock_session(
            status=200,
            json_body={"success": True},
        )
        client._session = mock_session

        await client.delete_session("session-001", force=True)

        call_arg = mock_session.request.call_args[0][1]
        assert "force=True" in call_arg


# ==================== AsyncSessionClient.get_messages Tests ====================


class TestGetMessages:
    """Tests for AsyncSessionClient.get_messages."""

    @pytest.mark.asyncio
    async def test_get_messages_success(self):
        """get_messages returns list of MessageInfo."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        message_data = _make_message_data()
        mock_session = _make_mock_session(
            status=200,
            json_body={"success": True, "data": [message_data, message_data]},
        )
        client._session = mock_session

        result = await client.get_messages("session-001")

        assert len(result) == 2
        assert all(isinstance(m, MessageInfo) for m in result)
        assert result[0].id == "msg-001"
        assert result[0].role == "user"
        assert result[0].content == "Hello, world"

    @pytest.mark.asyncio
    async def test_get_messages_empty(self):
        """get_messages returns empty list when no messages."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        mock_session = _make_mock_session(
            status=200,
            json_body={"success": True, "data": []},
        )
        client._session = mock_session

        result = await client.get_messages("session-001")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_messages_with_offset(self):
        """get_messages with offset parameter."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        mock_session = _make_mock_session(
            status=200,
            json_body={"success": True, "data": []},
        )
        client._session = mock_session

        await client.get_messages("session-001", offset=10)

        call_arg = mock_session.request.call_args[0][1]
        assert "offset=10" in call_arg

    @pytest.mark.asyncio
    async def test_get_messages_with_limit(self):
        """get_messages with limit parameter."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        mock_session = _make_mock_session(
            status=200,
            json_body={"success": True, "data": []},
        )
        client._session = mock_session

        await client.get_messages("session-001", limit=5)

        call_arg = mock_session.request.call_args[0][1]
        assert "limit=5" in call_arg

    @pytest.mark.asyncio
    async def test_get_messages_parses_metadata(self):
        """get_messages maps 'metadata' field to MessageInfo.meta."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        message_data = _make_message_data(metadata={"custom": True})
        mock_session = _make_mock_session(
            status=200,
            json_body={"success": True, "data": [message_data]},
        )
        client._session = mock_session

        result = await client.get_messages("session-001")

        assert result[0].meta == {"custom": True}

    @pytest.mark.asyncio
    async def test_get_messages_strips_none_params(self):
        """get_messages strips None values from query params."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        mock_session = _make_mock_session(
            status=200,
            json_body={"success": True, "data": []},
        )
        client._session = mock_session

        await client.get_messages("session-001", limit=None, offset=0)

        call_arg = mock_session.request.call_args[0][1]
        assert "limit=" not in call_arg


# ==================== AsyncSessionClient.clear_messages Tests ====================


class TestClearMessages:
    """Tests for AsyncSessionClient.clear_messages."""

    @pytest.mark.asyncio
    async def test_clear_messages_success(self):
        """clear_messages returns True on success."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        mock_session = _make_mock_session(
            status=200,
            json_body={"success": True},
        )
        client._session = mock_session

        result = await client.clear_messages("session-001")

        assert result is True

        # Verify DELETE request to messages endpoint
        call_args = mock_session.request.call_args
        assert call_args[0][0] == "DELETE"
        assert "session-001/messages" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_clear_messages_with_engine(self):
        """clear_messages includes engine in query parameters."""
        client = AsyncSessionClient(
            base_url="https://api.example.com", engine="openclaw"
        )
        mock_session = _make_mock_session(
            status=200,
            json_body={"success": True},
        )
        client._session = mock_session

        await client.clear_messages("session-001")

        call_arg = mock_session.request.call_args[0][1]
        assert "engine=openclaw" in call_arg


# ==================== AsyncSessionClient.close Tests ====================


class TestClose:
    """Tests for AsyncSessionClient.close."""

    @pytest.mark.asyncio
    async def test_closes_session(self):
        """close() closes the session and sets it to None."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        mock_session = AsyncMock()
        mock_session.closed = False
        client._session = mock_session

        await client.close()

        mock_session.close.assert_awaited_once()
        assert client._session is None

    @pytest.mark.asyncio
    async def test_close_already_closed_does_nothing(self):
        """close() does nothing if session is already closed."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        mock_session = AsyncMock()
        mock_session.closed = True
        client._session = mock_session

        await client.close()

        mock_session.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_close_no_session_does_nothing(self):
        """close() does nothing when no session exists."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        client._session = None

        await client.close()


# ==================== AsyncSessionClient Context Manager Tests ====================


class TestContextManager:
    """Tests for AsyncSessionClient async context manager."""

    @pytest.mark.asyncio
    async def test_aenter_returns_self(self):
        """__aenter__ returns self."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        result = await client.__aenter__()
        assert result is client

    @pytest.mark.asyncio
    async def test_aexit_calls_close(self):
        """__aexit__ calls close() on the client."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        mock_session = AsyncMock()
        mock_session.closed = False
        client._session = mock_session

        await client.__aexit__(None, None, None)

        mock_session.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_aexit_returns_false(self):
        """__aexit__ returns False (does not suppress exceptions)."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        result = await client.__aexit__(None, None, None)
        assert result is False

    @pytest.mark.asyncio
    async def test_aexit_with_exception_still_closes(self):
        """__aexit__ still closes the session even with an exception."""
        client = AsyncSessionClient(base_url="https://api.example.com")
        mock_session = AsyncMock()
        mock_session.closed = False
        client._session = mock_session

        await client.__aexit__(ValueError, ValueError("test"), None)

        mock_session.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_full_context_manager_usage(self):
        """Full async context manager usage pattern."""
        client = AsyncSessionClient(base_url="https://api.example.com")

        async with client as c:
            assert c is client
            mock_session = _make_mock_session(
                status=200,
                json_body={"success": True, "data": _make_session_data()},
            )
            client._session = mock_session
            result = await c.create_session(title="Test")

        assert isinstance(result, SessionInfo)
        assert client._session is None
