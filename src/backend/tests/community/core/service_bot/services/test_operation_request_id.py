"""Unit tests for the deterministic operation request-id helper."""
from agentclaw.community.core.service_bot.services.publish_flow.operation_runner import (
    operation_request_id,
)


def test_deterministic_same_inputs():
    a = operation_request_id(5, "upgrade", "online", 1)
    b = operation_request_id(5, "upgrade", "online", 1)
    assert a == b == "pub_5.upgrade.online.a1"


def test_empty_stage_omitted():
    assert operation_request_id(7, "restart", "", 2) == "pub_7.restart.a2"


def test_distinct_across_kind_stage_attempt():
    base = operation_request_id(9, "upgrade", "verify", 1)
    assert base != operation_request_id(9, "first_release", "verify", 1)
    assert base != operation_request_id(9, "upgrade", "online", 1)
    assert base != operation_request_id(9, "upgrade", "verify", 2)
    assert base != operation_request_id(10, "upgrade", "verify", 1)


def test_within_128_chars_for_large_publish_id():
    rid = operation_request_id(999_999_999_999, "first_release", "online", 99)
    assert len(rid) <= 128
