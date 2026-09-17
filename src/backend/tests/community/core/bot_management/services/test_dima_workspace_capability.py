"""Unit tests for has_dima_workspace_enabled capability check."""
from __future__ import annotations

from agentclaw.community.core.bot_management.services.aicoding.dima_workspace_capability import (
    has_dima_workspace_enabled,
)


class TestHasDimaWorkspaceEnabled:
    """has_dima_workspace_enabled: 读取 bot_template_config.capabilities.dima_workspace。"""

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
