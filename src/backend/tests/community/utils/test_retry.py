"""Tests for agentclaw.community.utils.retry.

Assertions are on **call counts**, not only outcomes: the point of the component
is how many times the thunk runs, and an outcome-only assertion would pass even
if retry silently did nothing.
"""
from __future__ import annotations

import pytest

from agentclaw.community.utils import retry as retry_mod
from agentclaw.community.utils.retry import (
    client_error_status,
    describe_exception,
    is_transport_failure,
    retry_transport_call,
)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Patch ``time.sleep`` so the suite never actually waits.

    Returns the list of slept durations so tests can assert on the backoff.
    """
    slept: list[float] = []
    monkeypatch.setattr(retry_mod.time, "sleep", slept.append)
    return slept


def _response_exc(status: int) -> Exception:
    """An exception carrying an HTTP response with ``status``."""

    class _Response:
        status_code = status

    exc = RuntimeError(f"http {status}")
    exc.response = _Response()  # type: ignore[attr-defined]
    return exc


class _Counter:
    """Thunk that records its call count and replays a scripted sequence."""

    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self) -> object:
        self.calls += 1
        outcome = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class TestRetryTransportCall:
    def test_returns_value_and_calls_once_on_success(self):
        call = _Counter("payload")
        assert retry_transport_call(call, operation="op") == "payload"
        assert call.calls == 1

    def test_retries_once_then_succeeds(self, no_sleep: list[float]):
        call = _Counter(RuntimeError("connection reset"), "payload")
        assert retry_transport_call(call, operation="op") == "payload"
        assert call.calls == 2
        assert len(no_sleep) == 1

    def test_does_not_retry_on_client_error_response(self, no_sleep: list[float]):
        call = _Counter(_response_exc(404))
        with pytest.raises(RuntimeError):
            retry_transport_call(call, operation="op")
        assert call.calls == 1
        assert no_sleep == []

    def test_exhausts_attempts_and_reraises_original_exception(self):
        original = RuntimeError("connection reset")
        call = _Counter(original)
        with pytest.raises(RuntimeError) as excinfo:
            retry_transport_call(call, operation="op")
        # The *same* object, not a wrapper: call-site ``except`` clauses and
        # cause chains must keep working after adoption.
        assert excinfo.value is original
        assert call.calls == 2

    def test_completed_error_response_is_returned_not_retried(self):
        """A 5xx *response object* is an answer — the thunk returned it."""

        class _Response:
            status_code = 503

        response = _Response()
        call = _Counter(response)
        assert retry_transport_call(call, operation="op") is response
        assert call.calls == 1

    def test_attempts_one_performs_no_retry(self, no_sleep: list[float]):
        call = _Counter(RuntimeError("connection reset"))
        with pytest.raises(RuntimeError):
            retry_transport_call(call, operation="op", attempts=1)
        assert call.calls == 1
        assert no_sleep == []

    def test_attempts_zero_raises_value_error(self):
        call = _Counter("payload")
        with pytest.raises(ValueError):
            retry_transport_call(call, operation="op", attempts=0)
        assert call.calls == 0

    def test_backoff_is_slept_and_non_negative(self, no_sleep: list[float]):
        call = _Counter(RuntimeError("connection reset"), "payload")
        retry_transport_call(call, operation="op", backoff_seconds=0.2)
        assert len(no_sleep) == 1
        # Base plus jitter, never below the base and never unbounded.
        assert 0.2 <= no_sleep[0] <= 0.2 * (1 + retry_mod._JITTER_FRACTION)

    def test_honours_higher_attempt_counts(self, no_sleep: list[float]):
        call = _Counter(RuntimeError("connection reset"), RuntimeError("again"), "ok")
        assert retry_transport_call(call, operation="op", attempts=3) == "ok"
        assert call.calls == 3
        assert len(no_sleep) == 2


class TestClientErrorStatus:
    @pytest.mark.parametrize("status", [400, 404, 429, 499])
    def test_client_error_returns_status(self, status: int):
        assert client_error_status(_response_exc(status)) == status

    @pytest.mark.parametrize("status", [200, 302, 500, 503])
    def test_non_client_error_returns_none(self, status: int):
        assert client_error_status(_response_exc(status)) is None

    def test_missing_response_returns_none(self):
        assert client_error_status(RuntimeError("connection reset")) is None

    def test_non_int_status_returns_none(self):
        class _Response:
            status_code = "404"

        exc = RuntimeError("odd")
        exc.response = _Response()  # type: ignore[attr-defined]
        assert client_error_status(exc) is None

    def test_is_transport_failure_is_the_inverse(self):
        assert is_transport_failure(RuntimeError("connection reset")) is True
        assert is_transport_failure(_response_exc(404)) is False
        # A 5xx is a transport failure by this definition — the caller owns
        # status-based policy, so the component does not claim it.
        assert is_transport_failure(_response_exc(503)) is True


class TestDescribeException:
    def test_bare_exception(self):
        assert describe_exception(RuntimeError("boom")) == "RuntimeError: boom"

    def test_includes_cause(self):
        cause = ValueError("connect timeout")
        exc = RuntimeError("Error in httpx send hook")
        exc.__cause__ = cause
        detail = describe_exception(exc)
        assert "RuntimeError: Error in httpx send hook" in detail
        assert "caused by ValueError: connect timeout" in detail

    def test_includes_context_when_no_cause(self):
        exc = RuntimeError("wrapper")
        exc.__context__ = ValueError("underlying")
        assert "caused by ValueError: underlying" in describe_exception(exc)

    def test_includes_request_url(self):
        class _Request:
            url = "https://baas.internal/api/v1/bots/abc/http-info"

        exc = RuntimeError("boom")
        exc.request = _Request()  # type: ignore[attr-defined]
        assert "request=https://baas.internal/api/v1/bots/abc/http-info" in (
            describe_exception(exc)
        )

    def test_self_referential_cause_is_not_repeated(self):
        exc = RuntimeError("boom")
        exc.__context__ = exc
        assert describe_exception(exc) == "RuntimeError: boom"
