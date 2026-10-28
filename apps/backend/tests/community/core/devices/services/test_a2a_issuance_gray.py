"""Tests for the A2A issuance gray-release gate (pure layer + gate service).

Covers the semantic matrix of the design doc:
- unconfigured / ``enabled:false`` → no gating at all (face stays additive);
- valid whitelist / percent → membership by stable hash;
- any malformed configuration or DB read failure → fail-closed (face withheld);
- empty ``staff_id`` → fail-closed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentclaw.community.core.devices.services.a2a_issuance_gray import (
    A2A_ISSUANCE_GRAY_CONFIG_CATEGORY,
    A2A_ISSUANCE_GRAY_CONFIG_KEY,
    A2aIssuanceGate,
    is_user_in_gray,
    parse_gray_config,
)


class _FakeConfigService:
    """Structural stand-in for ConfigRepositoryProtocol (record-level reads).

    ``record``: None = 键不存在；否则是携带原始 ``config_value``（repo 不做
    JSON 解析）的极简 record 形态，见 ``_record``。
    """

    _CATEGORY = SimpleNamespace(id=7)

    def __init__(self, record=None, *, raises: bool = False):
        self._record = record
        self._raises = raises
        self.calls: list[tuple] = []

    def get_category(self, *, category: str, env: str):
        self.calls.append(("get_category", category, env))
        if self._raises:
            raise RuntimeError("config store unavailable")
        return self._CATEGORY

    def get_config_by_key(self, *, parent_id: int, config_key: str):
        self.calls.append(("get_config", parent_id, config_key))
        if self._raises:
            raise RuntimeError("config store unavailable")
        return self._record


def _record(config_value):
    """repo record 极简形态：``config_value`` 是未 parse 的原始字符串。"""
    return SimpleNamespace(id=1, parent_id=7, config_key="x", config_value=config_value)


# ===========================================================================
# parse_gray_config — 显式 enabled:false 是唯一的“解除门控”正形
# ===========================================================================


def test_disabled_releases_the_gate():
    assert parse_gray_config({"enabled": False}) is None


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
        # review R3-F2：值存在但为 null/空白——键存在性已由 gate 层确认后
        # 才到达 parse，这里必须 fail-closed 而非解读为"未配置"。
        None,
        "",
        "   ",
        "null",
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
        # review F3：配置对象已存在而缺 enabled / enabled 为 null——不是
        # "未配置"（键不存在），是结构残缺，必须 fail-closed 而非全量放行。
        {},
        {"mode": "percent", "percent": 10},
        {"enabled": None, "mode": "percent", "percent": 10},
        {"mode": "whitelist", "users": ["240841"]},
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
    """键不存在（repo record None）= 未管控，加性签发保持。"""
    gate = A2aIssuanceGate(_FakeConfigService(None))
    assert gate.allows_face("240841") is True


def test_gate_unconfigured_category_allows_face():
    """分类目录未建同属未管控。"""

    class _NoCategoryRepo(_FakeConfigService):
        def get_category(self, *, category, env):
            return None

    gate = A2aIssuanceGate(_NoCategoryRepo())
    assert gate.allows_face("240841") is True


def test_gate_disabled_config_allows_face():
    gate = A2aIssuanceGate(
        _FakeConfigService(record=_record('{"enabled": false}'))
    )
    assert gate.allows_face("240841") is True


def test_gate_whitelist_hit_and_miss():
    hit = A2aIssuanceGate(
        _FakeConfigService(record=_record('{"enabled": true, "mode": "whitelist", "users": ["240841"]}'))
    )
    assert hit.allows_face("240841") is True
    assert hit.allows_face("other") is False


@pytest.mark.parametrize("stored", ["null", "", "   ", "None"])
def test_gate_existing_but_empty_value_fails_closed(stored):
    """review R3-F2：记录存在而值为 'null'/空串/空白——半配置事故。
    repo record 携带未 parse 原始串，gate 不得把它折叠成“未配置”放行。"""
    gate = A2aIssuanceGate(_FakeConfigService(record=_record(stored)))
    assert gate.allows_face("240841") is False


def test_gate_read_failure_withholds_face(caplog):
    gate = A2aIssuanceGate(_FakeConfigService(record=None, raises=True))
    assert gate.allows_face("240841") is False
    assert any("a2a" in (rec.msg.lower() if rec.msg else "") for rec in caplog.records)


def test_gate_malformed_config_withholds_face():
    gate = A2aIssuanceGate(_FakeConfigService(record=_record("}broken{")))
    assert gate.allows_face("240841") is False


def test_gate_reads_the_pinned_config_key():
    fake = _FakeConfigService(
        record=_record('{"enabled": true, "mode": "whitelist", "users": ["240841"]}')
    )
    A2aIssuanceGate(fake).allows_face("240841")
    assert fake.calls == [
        ("get_category", A2A_ISSUANCE_GRAY_CONFIG_CATEGORY, fake.calls[0][2]),
        ("get_config", 7, A2A_ISSUANCE_GRAY_CONFIG_KEY),
    ]