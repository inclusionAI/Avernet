"""Tests for the A2A issuance gray-release gate (pure layer + gate service).

Covers the semantic matrix of the design doc:
- unconfigured / ``enabled:false`` → no gating at all (face stays additive);
- valid whitelist / percent → membership by stable hash;
- any malformed configuration or DB read failure → fail-closed (face withheld);
- empty ``staff_id`` → fail-closed.
"""

from __future__ import annotations

import pytest

from agentclaw.community.core.devices.services.a2a_issuance_gray import (
    A2A_ISSUANCE_GRAY_CONFIG_CATEGORY,
    A2A_ISSUANCE_GRAY_CONFIG_KEY,
    A2aIssuanceGate,
    is_user_in_gray,
    parse_gray_config,
)


class _FakeConfigService:
    """Structural stand-in for SystemConfigServiceProtocol."""

    def __init__(self, value=None, *, raises: bool = False):
        self._value = value
        self._raises = raises
        self.calls: list[dict[str, str]] = []

    def get_config(self, *, category: str, config_key: str, env: str):
        self.calls.append(
            {"category": category, "config_key": config_key, "env": env}
        )
        if self._raises:
            raise RuntimeError("config store unavailable")
        return self._value


# ===========================================================================
# parse_gray_config — unconfigured / disabled → None (no gating)
# ===========================================================================


@pytest.mark.parametrize("raw", [None, "", "  ", {}, {"enabled": False}])
def test_unconfigured_or_disabled_means_no_gate(raw):
    assert parse_gray_config(raw) is None


def test_raw_json_string_is_parsed():
    parsed = parse_gray_config(
        '{"enabled": true, "mode": "percent", "percent": 10}'
    )
    assert parsed is not None
    assert is_user_in_gray(parsed, "240841") in (True, False)


# ===========================================================================
# parse_gray_config — whitelist mode
# ===========================================================================


def test_whitelist_valid():
    parsed = parse_gray_config(
        {"enabled": True, "mode": "whitelist", "users": ["240841", "009977"]}
    )
    assert parsed is not None
    assert decoded_members(parsed) == frozenset({"240841", "009977"})


def decoded_members(parsed) -> frozenset[str] | None:
    return getattr(parsed, "users", None)


# ===========================================================================
# parse_gray_config — percent mode
# ===========================================================================


@pytest.mark.parametrize(
    "raw",
    [
        {"enabled": True, "mode": "percent", "percent": 0},
        {"enabled": True, "mode": "percent", "percent": 1},
        {"enabled": True, "mode": "percent", "percent": 50},
        {"enabled": True, "mode": "percent", "percent": 100},
    ],
)
def test_percent_valid(raw):
    parsed = parse_gray_config(raw)
    assert parsed is not None
    assert parsed.percent == raw["percent"]


# ===========================================================================
# parse_gray_config — malformed → fail-closed (empty whitelist)
# ===========================================================================


@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",
        [],
        "123",
        {"enabled": True},
        {"enabled": True, "mode": "unknown-mode", "users": ["a"]},
        {"enabled": True, "mode": "whitelist", "users": [1, 2]},
        {"enabled": True, "mode": "whitelist", "users": "single-string"},
        {"enabled": True, "mode": "percent", "percent": -1},
        {"enabled": True, "mode": "percent", "percent": 101},
        {"enabled": True, "mode": "percent", "percent": "50"},
        {"enabled": True, "mode": "percent"},
        {"enabled": "yes", "mode": "percent", "percent": 10},
    ],
)
def test_malformed_config_fails_closed(raw):
    parsed = parse_gray_config(raw)
    assert parsed is not None, "fail-closed must still yield a gate, not none"
    assert is_user_in_gray(parsed, "240841") is False


# ===========================================================================
# is_user_in_gray — membership semantics
# ===========================================================================


def test_whitelist_membership():
    parsed = parse_gray_config(
        {"enabled": True, "mode": "whitelist", "users": ["240841"]}
    )
    assert is_user_in_gray(parsed, "240841") is True
    assert is_user_in_gray(parsed, "other") is False


def test_percent_zero_is_closed_for_everyone():
    parsed = parse_gray_config(
        {"enabled": True, "mode": "percent", "percent": 0}
    )
    assert is_user_in_gray(parsed, "240841") is False


def test_percent_hundred_is_open_for_everyone():
    parsed = parse_gray_config(
        {"enabled": True, "mode": "percent", "percent": 100}
    )
    assert is_user_in_gray(parsed, "240841") is True


def test_percent_bucket_is_stable_per_user():
    parsed = parse_gray_config(
        {"enabled": True, "mode": "percent", "percent": 10}
    )
    trials = [is_user_in_gray(parsed, "240841") for _ in range(20)]
    assert set(trials) <= {True} or set(trials) <= {False}, trials


@pytest.mark.parametrize("staff_id", ["", None])
def test_empty_staff_id_fails_closed(staff_id):
    parsed = parse_gray_config(
        {"enabled": True, "mode": "percent", "percent": 100}
    )
    assert is_user_in_gray(parsed, staff_id) is False


# ===========================================================================
# A2aIssuanceGate — service semantics over the config store
# ===========================================================================


def test_gate_unconfigured_allows_face():
    gate = A2aIssuanceGate(_FakeConfigService(None))
    assert gate.allows_face("240841") is True


def test_gate_disabled_config_allows_face():
    gate = A2aIssuanceGate(_FakeConfigService({"enabled": False}))
    assert gate.allows_face("240841") is True


def test_gate_whitelist_hit_and_miss():
    hit = A2aIssuanceGate(
        _FakeConfigService(
            {"enabled": True, "mode": "whitelist", "users": ["240841"]}
        )
    )
    assert hit.allows_face("240841") is True
    assert hit.allows_face("other") is False


def test_gate_read_failure_withholds_face(caplog):
    gate = A2aIssuanceGate(_FakeConfigService(None, raises=True))
    assert gate.allows_face("240841") is False
    assert any("a2a" in (rec.msg.lower() if rec.msg else "") for rec in caplog.records)


def test_gate_malformed_config_withholds_face():
    gate = A2aIssuanceGate(_FakeConfigService("}broken{"))
    assert gate.allows_face("240841") is False


def test_gate_reads_the_pinned_config_key():
    fake = _FakeConfigService(
        {"enabled": True, "mode": "whitelist", "users": ["240841"]}
    )
    A2aIssuanceGate(fake).allows_face("240841")
    assert fake.calls == [
        {
            "category": A2A_ISSUANCE_GRAY_CONFIG_CATEGORY,
            "config_key": A2A_ISSUANCE_GRAY_CONFIG_KEY,
            "env": fake.calls[0]["env"],
        }
    ]