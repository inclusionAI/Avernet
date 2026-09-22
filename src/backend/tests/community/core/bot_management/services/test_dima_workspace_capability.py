"""Unit tests for has_dima_workspace_enabled capability check."""
from __future__ import annotations

from agentclaw.community.core.bot_management.services.aicoding.dima_workspace_capability import (
    has_dima_workspace_enabled,
)


class TestHasDimaWorkspaceEnabled:
    """has_dima_workspace_enabled: 扁平 capabilities.dima_workspace 优先，嵌套兜底。"""

    # ── 扁平形态：模板工厂快照约定（capabilities 在根部） ──────────────

    def test_true_when_flat_bool_true(self):
        assert has_dima_workspace_enabled(
            {"capabilities": {"dima_workspace": True}}
        ) is True

    def test_true_when_flat_string_true(self):
        assert has_dima_workspace_enabled(
            {"capabilities": {"dima_workspace": "true"}}
        ) is True

    def test_false_when_flat_bool_false(self):
        assert has_dima_workspace_enabled(
            {"capabilities": {"dima_workspace": False}}
        ) is False

    def test_false_when_flat_missing_key(self):
        assert has_dima_workspace_enabled(
            {"capabilities": {"enable_bcn_network": True}}
        ) is False

    def test_flat_capabilities_is_sole_truth_source(self):
        """根部 capabilities 存在时是唯一事实源：缺 dima_workspace 即 False，不再看嵌套。"""
        assert has_dima_workspace_enabled(
            {
                "capabilities": {"other": True},
                "bot_template_config": {"capabilities": {"dima_workspace": True}},
            }
        ) is False

    def test_flat_false_wins_over_nested_true(self):
        assert has_dima_workspace_enabled(
            {
                "capabilities": {"dima_workspace": False},
                "bot_template_config": {"capabilities": {"dima_workspace": True}},
            }
        ) is False

    # ── 契约 terminal：扁平键存在但值畸形 → 全关闭，不混入 legacy 嵌套 ─────

    def test_flat_declared_but_none_value_is_false_not_nested_fallback(self):
        """扁平键存在但值为 None：唯一事实源 → False，禁止 fall-through 到嵌套。"""
        assert has_dima_workspace_enabled(
            {
                "capabilities": None,
                "bot_template_config": {"capabilities": {"dima_workspace": True}},
            }
        ) is False

    def test_flat_declared_but_string_value_is_false_not_nested_fallback(self):
        assert has_dima_workspace_enabled(
            {
                "capabilities": "malformed",
                "bot_template_config": {"capabilities": {"dima_workspace": True}},
            }
        ) is False

    # ── 嵌套形态：bot_template_config.capabilities.dima_workspace（兼容兜底） ──

    def test_true_when_bool_true(self):
        assert has_dima_workspace_enabled(
            {"bot_template_config": {"capabilities": {"dima_workspace": True}}}
        ) is True

    def test_true_when_string_true(self):
        assert has_dima_workspace_enabled(
            {"bot_template_config": {"capabilities": {"dima_workspace": "true"}}}
        ) is True

    def test_true_when_string_true_uppercase(self):
        assert has_dima_workspace_enabled(
            {"bot_template_config": {"capabilities": {"dima_workspace": "TRUE"}}}
        ) is True

    def test_true_when_string_true_with_whitespace(self):
        assert has_dima_workspace_enabled(
            {"bot_template_config": {"capabilities": {"dima_workspace": " true "}}}
        ) is True

    def test_false_when_string_false(self):
        assert has_dima_workspace_enabled(
            {"bot_template_config": {"capabilities": {"dima_workspace": "false"}}}
        ) is False

    def test_false_when_bool_false(self):
        assert has_dima_workspace_enabled(
            {"bot_template_config": {"capabilities": {"dima_workspace": False}}}
        ) is False

    def test_false_when_other_string(self):
        assert has_dima_workspace_enabled(
            {"bot_template_config": {"capabilities": {"dima_workspace": "yes"}}}
        ) is False

    def test_false_when_integer_one(self):
        assert has_dima_workspace_enabled(
            {"bot_template_config": {"capabilities": {"dima_workspace": 1}}}
        ) is False

    def test_false_when_dima_workspace_missing(self):
        assert has_dima_workspace_enabled(
            {"bot_template_config": {"capabilities": {"other": True}}}
        ) is False

    def test_false_when_capabilities_missing(self):
        assert has_dima_workspace_enabled({"bot_template_config": {}}) is False

    def test_false_when_bot_template_config_missing(self):
        assert has_dima_workspace_enabled({}) is False

    def test_false_when_bot_template_config_not_mapping(self):
        assert has_dima_workspace_enabled({"bot_template_config": "x"}) is False

    def test_false_when_capabilities_not_mapping(self):
        assert has_dima_workspace_enabled(
            {"bot_template_config": {"capabilities": None}}
        ) is False

    def test_false_when_template_config_not_mapping(self):
        assert has_dima_workspace_enabled(None) is False
        assert has_dima_workspace_enabled("string") is False

    def test_ignores_other_capabilities(self):
        assert has_dima_workspace_enabled(
            {"bot_template_config": {"capabilities": {"foo": True, "dima_workspace": True}}}
        ) is True
