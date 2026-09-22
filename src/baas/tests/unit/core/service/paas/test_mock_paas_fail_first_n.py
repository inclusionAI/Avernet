"""Unit tests for the mock PaaS fail-first-N create counter."""

import pytest

from secbaas.community.core.service.paas._mock_paas_service import (
    _consume_create_failure,
    reset_create_failure_counter,
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("PAAS_MOCK_CREATE_FAIL_TIMES", raising=False)
    reset_create_failure_counter()
    yield
    reset_create_failure_counter()


class TestFailFirstN:
    def test_no_budget_never_fails(self):
        assert _consume_create_failure() is False

    def test_zero_budget_never_fails(self, monkeypatch):
        monkeypatch.setenv("PAAS_MOCK_CREATE_FAIL_TIMES", "0")
        assert _consume_create_failure() is False

    def test_fails_exactly_n_times_then_succeeds(self, monkeypatch):
        monkeypatch.setenv("PAAS_MOCK_CREATE_FAIL_TIMES", "2")
        assert _consume_create_failure() is True
        assert _consume_create_failure() is True
        assert _consume_create_failure() is False
        assert _consume_create_failure() is False

    def test_invalid_value_is_treated_as_zero(self, monkeypatch):
        monkeypatch.setenv("PAAS_MOCK_CREATE_FAIL_TIMES", "not-a-number")
        assert _consume_create_failure() is False

    def test_negative_value_is_treated_as_zero(self, monkeypatch):
        monkeypatch.setenv("PAAS_MOCK_CREATE_FAIL_TIMES", "-5")
        assert _consume_create_failure() is False

    def test_reset_restores_budget(self, monkeypatch):
        monkeypatch.setenv("PAAS_MOCK_CREATE_FAIL_TIMES", "1")
        assert _consume_create_failure() is True
        assert _consume_create_failure() is False
        reset_create_failure_counter()
        assert _consume_create_failure() is True
