"""API integration tests for bot_chat router."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from agentclaw.community.core.bot_chat.schemas import (
    ConversationSession,
    ConversationDetail,
    ConversationObservation,
    SessionListResponse,
    SessionMetadata,
    HealthCheckData,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_service():
    """Mock BotChatService passed as the ``service`` DI parameter to router fns."""
    return MagicMock()


@pytest.fixture
def mock_user():
    """Create a mock AuthenticatedIdentity."""
    user = MagicMock()
    user.staffId = "361618"
    user.userId = "361618"
    return user


@pytest.fixture
def sample_sessions():
    return [
        ConversationSession(
            id="trace-1",
            name="Test Session",
            input="hello",
            status="SUCCESS",
            timestamp="2025-01-15T10:00:00Z",
            user_id="361618",
            metadata=SessionMetadata(attributes={"identity.bot_id": "bot_a"}),
            total_cost=0.01,
            latency_ms=2000,
            total_tokens=100,
        )
    ]


# ---------------------------------------------------------------------------
# GET /api/v1/bot-chats — list sessions
# ---------------------------------------------------------------------------

class TestListSessions:

    @pytest.mark.asyncio
    async def test_product_query_parameters_are_forwarded(self, mock_service, mock_user):
        from agentclaw.community.adapters.http.bot_chat.router import list_sessions

        mock_service.list_sessions = AsyncMock(
            return_value=SessionListResponse(
                sessions=[],
                total=0,
                page=1,
                limit=20,
                has_more=False,
            )
        )

        await list_sessions(
            service=mock_service,
            user=mock_user,
            owner_id=None,
            bot_id=None,
            trace_id=None,
            session_id=None,
            session_key=None,
            query="fixture",
            biz_scene="scene_fixture",
            biz_task_id="task_fixture",
            group_id="group_fixture",
            match_mode="contains",
            include_output_match=True,
            time_scope="default",
            from_date=None,
            to_date=None,
            page=1,
            limit=20,
            log_source=None,
        )

        kwargs = mock_service.list_sessions.call_args.kwargs
        assert kwargs["biz_scene"] == "scene_fixture"
        assert kwargs["biz_task_id"] == "task_fixture"
        assert kwargs["group_id"] == "group_fixture"
        assert kwargs["match_mode"] == "contains"
        assert kwargs["include_output_match"] is True
        assert kwargs["time_scope"] == "default"

    @pytest.mark.asyncio
    async def test_list_sessions_success(self, mock_service, mock_user, sample_sessions):
        from agentclaw.community.adapters.http.bot_chat.router import list_sessions

        mock_service.list_sessions = AsyncMock(return_value=SessionListResponse(
            sessions=sample_sessions,
            total=1,
            page=1,
            limit=20,
            has_more=False,
        ))

        result = await list_sessions(
            service=mock_service,
            owner_id=None,
            bot_id=None,
            trace_id=None,
            session_id=None,
            session_key=None,
            query=None,
            from_date=None,
            to_date=None,
            page=1,
            limit=20,
            user=mock_user,
        )

        assert result.success is True
        assert result.data.total == 1
        assert len(result.data.sessions) == 1
        # Verify owner_id defaults to user's staffId
        mock_service.list_sessions.assert_called_once()
        call_kwargs = mock_service.list_sessions.call_args
        assert call_kwargs.kwargs.get("owner_id") == "361618" or call_kwargs[1].get("owner_id") == "361618"

    @pytest.mark.asyncio
    async def test_list_sessions_with_filters(self, mock_service, mock_user):
        from agentclaw.community.adapters.http.bot_chat.router import list_sessions

        mock_service.list_sessions = AsyncMock(return_value=SessionListResponse(
            sessions=[],
            total=0,
            page=1,
            limit=20,
            has_more=False,
        ))

        await list_sessions(
            service=mock_service,
            owner_id="custom_owner",
            bot_id="bot_123",
            trace_id=None,
            session_id="gen-ai-sess-456",
            session_key=None,
            query=None,
            from_date=None,
            to_date=None,
            page=1,
            limit=20,
            user=mock_user,
        )

        call_kwargs = mock_service.list_sessions.call_args.kwargs
        assert call_kwargs["owner_id"] == "custom_owner"
        assert call_kwargs["bot_id"] == "bot_123"
        assert call_kwargs["session_id"] == "gen-ai-sess-456"

    @pytest.mark.asyncio
    async def test_list_sessions_with_query(self, mock_service, mock_user, sample_sessions):
        from agentclaw.community.adapters.http.bot_chat.router import list_sessions

        mock_service.list_sessions = AsyncMock(return_value=SessionListResponse(
            sessions=[sample_sessions[0]],
            total=1,
            page=1,
            limit=20,
            has_more=False,
        ))

        result = await list_sessions(
            service=mock_service,
            owner_id=None,
            bot_id=None,
            trace_id=None,
            session_id=None,
            session_key=None,
            query="test",
            from_date=None,
            to_date=None,
            page=1,
            limit=20,
            user=mock_user,
        )

        assert result.success is True
        call_kwargs = mock_service.list_sessions.call_args.kwargs
        assert call_kwargs["query"] == "test"

    @pytest.mark.asyncio
    async def test_list_sessions_with_session_id(self, mock_service, mock_user):
        """Test that session_id (gen_ai.session.id) is passed to service."""
        from agentclaw.community.adapters.http.bot_chat.router import list_sessions

        mock_service.list_sessions = AsyncMock(return_value=SessionListResponse(
            sessions=[],
            total=0,
            page=1,
            limit=20,
            has_more=False,
        ))

        await list_sessions(
            service=mock_service,
            owner_id=None,
            bot_id=None,
            trace_id=None,
            session_id="120e838c-011c-4e72-a744-8ca165a2ccdb",
            session_key=None,
            query=None,
            from_date=None,
            to_date=None,
            page=1,
            limit=20,
            user=mock_user,
        )

        call_kwargs = mock_service.list_sessions.call_args.kwargs
        assert call_kwargs["session_id"] == "120e838c-011c-4e72-a744-8ca165a2ccdb"
        assert call_kwargs["session_key"] is None

    @pytest.mark.asyncio
    async def test_list_sessions_with_session_key(self, mock_service, mock_user):
        """Test that session_key (OpenClaw session key) is passed to service."""
        from agentclaw.community.adapters.http.bot_chat.router import list_sessions

        mock_service.list_sessions = AsyncMock(return_value=SessionListResponse(
            sessions=[],
            total=0,
            page=1,
            limit=20,
            has_more=False,
        ))

        await list_sessions(
            service=mock_service,
            owner_id=None,
            bot_id=None,
            trace_id=None,
            session_id=None,
            session_key="agent:main:cron:00000000-0000-0000-0000-000000000000",
            query=None,
            from_date=None,
            to_date=None,
            page=1,
            limit=20,
            user=mock_user,
        )

        call_kwargs = mock_service.list_sessions.call_args.kwargs
        assert call_kwargs["session_key"] == "agent:main:cron:00000000-0000-0000-0000-000000000000"
        assert call_kwargs["session_id"] is None

    @pytest.mark.asyncio
    async def test_list_sessions_langfuse_error(self, mock_service, mock_user):
        from agentclaw.community.adapters.http.bot_chat.router import list_sessions
        from agentclaw.community.core.bot_chat.errors import LangfuseAPIError

        mock_service.list_sessions = AsyncMock(side_effect=LangfuseAPIError("API error", status_code=503))

        result = await list_sessions(
            service=mock_service,
            owner_id=None, bot_id=None, trace_id=None, session_id=None, query=None,
            from_date=None, to_date=None, page=1, limit=20, user=mock_user,
        )

        assert result.success is False
        assert result.error_code == 5999


# ---------------------------------------------------------------------------
# GET /api/v1/bot-chats/{trace_id} — get session detail
# ---------------------------------------------------------------------------

class TestGetSession:

    @pytest.mark.asyncio
    async def test_get_session_success(self, mock_service, mock_user):
        from agentclaw.community.adapters.http.bot_chat.router import get_session

        detail = ConversationDetail(
            id="trace-1",
            name="Detail Session",
            status="SUCCESS",
            timestamp="2025-01-15T10:00:00Z",
            user_id="361618",
            observations=[
                ConversationObservation(
                    id="obs-1",
                    name="llm_call",
                    type="GENERATION",
                    model_name="gpt-4",
                ),
            ],
        )
        mock_service.get_session = AsyncMock(return_value=detail)

        result = await get_session(trace_id="trace-1", user=mock_user, service=mock_service)

        assert result.success is True
        assert result.data.id == "trace-1"
        assert len(result.data.observations) == 1

    @pytest.mark.asyncio
    async def test_get_session_not_found(self, mock_service, mock_user):
        from agentclaw.community.adapters.http.bot_chat.router import get_session
        from agentclaw.community.core.bot_chat.errors import SessionNotFoundError

        mock_service.get_session = AsyncMock(side_effect=SessionNotFoundError("not found"))

        result = await get_session(trace_id="nonexistent", user=mock_user, service=mock_service)

        assert result.success is False
        assert result.error_code == 4004


# ---------------------------------------------------------------------------
# GET /api/v1/bot-chats/health — health check (unauthenticated)
# ---------------------------------------------------------------------------

class TestHealthCheck:

    @pytest.mark.asyncio
    async def test_health_check_healthy(self, mock_service):
        from agentclaw.community.adapters.http.bot_chat.router import health_check

        mock_service.health_check = AsyncMock(return_value=HealthCheckData(
            status="healthy",
            langfuse_url="https://example.com",
        ))

        result = await health_check(service=mock_service)

        assert result.success is True
        assert result.data.status == "healthy"

    @pytest.mark.asyncio
    async def test_health_check_unhealthy(self, mock_service):
        from agentclaw.community.adapters.http.bot_chat.router import health_check

        mock_service.health_check = AsyncMock(return_value=HealthCheckData(
            status="unhealthy",
            langfuse_url="https://example.com",
            error="Connection refused",
        ))

        result = await health_check(service=mock_service)

        assert result.success is False
        assert result.data.status == "unhealthy"
