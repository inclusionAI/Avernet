"""Tests for the capability resolver and the surface feature flag (W1).

_fail-closed_ is the whole point of these tests: the matrix from
``engine-requirements.zh-CN.md`` §2, plus the phase-1 delivery notes
(``engine_config``/``cli_tools`` unsupported until their materializers land),
plus the W13-mandated two-argument purity.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.bot_config_manifest.capabilities import (
    ARC_ENGINES,
    RESERVED_IDENTITY_FILES,
    identity_file_whitelist,
    supported_categories,
)
from agentclaw.community.core.bot_config_manifest.feature_flags import (
    get_bot_config_manifest_flags,
)


# --- fail closed -------------------------------------------------------------


@pytest.mark.parametrize("engine", ["", "nobody", "TECLAW", "Claude_Code"])
def test_unknown_engines_are_unsupported_everywhere(engine):
    support = supported_categories(engine, "personal")
    assert set(support.categories.values()) == {False}
    # Every False carries a reason — an unexplained refusal is a dead end for
    # a caller deciding what to do.
    for category, supported in support.categories.items():
        if not supported:
            assert category in support.reasons


def test_engine_names_are_case_sensitive():
    """未知大小写 = 未知引擎,按 fail closed 处理(不做静默纠正)。"""
    assert supported_categories("OpenClaw", "personal").categories == (
        supported_categories("nobody", "personal").categories
    )


# --- ARCA family -------------------------------------------------------------


@pytest.mark.parametrize("engine", sorted(ARC_ENGINES))
def test_arca_engines_support_the_five_and_script(engine):
    support = supported_categories(engine, "personal")
    assert support.categories["mcp"] is True
    assert support.categories["resources"] is True
    assert support.categories["skills"] is True
    assert support.categories["identity"] is True
    assert support.categories["script"] is True
    # 一期例外:engine_config(T3)与 cli_tools(W9)fail closed。
    assert support.categories["engine_config"] is False
    assert support.categories["cli_tools"] is False
    assert "T3" in support.reasons["engine_config"]
    assert "W9" in support.reasons["cli_tools"]


def test_bot_type_does_not_fork_the_table():
    """v1 表不随 bot_type 分支;签名冗余是为 W13 参数入口预留。"""
    assert (
        supported_categories("openclaw", "personal").categories
        == supported_categories("openclaw", "service").categories
    )


# --- teclaw ------------------------------------------------------------------


def test_teclaw_supports_the_five_but_refuses_script():
    support = supported_categories("teclaw", "personal")
    for category in ("mcp", "resources", "skills", "identity"):
        assert support.categories[category] is True
    assert support.categories["script"] is False
    assert "start-command" in support.reasons["script"]


# --- identity whitelists -----------------------------------------------------


def test_identity_whitelist_matches_the_identity_service_constants():
    from agentclaw.community.core.services.identity import (
        CLAUDE_CODE_IDENTITY_FILES,
        VALID_IDENTITY_FILES,
    )

    assert identity_file_whitelist("claude_code") == frozenset(
        CLAUDE_CODE_IDENTITY_FILES
    )
    for engine in ("openclaw", "teclaw"):
        assert identity_file_whitelist(engine) == frozenset(VALID_IDENTITY_FILES)
    assert identity_file_whitelist("nobody") is None


def test_memory_and_identity_files_are_never_writable():
    """D2 覆盖语义的唯一例外,提前钉死(W5 的物化器消费同一常量)。"""
    assert RESERVED_IDENTITY_FILES == frozenset({"MEMORY.md", "IDENTITY.md"})


# --- feature flag ------------------------------------------------------------


def test_flag_defaults_to_dark(monkeypatch):
    monkeypatch.delenv("BCM_API_ENABLED", raising=False)
    assert get_bot_config_manifest_flags().api_enabled is False


def test_flag_reads_the_env(monkeypatch):
    monkeypatch.setenv("BCM_API_ENABLED", "1")
    assert get_bot_config_manifest_flags().api_enabled is True


def test_flag_is_read_per_call_not_cached(monkeypatch):
    """读缓存 Pin 住第一条请求的环境变量(dcl-singletons 门禁禁掉了 lazy
    快照)——env 每次读取即刻生效。"""
    monkeypatch.delenv("BCM_API_ENABLED", raising=False)
    assert get_bot_config_manifest_flags().api_enabled is False
    monkeypatch.setenv("BCM_API_ENABLED", "1")
    assert get_bot_config_manifest_flags().api_enabled is True
