"""Tests for staff-directory work number normalization."""

import pytest

from agentclaw.community.utils.work_no import normalize_work_no_for_lookup


@pytest.mark.parametrize(
    ("work_no", "expected"),
    [
        ("1234", "001234"),
        ("12345", "012345"),
        ("123456", "123456"),
        ("1234567", "1234567"),
        ("A1234", "A1234"),
        (" 1234 ", "001234"),
        ("  A1234  ", "A1234"),
    ],
)
def test_normalize_work_no_for_lookup(work_no: str, expected: str) -> None:
    assert normalize_work_no_for_lookup(work_no) == expected
