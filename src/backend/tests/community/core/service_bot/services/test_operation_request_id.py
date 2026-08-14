"""Unit tests for the deterministic operation request-id helper."""
import re

from agentclaw.community.core.service_bot.services.publish_flow.operation_runner import (
    operation_request_id,
    to_baas_request_id,
)

# BaaS validates request_id against this on its strict endpoints (scale /
# update-devices / restart), so every id we generate must satisfy it.
_BAAS_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{32,64}$")


def test_deterministic_same_inputs():
    a = operation_request_id(5, "upgrade", "online", 1)
    b = operation_request_id(5, "upgrade", "online", 1)
    assert a == b


def test_complies_with_baas_request_id_contract():
    # Every kind/stage combination, including short ones and an empty stage, must
    # land in BaaS's 32-64 / [A-Za-z0-9_-] window.
    for pid, kind, stage, attempt in [
        (5, "upgrade", "online", 1),
        (7, "restart", "", 2),
        (1, "scale", "online", 1),
        (7, "eval_teardown", "eval", 1),
        (999_999_999_999, "first_release", "online", 99),
    ]:
        rid = operation_request_id(pid, kind, stage, attempt)
        assert _BAAS_REQUEST_ID.match(rid), rid


def test_readable_prefix_preserved():
    # The greppable prefix survives so a BaaS log line still points at the op.
    assert operation_request_id(1, "scale", "online", 1).startswith("pub_1_scale_online_a1")
    assert operation_request_id(7, "restart", "", 2).startswith("pub_7_restart_a2")


def test_distinct_across_kind_stage_attempt():
    base = operation_request_id(9, "upgrade", "verify", 1)
    assert base != operation_request_id(9, "first_release", "verify", 1)
    assert base != operation_request_id(9, "upgrade", "online", 1)
    assert base != operation_request_id(9, "upgrade", "verify", 2)
    assert base != operation_request_id(10, "upgrade", "verify", 1)


def test_to_baas_request_id_folds_invalid_and_short_input():
    # Dots become underscores and a short id is padded into range.
    rid = to_baas_request_id("offline_destroy.pub_5.online")
    assert _BAAS_REQUEST_ID.match(rid), rid
    assert "." not in rid
    assert to_baas_request_id("x") == to_baas_request_id("x")  # deterministic
