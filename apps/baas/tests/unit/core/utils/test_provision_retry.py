"""Unit tests for the publish retry deadline and backoff helpers."""

from datetime import datetime, timedelta

from secbaas.community.core.utils.provision_retry import (
    ATTEMPT_TIMESTAMP_FORMAT,
    MAX_RETRY_BACKOFF_SECONDS,
    RETRY_BACKOFF_BASE_SECONDS,
    attempt_started_at,
    has_retry_budget,
    is_attempt_expired,
    is_publish_deadline_exceeded,
    parse_attempt_started_at,
    publish_deadline,
    retry_backoff_seconds,
)


class TestAttemptTimestamp:
    def test_stamps_parseable_timestamp(self):
        value = attempt_started_at()
        assert datetime.strptime(value, ATTEMPT_TIMESTAMP_FORMAT)

    def test_roundtrip_parse(self):
        value = attempt_started_at()
        assert parse_attempt_started_at(value) is not None

    def test_none_and_empty_parse_to_none(self):
        assert parse_attempt_started_at(None) is None
        assert parse_attempt_started_at("") is None

    def test_invalid_value_parses_to_none(self):
        assert parse_attempt_started_at("not-a-timestamp") is None


class TestIsAttemptExpired:
    def test_fresh_attempt_not_expired(self):
        now = datetime(2026, 1, 1, 12, 0, 0)
        started = (now - timedelta(seconds=10)).strftime(ATTEMPT_TIMESTAMP_FORMAT)
        assert is_attempt_expired(started, 60, now=now) is False

    def test_overdue_attempt_expired(self):
        now = datetime(2026, 1, 1, 12, 0, 0)
        started = (now - timedelta(seconds=61)).strftime(ATTEMPT_TIMESTAMP_FORMAT)
        assert is_attempt_expired(started, 60, now=now) is True

    def test_exact_boundary_is_expired(self):
        now = datetime(2026, 1, 1, 12, 0, 0)
        started = now.strftime(ATTEMPT_TIMESTAMP_FORMAT)
        assert is_attempt_expired(started, 0, now=now) is True

    def test_missing_timestamp_is_expired(self):
        assert is_attempt_expired(None, 60) is True

    def test_corrupt_timestamp_is_expired(self):
        assert is_attempt_expired("garbage", 60) is True


class TestRetryBackoff:
    def test_first_retry_waits_one_base(self):
        assert retry_backoff_seconds(1) == RETRY_BACKOFF_BASE_SECONDS

    def test_backoff_doubles_per_ordinal(self):
        assert retry_backoff_seconds(2) == RETRY_BACKOFF_BASE_SECONDS * 2
        assert retry_backoff_seconds(3) == RETRY_BACKOFF_BASE_SECONDS * 4

    def test_backoff_is_capped(self):
        assert retry_backoff_seconds(50) == MAX_RETRY_BACKOFF_SECONDS

    def test_non_positive_ordinal_has_no_wait(self):
        assert retry_backoff_seconds(0) == 0.0
        assert retry_backoff_seconds(-1) == 0.0

    def test_backoff_is_monotonic(self):
        values = [retry_backoff_seconds(i) for i in range(1, 12)]
        assert values == sorted(values)


class TestHasRetryBudget:
    def test_zero_budget_has_none(self):
        assert has_retry_budget(0, 0) is False

    def test_budget_available(self):
        assert has_retry_budget(0, 1) is True
        assert has_retry_budget(1, 3) is True

    def test_budget_exhausted(self):
        assert has_retry_budget(3, 3) is False
        assert has_retry_budget(4, 3) is False


class TestPublishDeadline:
    def test_deadline_adds_duration(self):
        created = datetime(2026, 1, 1, 12, 0, 0)
        assert publish_deadline(created, 600) == datetime(2026, 1, 1, 12, 10, 0)

    def test_within_deadline_not_exceeded(self):
        created = datetime(2026, 1, 1, 12, 0, 0)
        now = created + timedelta(seconds=599)
        assert is_publish_deadline_exceeded(created, 600, now=now) is False

    def test_past_deadline_exceeded(self):
        created = datetime(2026, 1, 1, 12, 0, 0)
        now = created + timedelta(seconds=601)
        assert is_publish_deadline_exceeded(created, 600, now=now) is True

    def test_missing_created_at_never_exceeds(self):
        assert is_publish_deadline_exceeded(None, 600) is False
