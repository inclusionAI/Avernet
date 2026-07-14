"""Unit tests for api/bot_runtime/_models.py — Bot runtime data models."""

from datetime import datetime

from secbaas.community.api.bot_runtime import (
    BotChatContext,
    BotResponse,
    MessageContent,
    MessageDeliverRequest,
    SessionInfo,
)


class TestBotChatContext:
    """Tests for BotChatContext dataclass."""

    def test_required_fields(self):
        """WHEN created with required fields, THEN all are set."""
        ctx = BotChatContext(
            api_key_prefix="key-abc",
            app_id="app-123",
            app_type="baas",
        )
        assert ctx.api_key_prefix == "key-abc"
        assert ctx.app_id == "app-123"
        assert ctx.app_type == "baas"

    def test_optional_fields_defaults(self):
        """WHEN optional fields omitted, THEN they default correctly."""
        ctx = BotChatContext(api_key_prefix="k", app_id="a", app_type="t")
        assert ctx.iam_token is None
        assert ctx.tenant == ""
        assert ctx.extra is None

    def test_optional_fields_provided(self):
        """WHEN optional fields provided, THEN they are stored."""
        ctx = BotChatContext(
            api_key_prefix="k",
            app_id="a",
            app_type="t",
            iam_token="token-xyz",
            tenant="my-tenant",
            extra={"key": "val"},
        )
        assert ctx.iam_token == "token-xyz"
        assert ctx.tenant == "my-tenant"
        assert ctx.extra == {"key": "val"}

    def test_immutable_by_default(self):
        """THEN dataclass is not frozen (fields are mutable)."""
        ctx = BotChatContext(api_key_prefix="k", app_id="a", app_type="t")
        ctx.api_key_prefix = "new-key"
        assert ctx.api_key_prefix == "new-key"

    def test_build_auth_token(self):
        """THEN build_auth_token returns expected format."""
        ctx = BotChatContext(api_key_prefix="key-abc", app_id="a", app_type="t")
        token = ctx.build_auth_token()
        assert token == "OPEN_API:app:key-abc"

    def test_equality(self):
        """THEN two identical instances are equal."""
        ctx1 = BotChatContext(api_key_prefix="k", app_id="a", app_type="t")
        ctx2 = BotChatContext(api_key_prefix="k", app_id="a", app_type="t")
        assert ctx1 == ctx2


class TestSessionInfo:
    """Tests for SessionInfo dataclass."""

    def test_required_fields(self):
        """WHEN created with required fields, THEN session_id and bot_id are set."""
        info = SessionInfo(session_id="sess-001", bot_id="bot-123")
        assert info.session_id == "sess-001"
        assert info.bot_id == "bot-123"

    def test_status_default(self):
        """THEN status defaults to 'active'."""
        info = SessionInfo(session_id="s", bot_id="b")
        assert info.status == "active"

    def test_created_at_default(self):
        """THEN created_at defaults to current datetime (rough check)."""
        info = SessionInfo(session_id="s", bot_id="b")
        assert isinstance(info.created_at, datetime)

    def test_all_fields(self):
        """WHEN all fields provided, THEN they are stored."""
        now = datetime.now()
        info = SessionInfo(
            session_id="sess-001",
            bot_id="bot-123",
            status="closed",
            created_at=now,
            expires_at=now,
            metadata={"foo": "bar"},
        )
        assert info.status == "closed"
        assert info.created_at == now
        assert info.expires_at == now
        assert info.metadata == {"foo": "bar"}


class TestBotResponse:
    """Tests for BotResponse dataclass."""

    def test_required_fields(self):
        """WHEN created with content, THEN it is stored."""
        resp = BotResponse(content="Hello, world!")
        assert resp.content == "Hello, world!"

    def test_optional_fields_defaults(self):
        """WHEN optional fields omitted, THEN they default to None."""
        resp = BotResponse(content="Hi")
        assert resp.usage is None
        assert resp.metadata is None

    def test_all_fields(self):
        """WHEN all fields provided, THEN they are stored."""
        resp = BotResponse(
            content="Response text",
            usage={"prompt_tokens": 10, "completion_tokens": 20},
            metadata={"session_id": "sess-001"},
        )
        assert resp.usage["prompt_tokens"] == 10
        assert resp.metadata["session_id"] == "sess-001"


class TestMessageDeliverRequest:
    """Tests for MessageDeliverRequest dataclass."""

    def test_required_fields(self):
        """WHEN created with required fields, THEN all set."""
        req = MessageDeliverRequest(
            message="hello",
            bot_id="bot-123",
            raw_session_id="sess-001",
        )
        assert req.message == "hello"
        assert req.bot_id == "bot-123"
        assert req.raw_session_id == "sess-001"


class TestMessageContent:
    """Tests for MessageContent dataclass."""

    def test_required_fields(self):
        """WHEN created with text, THEN text is set."""
        msg = MessageContent(text="Hello")
        assert msg.text == "Hello"

    def test_attachments_default(self):
        """THEN attachments defaults to None."""
        msg = MessageContent(text="Hi")
        assert msg.attachments is None

    def test_with_attachments(self):
        """WHEN attachments provided, THEN they are stored."""
        attachments = [{"type": "image", "url": "https://example.com/img.png"}]
        msg = MessageContent(text="Check this", attachments=attachments)
        assert msg.attachments == attachments
