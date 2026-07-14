"""Unit tests for api/publish_manage/_exceptions.py — Publish domain exceptions."""

from secbaas.community.api import DomainError
from secbaas.community.api.publish_manage import (
    PublishConflictError,
    PublishNotFoundError,
)


class TestPublishNotFoundError:
    """Tests for PublishNotFoundError."""

    def test_default_publish_id(self):
        err = PublishNotFoundError()
        assert err.publish_id == ""
        assert "not found" in str(err).lower()

    def test_with_publish_id(self):
        err = PublishNotFoundError(42)
        assert err.publish_id == 42
        assert "42" in str(err)

    def test_with_string_publish_id(self):
        err = PublishNotFoundError("pub-001")
        assert err.publish_id == "pub-001"
        assert "pub-001" in str(err)

    def test_error_code(self):
        assert PublishNotFoundError.error_code == "PUBLISH_NOT_FOUND"

    def test_http_status(self):
        assert PublishNotFoundError.http_status == 404

    def test_is_domain_error(self):
        assert issubclass(PublishNotFoundError, DomainError)


class TestPublishConflictError:
    """Tests for PublishConflictError."""

    def test_default_message(self):
        err = PublishConflictError()
        assert err.message == ""
        assert str(err) == ""

    def test_with_message(self):
        err = PublishConflictError("conflict occurred")
        assert err.message == "conflict occurred"
        assert str(err) == "conflict occurred"

    def test_error_code(self):
        assert PublishConflictError.error_code == "PUBLISH_CONFLICT"

    def test_http_status(self):
        assert PublishConflictError.http_status == 409

    def test_is_domain_error(self):
        assert issubclass(PublishConflictError, DomainError)
