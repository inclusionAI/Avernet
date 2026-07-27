"""Tests for BotChatService — response mapping, client-side filtering, and observation tree building."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone

from agentclaw.community.core.bot_chat.service import (
    BotChatService,
    _map_trace_to_session,
    _extract_user_input,
    _map_observation,
    _build_observation_tree,
    _apply_client_side_filters,
    _matches_session_key,
)
from agentclaw.community.core.bot_chat.schemas import ConversationDetail, ConversationObservation, ConversationSession, SessionMetadata
from agentclaw.community.core.bot_chat.errors import SessionNotFoundError, LangfuseAPIError
from agentclaw.community.core.bot_chat.query_support import QueryScope
from agentclaw.community.di.config import BotChatConfig

# Langfuse creds are deployment config (BotChatConfig) now, injected into the
# service; the tests configure a fixed test endpoint/creds via this config.
_TEST_BOTCHAT_CONFIG = BotChatConfig(
    langfuse_base_url="https://langfuse.example.com",
    langfuse_public_key="pk-test",
    langfuse_secret_key="sk-test",
)


# ---------------------------------------------------------------------------
# _extract_user_input
# ---------------------------------------------------------------------------

class TestExtractUserInput:

    def test_string_input(self):
        assert _extract_user_input("hello world") == "hello world"

    def test_none_input(self):
        assert _extract_user_input(None) is None

    def test_list_input_finds_last_user_message(self):
        data = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "second"},
        ]
        assert _extract_user_input(data) == "second"

    def test_list_input_no_user_role(self):
        data = [{"role": "assistant", "content": "hi"}]
        assert _extract_user_input(data) == "hi"

    def test_dict_input_with_content_key(self):
        assert _extract_user_input({"content": "hello"}) == "hello"

    def test_dict_input_without_content_key(self):
        result = _extract_user_input({"key": "val"})
        assert "key" in result

    def test_list_input_content_as_text_array(self):
        data = [
            {"role": "user", "content": [{"type": "text", "text": "hello from array"}]},
        ]
        assert _extract_user_input(data) == "hello from array"


# ---------------------------------------------------------------------------
# _map_trace_to_session
# ---------------------------------------------------------------------------

class TestMapTraceToSession:

    def test_basic_mapping(self):
        trace = {
            "id": "trace-123",
            "name": "My Session",
            "input": [{"role": "user", "content": "hello"}],
            "success": True,
            "timestamp": "2025-01-15T10:00:00Z",
            "userId": "user1",
            "totalCost": 0.005,
            "latency": 1500,
            "usage": {"totalTokens": 500},
            "metadata": {"attributes": {"identity.bot_id": "bot1", "session_id": "sess-1"}},
        }
        session = _map_trace_to_session(trace)
        assert session.id == "trace-123"
        assert session.name == "My Session"
        assert session.input == "hello"
        assert session.status == "SUCCESS"
        assert session.user_id == "user1"
        assert session.total_cost == 0.005
        assert session.latency_ms == 1500
        assert session.total_tokens == 500
        assert session.metadata is not None
        assert session.metadata.attributes.get("identity.bot_id") == "bot1"

    def test_failed_status(self):
        trace = {"id": "t1", "success": False, "timestamp": "2025-01-01T00:00:00Z"}
        session = _map_trace_to_session(trace)
        assert session.status == "FAILED"

    def test_missing_fields_defaults(self):
        trace = {"id": "t1", "timestamp": "2025-01-01T00:00:00Z"}
        session = _map_trace_to_session(trace)
        assert session.name == "未命名会话"
        assert session.total_cost == 0
        assert session.total_tokens == 0


# ---------------------------------------------------------------------------
# _map_observation
# ---------------------------------------------------------------------------

class TestMapObservation:

    def test_basic_mapping(self):
        obs = {
            "id": "obs-1",
            "name": "llm_call",
            "type": "GENERATION",
            "latency": 2000,
            "totalCost": 0.003,
            "usage": {"totalTokens": 300},
            "input": "prompt",
            "output": "response",
            "metadata": {"attributes": {"gen_ai.request.model": "gpt-4"}},
            "parentObservationId": None,
        }
        result = _map_observation(obs)
        assert result.id == "obs-1"
        assert result.type == "GENERATION"
        assert result.model_name == "gpt-4"
        assert result.parent_observation_id is None
        assert result.children == []


# ---------------------------------------------------------------------------
# _build_observation_tree
# ---------------------------------------------------------------------------

class TestBuildObservationTree:

    def test_flat_list_becomes_roots(self):
        observations = [
            {"id": "a", "type": "SPAN", "name": "root1", "parentObservationId": None},
            {"id": "b", "type": "SPAN", "name": "root2", "parentObservationId": None},
        ]
        tree = _build_observation_tree(observations)
        assert len(tree) == 2
        assert tree[0].id == "a"
        assert tree[1].id == "b"

    def test_nested_tree(self):
        observations = [
            {"id": "a", "type": "SPAN", "name": "root", "parentObservationId": None},
            {"id": "b", "type": "GENERATION", "name": "child1", "parentObservationId": "a"},
            {"id": "c", "type": "GENERATION", "name": "child2", "parentObservationId": "a"},
            {"id": "d", "type": "SPAN", "name": "grandchild", "parentObservationId": "b"},
        ]
        tree = _build_observation_tree(observations)
        assert len(tree) == 1
        root = tree[0]
        assert root.id == "a"
        assert len(root.children) == 2
        assert root.children[0].id == "b"
        assert len(root.children[0].children) == 1
        assert root.children[0].children[0].id == "d"

    def test_orphan_goes_to_root(self):
        observations = [
            {"id": "a", "type": "SPAN", "name": "root", "parentObservationId": None},
            {"id": "b", "type": "SPAN", "name": "orphan", "parentObservationId": "nonexistent"},
        ]
        tree = _build_observation_tree(observations)
        assert len(tree) == 2

    def test_empty_list(self):
        assert _build_observation_tree([]) == []


# ---------------------------------------------------------------------------
# _apply_client_side_filters
# ---------------------------------------------------------------------------

class TestApplyClientSideFilters:

    def _make_session(
        self,
        trace_id="t1",
        bot_id=None,
        session_key=None,
        conversation_id=None,
        session_id=None,
    ) -> ConversationSession:
        attrs = {}
        if bot_id:
            attrs["identity.bot_id"] = bot_id
        if session_key:
            attrs["session_id"] = session_key
        if conversation_id:
            attrs["gen_ai.conversation.id"] = conversation_id
        if session_id:
            attrs["gen_ai.session.id"] = session_id
        return ConversationSession(
            id=trace_id,
            name="test",
            timestamp="2025-01-01T00:00:00Z",
            metadata=SessionMetadata(attributes=attrs) if attrs else None,
        )

    def test_no_filters_returns_all(self):
        sessions = [self._make_session("t1"), self._make_session("t2")]
        result = _apply_client_side_filters(sessions, None, None, None, None)
        assert len(result) == 2

    def test_filter_by_trace_id(self):
        sessions = [self._make_session("t1"), self._make_session("t2")]
        result = _apply_client_side_filters(sessions, None, "t1", None, None)
        assert len(result) == 1
        assert result[0].id == "t1"

    def test_filter_by_bot_id(self):
        sessions = [
            self._make_session("t1", bot_id="bot_a"),
            self._make_session("t2", bot_id="bot_b"),
            self._make_session("t3", bot_id=None),
        ]
        result = _apply_client_side_filters(sessions, "bot_a", None, None, None)
        assert len(result) == 1
        assert result[0].id == "t1"

    def test_filter_by_session_id(self):
        """Test filtering by gen_ai.session.id (new session_id parameter)."""
        sessions = [
            self._make_session("t1", session_id="sid-1"),
            self._make_session("t2", session_id="sid-2"),
        ]
        result = _apply_client_side_filters(sessions, None, None, "sid-2", None)
        assert len(result) == 1
        assert result[0].id == "t2"

    def test_filter_by_session_key(self):
        """Test filtering by session_key matching metadata.attributes['session_id']."""
        sessions = [
            self._make_session("t1", session_key="agent:main:cron:s1"),
            self._make_session("t2", session_key="agent:main:cron:s2"),
        ]
        result = _apply_client_side_filters(sessions, None, None, None, "agent:main:cron:s2")
        assert len(result) == 1
        assert result[0].id == "t2"

    def test_session_key_matches_conversation_id(self):
        """Test session_key matching metadata.attributes['gen_ai.conversation.id']."""
        sessions = [
            self._make_session("t1", conversation_id="agent:main:dashboard:s1"),
            self._make_session("t2", conversation_id="agent:main:dashboard:s2"),
        ]
        result = _apply_client_side_filters(sessions, None, None, None, "agent:main:dashboard:s1")
        assert len(result) == 1
        assert result[0].id == "t1"

    def test_session_key_matches_both_fields(self):
        """Test session_key matches either session_id or gen_ai.conversation.id."""
        sessions = [
            self._make_session("t1", session_key="agent:main:cron:s1"),
            self._make_session("t2", conversation_id="agent:main:cron:s1"),
            self._make_session("t3", session_key="agent:main:cron:s2"),
        ]
        # session_key matches both t1 (session_id) and t2 (conversation_id)
        result = _apply_client_side_filters(sessions, None, None, None, "agent:main:cron:s1")
        assert len(result) == 2
        assert {s.id for s in result} == {"t1", "t2"}

    def test_session_key_does_not_match_gen_ai_session_id(self):
        """Test session_key does NOT match metadata.attributes['gen_ai.session.id']."""
        sessions = [
            self._make_session("t1", session_id="gen-ai-sid-1"),
            self._make_session("t2", session_key="gen-ai-sid-1"),
        ]
        # session_key should only match t2 (has session_id attribute)
        # NOT t1 (has gen_ai.session.id attribute)
        result = _apply_client_side_filters(sessions, None, None, None, "gen-ai-sid-1")
        assert len(result) == 1
        assert result[0].id == "t2"

    def test_matches_session_key_helper(self):
        """Test _matches_session_key helper function."""
        # Match session_id field
        assert _matches_session_key({"session_id": "key-1"}, "key-1") is True
        # Match gen_ai.conversation.id field
        assert _matches_session_key({"gen_ai.conversation.id": "key-2"}, "key-2") is True
        # No match
        assert _matches_session_key({"session_id": "key-1"}, "key-2") is False
        # Does not match gen_ai.session.id
        assert _matches_session_key({"gen_ai.session.id": "key-1"}, "key-1") is False
        # Match either field (session_id takes precedence if both present)
        assert _matches_session_key({
            "session_id": "key-1",
            "gen_ai.conversation.id": "key-2"
        }, "key-1") is True
        assert _matches_session_key({
            "session_id": "key-1",
            "gen_ai.conversation.id": "key-2"
        }, "key-2") is True

    def test_session_id_and_session_key_are_separate(self):
        """Test that session_id and session_key filter different attributes."""
        sessions = [
            self._make_session("t1", session_key="agent:main:s1"),
            self._make_session("t2", session_id="gen-ai-sid-1"),
            self._make_session("t3", session_key="agent:main:s1", session_id="gen-ai-sid-1"),
        ]
        # Filter by session_key should only match session with session_id attribute
        result = _apply_client_side_filters(sessions, None, None, None, "agent:main:s1")
        assert len(result) == 2
        assert {s.id for s in result} == {"t1", "t3"}

        # Filter by session_id should only match session with gen_ai.session.id attribute
        result = _apply_client_side_filters(sessions, None, None, "gen-ai-sid-1", None)
        assert len(result) == 2
        assert {s.id for s in result} == {"t2", "t3"}

    def test_combined_filters(self):
        sessions = [
            self._make_session("t1", bot_id="bot_a", session_key="s1"),
            self._make_session("t2", bot_id="bot_a", session_key="s2"),
            self._make_session("t3", bot_id="bot_b", session_key="s1"),
        ]
        result = _apply_client_side_filters(sessions, "bot_a", None, None, "s1")
        assert len(result) == 1
        assert result[0].id == "t1"

    def test_filter_no_match(self):
        sessions = [self._make_session("t1", bot_id="bot_a")]
        result = _apply_client_side_filters(sessions, "bot_nonexistent", None, None, None)
        assert len(result) == 0

    def test_query_match_by_name(self):
        sessions = [
            ConversationSession(id="t1", name="部署方案讨论", input="帮我看看", timestamp="2025-01-01T00:00:00Z"),
            ConversationSession(id="t2", name="日常对话", input="你好", timestamp="2025-01-01T00:00:00Z"),
        ]
        result = _apply_client_side_filters(sessions, None, None, None, None, query="部署")
        assert len(result) == 1
        assert result[0].id == "t1"

    def test_query_match_by_input(self):
        sessions = [
            ConversationSession(id="t1", name="未命名会话", input="报销流程是什么", timestamp="2025-01-01T00:00:00Z"),
            ConversationSession(id="t2", name="其他", input="hello", timestamp="2025-01-01T00:00:00Z"),
        ]
        result = _apply_client_side_filters(sessions, None, None, None, None, query="报销")
        assert len(result) == 1
        assert result[0].id == "t1"

    def test_query_case_insensitive(self):
        sessions = [
            ConversationSession(id="t1", name="API Gateway 配置", input="test", timestamp="2025-01-01T00:00:00Z"),
        ]
        result = _apply_client_side_filters(sessions, None, None, None, None, query="api")
        assert len(result) == 1

    def test_query_no_match(self):
        sessions = [
            ConversationSession(id="t1", name="test", input="hello", timestamp="2025-01-01T00:00:00Z"),
        ]
        result = _apply_client_side_filters(sessions, None, None, None, None, query="不存在的内容")
        assert len(result) == 0

    def test_query_null_input_field(self):
        sessions = [
            ConversationSession(id="t1", name="test session", input=None, timestamp="2025-01-01T00:00:00Z"),
        ]
        result = _apply_client_side_filters(sessions, None, None, None, None, query="test")
        assert len(result) == 1

    def test_query_null_name_field(self):
        sessions = [
            ConversationSession(id="t1", name="", input="search me", timestamp="2025-01-01T00:00:00Z"),
        ]
        result = _apply_client_side_filters(sessions, None, None, None, None, query="search")
        assert len(result) == 1

    def test_query_not_provided(self):
        sessions = [self._make_session("t1"), self._make_session("t2")]
        result = _apply_client_side_filters(sessions, None, None, None, None, query=None)
        assert len(result) == 2

    def test_query_combined_with_bot_id(self):
        sessions = [
            ConversationSession(id="t1", name="部署方案", input="帮我", timestamp="2025-01-01T00:00:00Z",
                                metadata=SessionMetadata(attributes={"identity.bot_id": "bot_a"})),
            ConversationSession(id="t2", name="部署方案", input="帮我", timestamp="2025-01-01T00:00:00Z",
                                metadata=SessionMetadata(attributes={"identity.bot_id": "bot_b"})),
        ]
        result = _apply_client_side_filters(sessions, bot_id="bot_a", trace_id=None, session_id=None, session_key=None, query="部署")
        assert len(result) == 1
        assert result[0].id == "t1"

    def test_session_id_with_bot_id_combined(self):
        """Test that session_id and bot_id must both match."""
        sessions = [
            self._make_session("t1", bot_id="bot_a", session_id="sid-1"),
            self._make_session("t2", bot_id="bot_b", session_id="sid-1"),
            self._make_session("t3", bot_id="bot_a", session_id="sid-2"),
        ]
        result = _apply_client_side_filters(sessions, bot_id="bot_a", trace_id=None, session_id="sid-1", session_key=None)
        assert len(result) == 1
        assert result[0].id == "t1"


# ---------------------------------------------------------------------------
# BotChatService.list_sessions (mocked aiohttp)
# ---------------------------------------------------------------------------

class TestBotChatServiceListSessions:

    @pytest.fixture
    def service(self):
        # Mock DatabasePlugin for testing
        mock_db = MagicMock()
        return BotChatService(db=mock_db, config=_TEST_BOTCHAT_CONFIG)

    @pytest.mark.asyncio
    async def test_list_sessions_defaults_to_db_source(self, service):
        """Default list_sessions should use DB repository, not Langfuse."""
        session = ConversationSession(
            id="trace-db",
            name="DB Session",
            input="hello",
            timestamp="2026-06-01T00:00:00Z",
        )
        service._db_repo = MagicMock()
        service._db_repo.list_ocb_traces.return_value = ([], 0)
        service._db_repo.list_traces.return_value = ([session], 3)

        result = await service.list_sessions(
            owner_id="user1",
            from_date=datetime(2026, 6, 1, tzinfo=timezone.utc),
            to_date=datetime(2026, 6, 2, tzinfo=timezone.utc),
            page=1,
            limit=2,
        )

        assert result.sessions == [session]
        assert result.total == 3
        assert result.has_more is True
        service._db_repo.list_traces.assert_called_once()
        _, kwargs = service._db_repo.list_traces.call_args
        assert kwargs["owner_id"] == "user1"
        assert kwargs["from_ms"] == 1780272000000
        assert kwargs["to_ms"] == 1780358400000

    @pytest.mark.asyncio
    async def test_list_sessions_db_clamps_page_and_limit(self, service):
        service._db_repo = MagicMock()
        service._db_repo.list_ocb_traces.return_value = ([], 0)
        service._db_repo.list_traces.return_value = ([], 0)

        result = await service.list_sessions(
            owner_id="user1",
            page=0,
            limit=999,
        )

        assert result.page == 1
        assert result.limit == 100
        _, kwargs = service._db_repo.list_traces.call_args
        assert kwargs["page"] == 1
        assert kwargs["limit"] == 100

    @pytest.mark.asyncio
    async def test_list_sessions_non_default_bot_denied_before_db_query(self, service):
        service._db_repo = MagicMock()
        service._db_repo.has_bot_access.return_value = False

        result = await service.list_sessions(
            owner_id="user1",
            bot_id="bot-a",
        )

        assert result.sessions == []
        assert result.total == 0
        assert result.has_more is False
        service._db_repo.has_bot_access.assert_called_once_with("user1", "bot-a")
        service._db_repo.list_ocb_traces.assert_not_called()
        service._db_repo.list_traces.assert_not_called()

    @pytest.mark.asyncio
    async def test_list_sessions_non_default_bot_owner_queries_by_bot(self, service):
        session = ConversationSession(
            id="trace-bot",
            name="Bot Session",
            timestamp="2026-06-01T00:00:00Z",
        )
        service._db_repo = MagicMock()
        service._db_repo.has_bot_access.return_value = True
        service._db_repo.list_ocb_traces.return_value = ([], 0)
        service._db_repo.list_traces.return_value = ([session], 1)

        result = await service.list_sessions(
            owner_id="user1",
            bot_id="bot-a",
        )

        assert result.sessions == [session]
        service._db_repo.has_bot_access.assert_called_once_with("user1", "bot-a")
        _, kwargs = service._db_repo.list_traces.call_args
        assert kwargs["bot_id"] == "bot-a"

    @pytest.mark.asyncio
    async def test_list_sessions_non_default_bot_collaborator_queries_by_bot(self, service):
        session = ConversationSession(
            id="trace-bot",
            name="Bot Session",
            timestamp="2026-06-01T00:00:00Z",
        )
        service._db_repo = MagicMock()
        service._db_repo.has_bot_access.return_value = True
        service._db_repo.list_ocb_traces.return_value = ([], 0)
        service._db_repo.list_traces.return_value = ([session], 1)

        result = await service.list_sessions(
            owner_id="collaborator-1",
            bot_id="bot-a",
        )

        assert result.sessions == [session]
        service._db_repo.has_bot_access.assert_called_once_with("collaborator-1", "bot-a")
        _, kwargs = service._db_repo.list_traces.call_args
        assert kwargs["bot_id"] == "bot-a"

    @pytest.mark.asyncio
    async def test_list_sessions_explicit_db_uses_db_source(self, service):
        service._db_repo = MagicMock()
        service._db_repo.list_ocb_traces.return_value = ([], 0)
        service._db_repo.list_traces.return_value = ([], 0)

        result = await service.list_sessions(owner_id="user1", log_source="db")

        assert result.total == 0
        service._db_repo.list_traces.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_sessions_db_does_not_mix_legacy_when_ocb_has_rows(self, service):
        """Dual-written traces must not mix AC OTEL and legacy rows in one page."""
        session = ConversationSession(
            id="trace-ocb",
            name="OCB Session",
            timestamp="2026-06-01T00:00:00Z",
        )
        service._db_repo = MagicMock()
        service._db_repo.list_ocb_traces.return_value = ([session], 3)
        legacy_session = ConversationSession(
            id="trace-legacy",
            timestamp="2026-06-01T00:00:00Z",
        )
        service._db_repo.list_traces.return_value = ([legacy_session], 10)

        result = await service.list_sessions(
            owner_id="user1",
            page=1,
            limit=2,
        )

        assert result.sessions == [session]
        assert result.total == 3
        assert result.has_more is True
        service._db_repo.list_traces.assert_not_called()

    @pytest.mark.asyncio
    async def test_list_sessions_success(self, service):
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "data": [
                {
                    "id": "trace-1",
                    "name": "Test Session",
                    "input": [{"role": "user", "content": "hello"}],
                    "success": True,
                    "timestamp": "2025-01-15T10:00:00Z",
                    "userId": "user1",
                    "totalCost": 0.01,
                    "latency": 2000,
                    "usage": {"totalTokens": 100},
                    "metadata": {"attributes": {}},
                }
            ],
            "meta": {"totalItems": 1},
        })

        mock_session_ctx = AsyncMock()
        mock_session_ctx.get = MagicMock(return_value=mock_response)
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_aiohttp_session = AsyncMock()
        mock_aiohttp_session.get = MagicMock(return_value=mock_session_ctx)
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=False)

        with patch("agentclaw.community.core.bot_chat.service.aiohttp.ClientSession", return_value=mock_aiohttp_session):
            result = await service.list_sessions(owner_id="user1", log_source="langfuse")

        assert result.total == 1
        assert len(result.sessions) == 1
        assert result.sessions[0].id == "trace-1"
        assert result.sessions[0].input == "hello"

    @pytest.mark.asyncio
    async def test_list_sessions_langfuse_error(self, service):
        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="Internal Server Error")

        mock_session_ctx = AsyncMock()
        mock_session_ctx.get = MagicMock(return_value=mock_response)
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_aiohttp_session = AsyncMock()
        mock_aiohttp_session.get = MagicMock(return_value=mock_session_ctx)
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=False)

        with patch("agentclaw.community.core.bot_chat.service.aiohttp.ClientSession", return_value=mock_aiohttp_session):
            with pytest.raises(LangfuseAPIError) as exc_info:
                await service.list_sessions(owner_id="user1", log_source="langfuse")
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_list_sessions_with_query_filter(self, service):
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "data": [
                {
                    "id": "trace-1",
                    "name": "部署方案讨论",
                    "input": [{"role": "user", "content": "帮我看看"}],
                    "success": True,
                    "timestamp": "2025-01-15T10:00:00Z",
                    "userId": "user1",
                    "totalCost": 0.01,
                    "latency": 2000,
                    "usage": {"totalTokens": 100},
                    "metadata": {"attributes": {}},
                },
                {
                    "id": "trace-2",
                    "name": "日常对话",
                    "input": [{"role": "user", "content": "你好"}],
                    "success": True,
                    "timestamp": "2025-01-15T11:00:00Z",
                    "userId": "user1",
                    "totalCost": 0.005,
                    "latency": 1000,
                    "usage": {"totalTokens": 50},
                    "metadata": {"attributes": {}},
                },
            ],
            "meta": {"totalItems": 2},
        })

        mock_session_ctx = AsyncMock()
        mock_session_ctx.get = MagicMock(return_value=mock_response)
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_aiohttp_session = AsyncMock()
        mock_aiohttp_session.get = MagicMock(return_value=mock_session_ctx)
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=False)

        with patch("agentclaw.community.core.bot_chat.service.aiohttp.ClientSession", return_value=mock_aiohttp_session):
            result = await service.list_sessions(owner_id="user1", query="部署", log_source="langfuse")

        assert len(result.sessions) == 1
        assert result.sessions[0].id == "trace-1"
        assert result.total == 1

    @pytest.mark.asyncio
    async def test_list_sessions_with_session_id_exhaustive_scan_page1_miss_page2_hit(self, service):
        """Test that session_id triggers exhaustive scan and finds match on page 2."""
        call_count = 0

        def make_response():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First page: 100 items, no match
                data = [
                    {"id": f"trace-a{i}", "name": f"Session A{i}", "input": "hello",
                     "success": True, "timestamp": "2025-01-15T10:00:00Z", "userId": "user1",
                     "totalCost": 0.01, "latency": 2000, "usage": {"totalTokens": 100},
                     "metadata": {"attributes": {"gen_ai.session.id": "other-session-id"}}}
                    for i in range(100)
                ]
            else:
                # Second page: 50 items, with match (total 150 items)
                data = [
                    {"id": f"trace-b{i}", "name": f"Session B{i}", "input": "world",
                     "success": True, "timestamp": "2025-01-15T11:00:00Z", "userId": "user1",
                     "totalCost": 0.02, "latency": 3000, "usage": {"totalTokens": 200},
                     "metadata": {"attributes": {"gen_ai.session.id": "other-session-id"}}}
                    for i in range(49)
                ] + [
                    {"id": "trace-target", "name": "Target Session", "input": "target",
                     "success": True, "timestamp": "2025-01-15T12:00:00Z", "userId": "user1",
                     "totalCost": 0.03, "latency": 4000, "usage": {"totalTokens": 300},
                     "metadata": {"attributes": {"gen_ai.session.id": "target-session-id"}}}
                ]
            
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={
                "data": data,
                "meta": {"totalItems": 150},
            })
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=False)
            return mock_response

        mock_aiohttp_session = AsyncMock()
        mock_aiohttp_session.get = MagicMock(side_effect=lambda *args, **kwargs: make_response())
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=False)

        with patch("agentclaw.community.core.bot_chat.service.aiohttp.ClientSession", return_value=mock_aiohttp_session):
            result = await service.list_sessions(
                owner_id="user1",
                session_id="target-session-id",
                log_source="langfuse",
            )

        assert call_count == 2  # Should scan 2 pages until all items scanned (150 items / 100 page_size = 2 pages)
        assert len(result.sessions) == 1
        assert result.sessions[0].id == "trace-target"
        assert result.total == 1
        # has_more is False because we scanned all 150 items
        assert result.has_more is False

    @pytest.mark.asyncio
    async def test_list_sessions_with_session_id_exhaustive_scan_no_early_stop(self, service):
        """Test that exhaustive scan does NOT stop early when match is found.

        It should continue scanning until all items are scanned or max pages reached.
        """
        call_count = 0

        def make_response():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First page: 100 items with match
                data = [
                    {"id": f"trace-a{i}", "name": f"Session A{i}", "input": "hello",
                     "success": True, "timestamp": "2025-01-15T10:00:00Z", "userId": "user1",
                     "totalCost": 0.01, "latency": 2000, "usage": {"totalTokens": 100},
                     "metadata": {"attributes": {"gen_ai.session.id": "target-session-id"}}}
                    for i in range(100)
                ]
            else:
                # Second page: 100 more items (total 200)
                data = [
                    {"id": f"trace-b{i}", "name": f"Session B{i}", "input": "world",
                     "success": True, "timestamp": "2025-01-15T11:00:00Z", "userId": "user1",
                     "totalCost": 0.02, "latency": 3000, "usage": {"totalTokens": 200},
                     "metadata": {"attributes": {"gen_ai.session.id": "other-id"}}}
                    for i in range(100)
                ]
            
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={
                "data": data,
                "meta": {"totalItems": 200},  # More than one page
            })
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=False)
            return mock_response

        mock_aiohttp_session = AsyncMock()
        mock_aiohttp_session.get = MagicMock(side_effect=lambda *args, **kwargs: make_response())
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=False)

        with patch("agentclaw.community.core.bot_chat.service.aiohttp.ClientSession", return_value=mock_aiohttp_session):
            result = await service.list_sessions(
                owner_id="user1",
                session_id="target-session-id",
                log_source="langfuse",
            )

        # Should scan ALL pages (200 items with page size 100 = 2 pages) even after finding a match
        assert call_count == 2
        # Default limit is 20, so only 20 sessions are returned
        assert len(result.sessions) == 20
        assert result.total == 100  # But total shows all 100 matches
        assert result.sessions[0].id == "trace-a0"
        # has_more is True because there are more matched items (100 > 20) AND more items to scan
        assert result.has_more is True

    @pytest.mark.asyncio
    async def test_list_sessions_with_session_id_exhaustive_scan_multi_page_matches(self, service):
        """Test that exhaustive scan collects all matches across multiple pages and supports pagination."""
        call_count = 0

        def make_response():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First page - 100 items with 2 matches at the beginning
                data = [
                    {"id": "trace-1", "name": "Session 1", "input": [{"role": "user", "content": "hello"}],
                     "success": True, "timestamp": "2025-01-15T10:00:00Z", "userId": "user1",
                     "totalCost": 0.01, "latency": 2000, "usage": {"totalTokens": 100},
                     "metadata": {"attributes": {"gen_ai.session.id": "target-session-id"}}},
                    {"id": "trace-2", "name": "Session 2", "input": [{"role": "user", "content": "world"}],
                     "success": True, "timestamp": "2025-01-15T11:00:00Z", "userId": "user1",
                     "totalCost": 0.02, "latency": 3000, "usage": {"totalTokens": 200},
                     "metadata": {"attributes": {"gen_ai.session.id": "target-session-id"}}},
                ] + [
                    {"id": f"trace-a{i}", "name": f"Session A{i}", "input": "other",
                     "success": True, "timestamp": "2025-01-15T12:00:00Z", "userId": "user1",
                     "totalCost": 0.01, "latency": 2000, "usage": {"totalTokens": 100},
                     "metadata": {"attributes": {"gen_ai.session.id": "other-id"}}}
                    for i in range(98)
                ]
            elif call_count == 2:
                # Second page - 100 items with 1 match
                data = [
                    {"id": "trace-3", "name": "Session 3", "input": [{"role": "user", "content": "test"}],
                     "success": True, "timestamp": "2025-01-15T12:00:00Z", "userId": "user1",
                     "totalCost": 0.03, "latency": 4000, "usage": {"totalTokens": 300},
                     "metadata": {"attributes": {"gen_ai.session.id": "target-session-id"}}},
                ] + [
                    {"id": f"trace-b{i}", "name": f"Session B{i}", "input": "other",
                     "success": True, "timestamp": "2025-01-15T13:00:00Z", "userId": "user1",
                     "totalCost": 0.01, "latency": 2000, "usage": {"totalTokens": 100},
                     "metadata": {"attributes": {"gen_ai.session.id": "other-id"}}}
                    for i in range(99)
                ]
            else:
                # Third page - 50 items no matches (total 250)
                data = [
                    {"id": f"trace-c{i}", "name": f"Session C{i}", "input": "other",
                     "success": True, "timestamp": "2025-01-15T14:00:00Z", "userId": "user1",
                     "totalCost": 0.01, "latency": 2000, "usage": {"totalTokens": 100},
                     "metadata": {"attributes": {"gen_ai.session.id": "other-id"}}}
                    for i in range(50)
                ]
            
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"data": data, "meta": {"totalItems": 250}})
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=False)
            return mock_response

        mock_aiohttp_session = AsyncMock()
        mock_aiohttp_session.get = MagicMock(side_effect=lambda *args, **kwargs: make_response())
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=False)

        # Test without pagination - should collect all 3 matches
        with patch("agentclaw.community.core.bot_chat.service.aiohttp.ClientSession", return_value=mock_aiohttp_session):
            result = await service.list_sessions(
                owner_id="user1",
                session_id="target-session-id",
                page=1,
                limit=20,
                log_source="langfuse",
            )

        # Should scan all 3 pages (250 items with page size 100 = 3 pages)
        assert call_count == 3
        # Should have all 3 matches
        assert len(result.sessions) == 3
        assert result.total == 3
        assert {s.id for s in result.sessions} == {"trace-1", "trace-2", "trace-3"}
        assert result.has_more is False  # All 3 matches returned in one page

    @pytest.mark.asyncio
    async def test_list_sessions_with_session_id_exhaustive_scan_multi_page_matches_pagination(self, service):
        """Test pagination over matched results from exhaustive scan."""
        call_count = 0
        total_items = 200

        def make_response():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First page - 100 items with 3 matches
                data = [
                    {"id": f"trace-{i}", "name": f"Session {i}", "input": "test", "success": True,
                     "timestamp": f"2025-01-15T{i:02d}:00:00Z", "userId": "user1",
                     "totalCost": 0.01 * i, "latency": 1000 * i, "usage": {"totalTokens": 100 * i},
                     "metadata": {"attributes": {"gen_ai.session.id": "target-session-id"}}}
                    for i in range(1, 4)
                ] + [
                    {"id": f"trace-a{i}", "name": f"Session A{i}", "input": "other", "success": True,
                     "timestamp": "2025-01-15T10:00:00Z", "userId": "user1",
                     "totalCost": 0.01, "latency": 2000, "usage": {"totalTokens": 100},
                     "metadata": {"attributes": {"gen_ai.session.id": "other-id"}}}
                    for i in range(97)
                ]
            else:
                # Second page - 100 items with 2 more matches
                data = [
                    {"id": f"trace-{i}", "name": f"Session {i}", "input": "test", "success": True,
                     "timestamp": f"2025-01-15T{i:02d}:00:00Z", "userId": "user1",
                     "totalCost": 0.01 * i, "latency": 1000 * i, "usage": {"totalTokens": 100 * i},
                     "metadata": {"attributes": {"gen_ai.session.id": "target-session-id"}}}
                    for i in range(4, 6)
                ] + [
                    {"id": f"trace-b{i}", "name": f"Session B{i}", "input": "other", "success": True,
                     "timestamp": "2025-01-15T11:00:00Z", "userId": "user1",
                     "totalCost": 0.01, "latency": 2000, "usage": {"totalTokens": 100},
                     "metadata": {"attributes": {"gen_ai.session.id": "other-id"}}}
                    for i in range(98)
                ]
            
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"data": data, "meta": {"totalItems": total_items}})
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=False)
            return mock_response

        mock_aiohttp_session = AsyncMock()
        mock_aiohttp_session.get = MagicMock(side_effect=lambda *args, **kwargs: make_response())
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=False)

        # Request page 1 with limit 2 (should get trace-1, trace-2)
        with patch("agentclaw.community.core.bot_chat.service.aiohttp.ClientSession", return_value=mock_aiohttp_session):
            result = await service.list_sessions(
                owner_id="user1",
                session_id="target-session-id",
                page=1,
                limit=2,
                log_source="langfuse",
            )

        # Should have scanned all pages (200 items / 100 page_size = 2 pages)
        assert call_count == 2
        # Should have 2 items (page 1)
        assert len(result.sessions) == 2
        assert result.total == 5  # Total 5 matches
        assert result.page == 1
        assert result.has_more is True  # More matches available on next page

        # Reset and test page 2
        call_count = 0
        with patch("agentclaw.community.core.bot_chat.service.aiohttp.ClientSession", return_value=mock_aiohttp_session):
            result = await service.list_sessions(
                owner_id="user1",
                session_id="target-session-id",
                page=2,
                limit=2,
                log_source="langfuse",
            )

        assert len(result.sessions) == 2  # trace-3, trace-4
        assert result.total == 5
        assert result.page == 2
        assert result.has_more is True  # Still 1 more match

        # Reset and test page 3 (last item)
        call_count = 0
        with patch("agentclaw.community.core.bot_chat.service.aiohttp.ClientSession", return_value=mock_aiohttp_session):
            result = await service.list_sessions(
                owner_id="user1",
                session_id="target-session-id",
                page=3,
                limit=2,
                log_source="langfuse",
            )

        assert len(result.sessions) == 1  # trace-5
        assert result.total == 5
        assert result.page == 3
        assert result.has_more is False  # No more matches

    @pytest.mark.asyncio
    async def test_list_sessions_with_session_id_exhaustive_scan_no_match(self, service):
        """Test exhaustive scan when no match found."""
        call_count = 0
        max_pages = 10  # Default BOT_CHAT_EXACT_QUERY_MAX_PAGES
        total_items = 1000  # More items than max scan pages

        def make_response():
            nonlocal call_count
            call_count += 1
            # Each page returns 100 items with no matching session_id
            data = [
                {
                    "id": f"trace-other-{call_count}-{i}",
                    "name": "Other Session",
                    "input": [{"role": "user", "content": "hello"}],
                    "success": True,
                    "timestamp": "2025-01-15T10:00:00Z",
                    "userId": "user1",
                    "totalCost": 0.01,
                    "latency": 2000,
                    "usage": {"totalTokens": 100},
                    "metadata": {"attributes": {"gen_ai.session.id": "other-id"}},
                }
                for i in range(100)
            ]
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={
                "data": data,
                "meta": {"totalItems": total_items},
            })
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=False)
            return mock_response

        mock_aiohttp_session = AsyncMock()
        mock_aiohttp_session.get = MagicMock(side_effect=lambda *args, **kwargs: make_response())
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=False)

        with patch("agentclaw.community.core.bot_chat.service.aiohttp.ClientSession", return_value=mock_aiohttp_session):
            result = await service.list_sessions(
                owner_id="user1",
                session_id="nonexistent-session-id",
                log_source="langfuse",
            )

        # Should scan max pages (10 by default) since no match found
        assert call_count == max_pages
        assert len(result.sessions) == 0
        assert result.total == 0
        # has_more is True because scanned_count (1000) < total_items (1000) is False
        # but we stopped at max_pages, so there might be more data beyond scan window
        # Actually scanned_count == total_items here, so has_more = False
        # To indicate potential more data, we need scanned_count < total_items
        assert result.has_more is False

    @pytest.mark.asyncio
    async def test_session_key_triggers_exhaustive_scan(self, service):
        """Test that session_key triggers exhaustive scan (not just single page)."""
        call_count = 0

        def make_response():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First page: no match
                data = [
                    {"id": f"trace-a{i}", "name": f"Session A{i}", "input": "hello",
                     "success": True, "timestamp": "2025-01-15T10:00:00Z", "userId": "user1",
                     "totalCost": 0.01, "latency": 2000, "usage": {"totalTokens": 100},
                     "metadata": {"attributes": {"session_id": "other-id"}}}
                    for i in range(100)
                ]
            else:
                # Second page: match via gen_ai.conversation.id (only 1 match)
                data = [
                    {"id": f"trace-b{i}", "name": f"Session B{i}", "input": "world",
                     "success": True, "timestamp": "2025-01-15T11:00:00Z", "userId": "user1",
                     "totalCost": 0.02, "latency": 3000, "usage": {"totalTokens": 200},
                     "metadata": {"attributes": {"session_id": "other-id"}}}
                    for i in range(99)
                ] + [
                    {"id": "trace-target", "name": "Target Session", "input": "target",
                     "success": True, "timestamp": "2025-01-15T12:00:00Z", "userId": "user1",
                     "totalCost": 0.03, "latency": 4000, "usage": {"totalTokens": 300},
                     "metadata": {"attributes": {"gen_ai.conversation.id": "target-key"}}}
                ]

            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={
                "data": data,
                "meta": {"totalItems": 200},
            })
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=False)
            return mock_response

        mock_aiohttp_session = AsyncMock()
        mock_aiohttp_session.get = MagicMock(side_effect=lambda *args, **kwargs: make_response())
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=False)

        with patch("agentclaw.community.core.bot_chat.service.aiohttp.ClientSession", return_value=mock_aiohttp_session):
            result = await service.list_sessions(
                owner_id="user1",
                session_key="target-key",  # session_key should trigger exhaustive scan
                log_source="langfuse",
            )

        # Should scan 2 pages until all items scanned
        assert call_count == 2
        assert len(result.sessions) == 1
        assert result.sessions[0].id == "trace-target"
        assert result.total == 1

    @pytest.mark.asyncio
    async def test_session_key_exhaustive_scan_multi_page_no_stop(self, service):
        """Test session_key collects all matches across pages without early stopping."""
        call_count = 0

        def make_response():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First page: 2 matches via session_id
                data = [
                    {"id": "trace-1", "name": "Session 1", "input": "hello",
                     "success": True, "timestamp": "2025-01-15T10:00:00Z", "userId": "user1",
                     "totalCost": 0.01, "latency": 2000, "usage": {"totalTokens": 100},
                     "metadata": {"attributes": {"session_id": "agent:main:key"}}},
                    {"id": "trace-2", "name": "Session 2", "input": "world",
                     "success": True, "timestamp": "2025-01-15T11:00:00Z", "userId": "user1",
                     "totalCost": 0.02, "latency": 3000, "usage": {"totalTokens": 200},
                     "metadata": {"attributes": {"session_id": "agent:main:key"}}},
                ] + [
                    {"id": f"trace-a{i}", "name": f"Session A{i}", "input": "other",
                     "success": True, "timestamp": "2025-01-15T12:00:00Z", "userId": "user1",
                     "totalCost": 0.01, "latency": 2000, "usage": {"totalTokens": 100},
                     "metadata": {"attributes": {"session_id": "other"}}}
                    for i in range(98)
                ]
            else:
                # Second page: 1 more match via gen_ai.conversation.id (should not stop early)
                data = [
                    {"id": "trace-3", "name": "Session 3", "input": "test",
                     "success": True, "timestamp": "2025-01-15T13:00:00Z", "userId": "user1",
                     "totalCost": 0.03, "latency": 4000, "usage": {"totalTokens": 300},
                     "metadata": {"attributes": {"gen_ai.conversation.id": "agent:main:key"}}},
                ] + [
                    {"id": f"trace-b{i}", "name": f"Session B{i}", "input": "other",
                     "success": True, "timestamp": "2025-01-15T14:00:00Z", "userId": "user1",
                     "totalCost": 0.01, "latency": 2000, "usage": {"totalTokens": 100},
                     "metadata": {"attributes": {"session_id": "other"}}}
                    for i in range(99)
                ]

            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={
                "data": data,
                "meta": {"totalItems": 200},
            })
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=False)
            return mock_response

        mock_aiohttp_session = AsyncMock()
        mock_aiohttp_session.get = MagicMock(side_effect=lambda *args, **kwargs: make_response())
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=False)

        with patch("agentclaw.community.core.bot_chat.service.aiohttp.ClientSession", return_value=mock_aiohttp_session):
            result = await service.list_sessions(
                owner_id="user1",
                session_key="agent:main:key",
                page=1,
                limit=2,
                log_source="langfuse",
            )

        # Should scan all 2 pages (200 items / 100 page_size = 2 pages) even after finding matches on page 1
        assert call_count == 2
        # Should have 2 items (page 1 of 3 total matches)
        assert len(result.sessions) == 2
        assert result.total == 3  # Total 3 matches collected from both pages
        assert result.has_more is True  # More matches available

    @pytest.mark.asyncio
    async def test_session_key_plus_bot_id_combined_filter_langfuse(self, service):
        """Test session_key + bot_id combined filter using AND semantics in Langfuse mode.

        Note: Uses bot_id="default" to avoid ownership check which requires DB.
        The client-side filter logic is the same for any bot_id value.
        """
        call_count = 0

        def make_response():
            nonlocal call_count
            call_count += 1
            # Mix of different bot_ids and session_keys
            data = [
                # Match: correct bot_id and session_key
                {"id": "trace-1", "name": "Session 1", "input": "hello",
                 "success": True, "timestamp": "2025-01-15T10:00:00Z", "userId": "user1",
                 "totalCost": 0.01, "latency": 2000, "usage": {"totalTokens": 100},
                 "metadata": {"attributes": {"session_id": "agent:main:key", "identity.bot_id": "default"}}},
                # No match: correct session_key but wrong bot_id
                {"id": "trace-2", "name": "Session 2", "input": "world",
                 "success": True, "timestamp": "2025-01-15T11:00:00Z", "userId": "user1",
                 "totalCost": 0.02, "latency": 3000, "usage": {"totalTokens": 200},
                 "metadata": {"attributes": {"session_id": "agent:main:key", "identity.bot_id": "other"}}},
                # No match: correct bot_id but wrong session_key
                {"id": "trace-3", "name": "Session 3", "input": "test",
                 "success": True, "timestamp": "2025-01-15T12:00:00Z", "userId": "user1",
                 "totalCost": 0.03, "latency": 4000, "usage": {"totalTokens": 300},
                 "metadata": {"attributes": {"session_id": "other-key", "identity.bot_id": "default"}}},
            ] + [
                # Fill rest with non-matching items
                {"id": f"trace-x{i}", "name": f"Session X{i}", "input": "other",
                 "success": True, "timestamp": "2025-01-15T13:00:00Z", "userId": "user1",
                 "totalCost": 0.01, "latency": 2000, "usage": {"totalTokens": 100},
                 "metadata": {"attributes": {"session_id": "other", "identity.bot_id": "other"}}}
                for i in range(97)
            ]

            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={
                "data": data,
                "meta": {"totalItems": 100},
            })
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=False)
            return mock_response

        mock_aiohttp_session = AsyncMock()
        mock_aiohttp_session.get = MagicMock(side_effect=lambda *args, **kwargs: make_response())
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=False)

        with patch("agentclaw.community.core.bot_chat.service.aiohttp.ClientSession", return_value=mock_aiohttp_session):
            result = await service.list_sessions(
                owner_id="user1",
                session_key="agent:main:key",
                bot_id="default",  # Combined filter (use default to avoid ownership check)
                log_source="langfuse",
            )

        # Should only return trace-1 (matches both session_key AND bot_id)
        assert len(result.sessions) == 1
        assert result.sessions[0].id == "trace-1"
        assert result.total == 1

    @pytest.mark.asyncio
    async def test_bot_id_triggers_exhaustive_scan(self, service):
        """Test that bot_id triggers exhaustive scan, not current-page filtering."""
        call_count = 0

        def make_response():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                data = [
                    {"id": f"trace-a{i}", "name": f"Session A{i}", "input": "hello",
                     "success": True, "timestamp": "2025-01-15T10:00:00Z", "userId": "user1",
                     "totalCost": 0.01, "latency": 2000, "usage": {"totalTokens": 100},
                     "metadata": {"attributes": {"identity.bot_id": "other_bot"}}}
                    for i in range(100)
                ]
            else:
                data = [
                    {"id": f"trace-b{i}", "name": f"Session B{i}", "input": "world",
                     "success": True, "timestamp": "2025-01-15T11:00:00Z", "userId": "user1",
                     "totalCost": 0.02, "latency": 3000, "usage": {"totalTokens": 200},
                     "metadata": {"attributes": {"identity.bot_id": "other_bot"}}}
                    for i in range(99)
                ] + [
                    {"id": "trace-default", "name": "Default Bot Session", "input": "target",
                     "success": True, "timestamp": "2025-01-15T12:00:00Z", "userId": "user1",
                     "totalCost": 0.03, "latency": 4000, "usage": {"totalTokens": 300},
                     "metadata": {"attributes": {"identity.bot_id": "default"}}}
                ]

            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={
                "data": data,
                "meta": {"totalItems": 200},
            })
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=False)
            return mock_response

        mock_aiohttp_session = AsyncMock()
        mock_aiohttp_session.get = MagicMock(side_effect=lambda *args, **kwargs: make_response())
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=False)

        with patch("agentclaw.community.core.bot_chat.service.aiohttp.ClientSession", return_value=mock_aiohttp_session):
            result = await service.list_sessions(
                owner_id="user1",
                bot_id="default",
                log_source="langfuse",
            )

        assert call_count == 2
        assert len(result.sessions) == 1
        assert result.sessions[0].id == "trace-default"
        assert result.total == 1


# ---------------------------------------------------------------------------
# BotChatService.get_session (mocked aiohttp)
# ---------------------------------------------------------------------------

class TestBotChatServiceGetSession:

    @pytest.fixture
    def service(self):
        # Mock DatabasePlugin for testing
        mock_db = MagicMock()
        return BotChatService(db=mock_db, config=_TEST_BOTCHAT_CONFIG)

    def _detail(self, trace_id="trace-1"):
        return ConversationDetail(
            id=trace_id,
            name="DB Detail",
            timestamp="2026-06-01T00:00:00Z",
            observations=[],
        )

    def _row(self, **kwargs):
        data = {
            "trace_id": "trace-1",
            "bot_id": "default",
            "user_id": "user1",
        }
        data.update(kwargs)
        row = MagicMock()
        for key, value in data.items():
            setattr(row, key, value)
        return row

    @pytest.mark.asyncio
    async def test_get_session_db_not_found(self, service):
        service._db_repo = MagicMock()
        service._db_repo.get_ocb_trace.return_value = None
        service._db_repo.get_trace.return_value = None

        with pytest.raises(SessionNotFoundError):
            await service.get_session(trace_id="missing", owner_id="user1")

    @pytest.mark.asyncio
    async def test_get_session_db_default_bot_owner_mismatch(self, service):
        """Default-bot traces still require user_id to match owner_id."""
        service._db_repo = MagicMock()
        service._db_repo.get_ocb_trace.return_value = None
        service._db_repo.get_trace.return_value = self._row(user_id="other-user", bot_id="default")

        with pytest.raises(SessionNotFoundError):
            await service.get_session(trace_id="trace-1", owner_id="user1")

    @pytest.mark.asyncio
    async def test_get_session_db_default_bot_owner_match(self, service):
        """Default-bot traces are accessible when user_id matches owner_id."""
        row = self._row(user_id="user1", bot_id="default")
        detail = self._detail("trace-1")
        service._db_repo = MagicMock()
        service._db_repo.get_ocb_trace.return_value = None
        service._db_repo.get_trace.return_value = row
        service._db_repo.list_legacy_observations.return_value = []
        service._db_repo._row_to_detail.return_value = detail
        service._fetch_observations_from_langfuse = AsyncMock(return_value=[])

        result = await service.get_session(trace_id="trace-1", owner_id="user1")

        assert result is detail
        service._db_repo.has_bot_access.assert_not_called()
        service._db_repo.list_legacy_observations.assert_called_once_with("trace-1")
        service._fetch_observations_from_langfuse.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_session_db_ocb_trace_does_not_mix_legacy_observations(self, service):
        """If AC OTEL trace exists, detail should not mix in legacy observations."""
        row = self._row(user_id="user1", bot_id="default")
        detail = self._detail("trace-1")
        service._db_repo = MagicMock()
        service._db_repo.get_ocb_trace.return_value = row
        service._db_repo.get_trace.return_value = self._row(user_id="user1", bot_id="default")
        service._db_repo.list_ocb_observations.return_value = []
        service._db_repo.list_legacy_observations.return_value = [ConversationObservation(id="legacy-obs", type="LLM")]
        service._db_repo._row_to_detail.return_value = detail

        result = await service.get_session(trace_id="trace-1", owner_id="user1")

        assert result is detail
        service._db_repo.get_trace.assert_not_called()
        service._db_repo.list_ocb_observations.assert_called_once_with("trace-1")
        service._db_repo.list_legacy_observations.assert_not_called()
        service._db_repo._row_to_detail.assert_called_once_with(row, [])

    @pytest.mark.asyncio
    async def test_get_session_db_non_default_bot_unauthorized(self, service):
        """Non-default bot traces reject users who are neither owner nor collaborator."""
        service._db_repo = MagicMock()
        service._db_repo.get_ocb_trace.return_value = None
        service._db_repo.get_trace.return_value = self._row(bot_id="bot-a", user_id="not-trusted")
        service._db_repo.has_bot_access.return_value = False

        with pytest.raises(SessionNotFoundError):
            await service.get_session(trace_id="trace-1", owner_id="user1")

        service._db_repo.has_bot_access.assert_called_once_with("user1", "bot-a")

    @pytest.mark.asyncio
    async def test_get_session_db_non_default_bot_owner_allowed(self, service):
        """Non-default bot traces allow owner access."""
        row = self._row(bot_id="bot-a", user_id="not-trusted")
        detail = self._detail("trace-1")
        service._db_repo = MagicMock()
        service._db_repo.get_ocb_trace.return_value = None
        service._db_repo.get_trace.return_value = row
        service._db_repo.has_bot_access.return_value = True
        service._db_repo.list_legacy_observations.return_value = []
        service._db_repo._row_to_detail.return_value = detail
        service._fetch_observations_from_langfuse = AsyncMock(return_value=[])

        result = await service.get_session(trace_id="trace-1", owner_id="user1")

        assert result is detail
        service._db_repo.has_bot_access.assert_called_once_with("user1", "bot-a")
        service._db_repo.list_legacy_observations.assert_called_once_with("trace-1")
        service._fetch_observations_from_langfuse.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_session_db_non_default_bot_collaborator_allowed(self, service):
        """Non-default bot traces allow collaborator access."""
        row = self._row(bot_id="bot-a", user_id="owner-user")
        detail = self._detail("trace-1")
        service._db_repo = MagicMock()
        service._db_repo.get_ocb_trace.return_value = None
        service._db_repo.get_trace.return_value = row
        service._db_repo.has_bot_access.return_value = True
        service._db_repo.list_legacy_observations.return_value = []
        service._db_repo._row_to_detail.return_value = detail
        service._fetch_observations_from_langfuse = AsyncMock(return_value=[])

        result = await service.get_session(trace_id="trace-1", owner_id="collaborator-1")

        assert result is detail
        service._db_repo.has_bot_access.assert_called_once_with("collaborator-1", "bot-a")
        service._db_repo.list_legacy_observations.assert_called_once_with("trace-1")
        service._fetch_observations_from_langfuse.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_session_db_success_fetches_observations_and_maps_detail(self, service):
        row = self._row(bot_id="bot-a", user_id="not-trusted")
        observation = ConversationObservation(id="obs-1", type="AGENT")
        detail = self._detail("trace-1")
        detail.observations = [observation]
        service._db_repo = MagicMock()
        service._db_repo.get_ocb_trace.return_value = None
        service._db_repo.get_trace.return_value = row
        service._db_repo.has_bot_access.return_value = True
        service._db_repo.list_legacy_observations.return_value = [observation]
        service._db_repo._row_to_detail.return_value = detail
        service._fetch_observations_from_langfuse = AsyncMock(return_value=[observation])

        result = await service.get_session(trace_id="trace-1", owner_id="user1")

        assert result is detail
        service._db_repo.has_bot_access.assert_called_once_with("user1", "bot-a")
        service._db_repo.list_legacy_observations.assert_called_once_with("trace-1")
        service._fetch_observations_from_langfuse.assert_not_awaited()
        service._db_repo._row_to_detail.assert_called_once_with(row, [observation])

    @pytest.mark.asyncio
    async def test_get_session_db_without_owner_skips_ownership(self, service):
        row = self._row(bot_id="bot-a", user_id="other")
        detail = self._detail("trace-1")
        service._db_repo = MagicMock()
        service._db_repo.get_ocb_trace.return_value = None
        service._db_repo.get_trace.return_value = row
        service._db_repo.list_legacy_observations.return_value = []
        service._db_repo._row_to_detail.return_value = detail
        service._fetch_observations_from_langfuse = AsyncMock(return_value=[])

        result = await service.get_session(trace_id="trace-1", owner_id=None)

        assert result is detail
        service._db_repo.has_bot_access.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_observations_success_builds_tree(self, service):
        root_response = AsyncMock()
        root_response.status = 200
        root_response.json = AsyncMock(return_value={
            "data": [
                {"id": "root", "name": "Root", "type": "AGENT", "parentObservationId": None},
                {"id": "child", "name": "Child", "type": "GENERATION", "parentObservationId": "root"},
            ]
        })
        root_response.__aenter__ = AsyncMock(return_value=root_response)
        root_response.__aexit__ = AsyncMock(return_value=False)

        mock_aiohttp_session = AsyncMock()
        mock_aiohttp_session.get = MagicMock(return_value=root_response)
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=False)

        with patch("agentclaw.community.core.bot_chat.service.aiohttp.ClientSession", return_value=mock_aiohttp_session):
            result = await service._fetch_observations_from_langfuse("trace-1")

        assert len(result) == 1
        assert result[0].id == "root"
        assert result[0].children[0].id == "child"

    @pytest.mark.asyncio
    async def test_fetch_observations_non_200_returns_empty(self, service):
        response = AsyncMock()
        response.status = 500
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=False)

        mock_aiohttp_session = AsyncMock()
        mock_aiohttp_session.get = MagicMock(return_value=response)
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=False)

        with patch("agentclaw.community.core.bot_chat.service.aiohttp.ClientSession", return_value=mock_aiohttp_session):
            result = await service._fetch_observations_from_langfuse("trace-1")

        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_observations_exception_returns_empty(self, service):
        mock_aiohttp_session = AsyncMock()
        mock_aiohttp_session.get = MagicMock(side_effect=TimeoutError("timeout"))
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=False)

        with patch("agentclaw.community.core.bot_chat.service.aiohttp.ClientSession", return_value=mock_aiohttp_session):
            result = await service._fetch_observations_from_langfuse("trace-1")

        assert result == []

    @pytest.mark.asyncio
    async def test_get_session_not_found_langfuse(self, service):
        mock_response = AsyncMock()
        mock_response.status = 404

        mock_session_ctx = AsyncMock()
        mock_session_ctx.get = MagicMock(return_value=mock_response)
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_aiohttp_session = AsyncMock()
        mock_aiohttp_session.get = MagicMock(return_value=mock_session_ctx)
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=False)

        with patch("agentclaw.community.core.bot_chat.service.aiohttp.ClientSession", return_value=mock_aiohttp_session):
            with pytest.raises(SessionNotFoundError):
                # Note: get_session doesn't have log_source param in router,
                # but service method supports it for testing
                await service.get_session(trace_id="nonexistent", owner_id="user1", log_source="langfuse")

    @pytest.mark.asyncio
    async def test_get_session_langfuse_default_bot_owner_mismatch(self, service):
        """Langfuse default-bot traces still require userId to match owner_id."""
        trace_response = AsyncMock()
        trace_response.status = 200
        trace_response.json = AsyncMock(return_value={
            "id": "trace-1",
            "name": "Private Session",
            "userId": "other_user",
            "timestamp": "2025-01-01T00:00:00Z",
            "success": True,
        })
        trace_response.__aenter__ = AsyncMock(return_value=trace_response)
        trace_response.__aexit__ = AsyncMock(return_value=False)

        mock_aiohttp_session = AsyncMock()
        mock_aiohttp_session.get = MagicMock(return_value=trace_response)
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=False)

        with patch("agentclaw.community.core.bot_chat.service.aiohttp.ClientSession", return_value=mock_aiohttp_session):
            with pytest.raises(SessionNotFoundError):
                await service.get_session(trace_id="trace-1", owner_id="user1", log_source="langfuse")

    @pytest.mark.asyncio
    async def test_get_session_langfuse_default_bot_owner_match(self, service):
        """Langfuse default-bot traces are accessible when userId matches owner_id."""
        trace_response = AsyncMock()
        trace_response.status = 200
        trace_response.json = AsyncMock(return_value={
            "id": "trace-1",
            "name": "Session",
            "userId": "user1",
            "timestamp": "2025-01-01T00:00:00Z",
            "success": True,
        })
        trace_response.__aenter__ = AsyncMock(return_value=trace_response)
        trace_response.__aexit__ = AsyncMock(return_value=False)

        obs_response = AsyncMock()
        obs_response.status = 200
        obs_response.json = AsyncMock(return_value={"data": []})
        obs_response.__aenter__ = AsyncMock(return_value=obs_response)
        obs_response.__aexit__ = AsyncMock(return_value=False)

        call_count = 0

        def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            return trace_response if call_count == 1 else obs_response

        mock_aiohttp_session = AsyncMock()
        mock_aiohttp_session.get = mock_get
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=False)

        with patch("agentclaw.community.core.bot_chat.service.aiohttp.ClientSession", return_value=mock_aiohttp_session):
            result = await service.get_session(trace_id="trace-1", owner_id="user1", log_source="langfuse")

        assert result.id == "trace-1"
        assert result.user_id == "user1"

    @pytest.mark.asyncio
    async def test_get_session_langfuse_non_default_bot_unauthorized(self, service):
        """Langfuse non-default bot traces reject unauthorized users."""
        trace_response = AsyncMock()
        trace_response.status = 200
        trace_response.json = AsyncMock(return_value={
            "id": "trace-1",
            "name": "Private Session",
            "userId": "other_user",
            "timestamp": "2025-01-01T00:00:00Z",
            "success": True,
            "metadata": {"attributes": {"identity.bot_id": "bot-a"}},
        })
        trace_response.__aenter__ = AsyncMock(return_value=trace_response)
        trace_response.__aexit__ = AsyncMock(return_value=False)

        service._db_repo = MagicMock()
        service._db_repo.has_bot_access.return_value = False

        mock_aiohttp_session = AsyncMock()
        mock_aiohttp_session.get = MagicMock(return_value=trace_response)
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=False)

        with patch("agentclaw.community.core.bot_chat.service.aiohttp.ClientSession", return_value=mock_aiohttp_session):
            with pytest.raises(SessionNotFoundError):
                await service.get_session(trace_id="trace-1", owner_id="user1", log_source="langfuse")

        service._db_repo.has_bot_access.assert_called_once_with("user1", "bot-a")

    @pytest.mark.asyncio
    async def test_get_session_langfuse_non_default_bot_owner_allowed(self, service):
        """Langfuse non-default bot traces allow owner access."""
        trace_response = AsyncMock()
        trace_response.status = 200
        trace_response.json = AsyncMock(return_value={
            "id": "trace-1",
            "name": "Session",
            "userId": "other_user",
            "timestamp": "2025-01-01T00:00:00Z",
            "success": True,
            "metadata": {"attributes": {"identity.bot_id": "bot-a"}},
        })
        trace_response.__aenter__ = AsyncMock(return_value=trace_response)
        trace_response.__aexit__ = AsyncMock(return_value=False)

        obs_response = AsyncMock()
        obs_response.status = 200
        obs_response.json = AsyncMock(return_value={"data": []})
        obs_response.__aenter__ = AsyncMock(return_value=obs_response)
        obs_response.__aexit__ = AsyncMock(return_value=False)

        call_count = 0

        def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            return trace_response if call_count == 1 else obs_response

        service._db_repo = MagicMock()
        service._db_repo.has_bot_access.return_value = True
        service._db_repo.get_group_labels.return_value = (None, None)
        service._db_repo.get_bot_name.return_value = "bot-a"

        mock_aiohttp_session = AsyncMock()
        mock_aiohttp_session.get = mock_get
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=False)

        with patch("agentclaw.community.core.bot_chat.service.aiohttp.ClientSession", return_value=mock_aiohttp_session):
            result = await service.get_session(trace_id="trace-1", owner_id="user1", log_source="langfuse")

        assert result.id == "trace-1"
        service._db_repo.has_bot_access.assert_called_once_with("user1", "bot-a")

    @pytest.mark.asyncio
    async def test_get_session_langfuse_non_default_bot_collaborator_allowed(self, service):
        """Langfuse non-default bot traces allow collaborator access."""
        trace_response = AsyncMock()
        trace_response.status = 200
        trace_response.json = AsyncMock(return_value={
            "id": "trace-1",
            "name": "Session",
            "userId": "owner-user",
            "timestamp": "2025-01-01T00:00:00Z",
            "success": True,
            "metadata": {"attributes": {"identity.bot_id": "bot-a"}},
        })
        trace_response.__aenter__ = AsyncMock(return_value=trace_response)
        trace_response.__aexit__ = AsyncMock(return_value=False)

        obs_response = AsyncMock()
        obs_response.status = 200
        obs_response.json = AsyncMock(return_value={"data": []})
        obs_response.__aenter__ = AsyncMock(return_value=obs_response)
        obs_response.__aexit__ = AsyncMock(return_value=False)

        call_count = 0

        def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            return trace_response if call_count == 1 else obs_response

        service._db_repo = MagicMock()
        service._db_repo.has_bot_access.return_value = True
        service._db_repo.get_group_labels.return_value = (None, None)
        service._db_repo.get_bot_name.return_value = "bot-a"

        mock_aiohttp_session = AsyncMock()
        mock_aiohttp_session.get = mock_get
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=False)

        with patch("agentclaw.community.core.bot_chat.service.aiohttp.ClientSession", return_value=mock_aiohttp_session):
            result = await service.get_session(trace_id="trace-1", owner_id="collaborator-1", log_source="langfuse")

        assert result.id == "trace-1"
        service._db_repo.has_bot_access.assert_called_once_with("collaborator-1", "bot-a")


# ---------------------------------------------------------------------------
# BotChatService open exact queries
# ---------------------------------------------------------------------------


class TestBotChatServiceOpenQueries:

    @pytest.fixture
    def service(self):
        return BotChatService(db=MagicMock(), config=_TEST_BOTCHAT_CONFIG)

    @pytest.mark.asyncio
    async def test_open_group_query_uses_open_scope_without_owner(self, service):
        service._db_repo = MagicMock()
        service._db_repo.list_ocb_traces.return_value = ([], 0)
        service._db_repo.list_traces.return_value = ([], 0)

        await service.list_open_sessions(group_id=" group_fixture ")

        kwargs = service._db_repo.list_ocb_traces.call_args.kwargs
        assert kwargs["owner_id"] is None
        assert kwargs["group_id"] == "group_fixture"
        assert kwargs["query_scope"] == QueryScope.OPEN
        assert kwargs["match_mode"] == "exact"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "params",
        [
            {},
            {"biz_scene": "scene_fixture"},
            {"biz_task_id": "task_fixture"},
            {
                "session_key": "session_fixture",
                "group_id": "group_fixture",
            },
        ],
    )
    async def test_open_query_rejects_invalid_mode_combinations(
        self, service, params
    ):
        with pytest.raises(ValueError):
            await service.list_open_sessions(**params)

    @pytest.mark.asyncio
    async def test_open_detail_skips_owner_filter(self, service):
        detail = MagicMock(spec=ConversationDetail)
        service._get_session_db = AsyncMock(return_value=detail)

        result = await service.get_open_session(" trace_fixture ")

        assert result is detail
        service._get_session_db.assert_awaited_once_with(
            "trace_fixture", owner_id=None
        )


# ---------------------------------------------------------------------------
# BotChatService.health_check
# ---------------------------------------------------------------------------


class TestBotChatServiceHealthCheck:

    @pytest.fixture
    def service(self):
        # Mock DatabasePlugin for testing
        mock_db = MagicMock()
        return BotChatService(db=mock_db, config=_TEST_BOTCHAT_CONFIG)

    @pytest.mark.asyncio
    async def test_health_check_healthy_langfuse(self, service):
        mock_response = AsyncMock()
        mock_response.status = 200

        mock_session_ctx = AsyncMock()
        mock_session_ctx.get = MagicMock(return_value=mock_response)
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_aiohttp_session = AsyncMock()
        mock_aiohttp_session.get = MagicMock(return_value=mock_session_ctx)
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=False)

        # Langfuse creds/endpoint are read from env with neutral empty defaults;
        # The service is configured with test Langfuse creds via the fixture, so
        # the health-check exercises the HTTP path (not the unconfigured branch).
        with patch("agentclaw.community.core.bot_chat.service.aiohttp.ClientSession", return_value=mock_aiohttp_session):
            result = await service.health_check()

        assert result.status == "healthy"

    @pytest.mark.asyncio
    async def test_health_check_unhealthy_langfuse(self, service):
        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="error")

        mock_session_ctx = AsyncMock()
        mock_session_ctx.get = MagicMock(return_value=mock_response)
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_aiohttp_session = AsyncMock()
        mock_aiohttp_session.get = MagicMock(return_value=mock_session_ctx)
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=False)

        # The fixture configures langfuse creds, so we exercise the HTTP-500 path,
        # not the unconfigured short-circuit.
        with patch("agentclaw.community.core.bot_chat.service.aiohttp.ClientSession", return_value=mock_aiohttp_session):
            result = await service.health_check()

        assert result.status == "unhealthy"
        assert result.error is not None
