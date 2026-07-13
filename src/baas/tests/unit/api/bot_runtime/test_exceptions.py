"""Unit tests for api/bot_runtime/_exceptions.py — Bot runtime exception hierarchy."""

from secbaas.community.api.bot_runtime import (
    BotNotAvailableError,
    BotNotFoundError,
    BotServiceError,
    NoActiveDevicesError,
    NoDevicesFoundError,
    SessionClosedError,
    SessionError,
    SessionNotFoundError,
)


class TestBotServiceError:
    """Tests for BotServiceError — base exception for all bot service errors."""

    def test_default_message(self):
        """WHEN created without args, THEN message is empty."""
        err = BotServiceError()
        assert err.message == ""
        assert str(err) == ""

    def test_with_message(self):
        """WHEN message provided, THEN it is stored."""
        err = BotServiceError("something went wrong")
        assert err.message == "something went wrong"
        assert str(err) == "something went wrong"

    def test_is_exception(self):
        """THEN BotServiceError is an Exception subclass."""
        assert issubclass(BotServiceError, Exception)


class TestBotNotFoundError:
    """Tests for BotNotFoundError."""

    def test_default_bot_id(self):
        """WHEN created without args, THEN bot_id is empty."""
        err = BotNotFoundError()
        assert err.bot_id == ""

    def test_with_bot_id(self):
        """WHEN bot_id provided, THEN message includes it."""
        err = BotNotFoundError("bot-123")
        assert err.bot_id == "bot-123"
        assert "bot-123" in str(err)

    def test_subclass(self):
        """THEN BotNotFoundError is a BotServiceError."""
        assert issubclass(BotNotFoundError, BotServiceError)


class TestBotNotAvailableError:
    """Tests for BotNotAvailableError."""

    def test_default_values(self):
        """WHEN created without args, THEN bot_id and status are empty."""
        err = BotNotAvailableError()
        assert err.bot_id == ""
        assert err.status == ""

    def test_with_bot_id_and_status(self):
        """WHEN bot_id and status provided, THEN message includes both."""
        err = BotNotAvailableError("bot-123", "INACTIVE")
        assert err.bot_id == "bot-123"
        assert err.status == "INACTIVE"
        assert "bot-123" in str(err)
        assert "INACTIVE" in str(err)

    def test_subclass(self):
        """THEN BotNotAvailableError is a BotServiceError."""
        assert issubclass(BotNotAvailableError, BotServiceError)


class TestSessionError:
    """Tests for SessionError — base exception for session errors."""

    def test_default(self):
        """WHEN created, THEN it is a valid exception."""
        err = SessionError()
        assert str(err) == ""

    def test_subclass(self):
        """THEN SessionError is a BotServiceError."""
        assert issubclass(SessionError, BotServiceError)


class TestSessionNotFoundError:
    """Tests for SessionNotFoundError."""

    def test_default_session_id(self):
        """WHEN created without args, THEN session_id is empty."""
        err = SessionNotFoundError()
        assert err.session_id == ""

    def test_with_session_id(self):
        """WHEN session_id provided, THEN message includes it."""
        err = SessionNotFoundError("sess-001")
        assert err.session_id == "sess-001"
        assert "sess-001" in str(err)

    def test_subclass(self):
        """THEN SessionNotFoundError is a SessionError."""
        assert issubclass(SessionNotFoundError, SessionError)


class TestSessionClosedError:
    """Tests for SessionClosedError."""

    def test_default_session_id(self):
        """WHEN created without args, THEN session_id is empty."""
        err = SessionClosedError()
        assert err.session_id == ""

    def test_with_session_id(self):
        """WHEN session_id provided, THEN message includes it."""
        err = SessionClosedError("sess-001")
        assert err.session_id == "sess-001"
        assert "closed" in str(err).lower()

    def test_subclass(self):
        """THEN SessionClosedError is a SessionError."""
        assert issubclass(SessionClosedError, SessionError)


class TestNoDevicesFoundError:
    """Tests for NoDevicesFoundError."""

    def test_default_bot_uuid(self):
        """WHEN created without args, THEN bot_uuid is empty."""
        err = NoDevicesFoundError()
        assert err.bot_uuid == ""

    def test_with_bot_uuid(self):
        """WHEN bot_uuid provided, THEN message includes it."""
        err = NoDevicesFoundError("bot-uuid-xyz")
        assert err.bot_uuid == "bot-uuid-xyz"
        assert "bot-uuid-xyz" in str(err)

    def test_subclass(self):
        """THEN NoDevicesFoundError is a BotServiceError."""
        assert issubclass(NoDevicesFoundError, BotServiceError)


class TestNoActiveDevicesError:
    """Tests for NoActiveDevicesError."""

    def test_default_bot_uuid(self):
        """WHEN created without args, THEN bot_uuid is empty."""
        err = NoActiveDevicesError()
        assert err.bot_uuid == ""

    def test_with_bot_uuid(self):
        """WHEN bot_uuid provided, THEN message includes it."""
        err = NoActiveDevicesError("bot-uuid-xyz")
        assert err.bot_uuid == "bot-uuid-xyz"
        assert "active" in str(err).lower()

    def test_subclass(self):
        """THEN NoActiveDevicesError is a BotServiceError."""
        assert issubclass(NoActiveDevicesError, BotServiceError)
