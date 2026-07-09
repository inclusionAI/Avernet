"""Unit tests for Open API dependencies.

Covers:
- parse_bot_id: bot_id 解析为 real_bot_id 和 entity_id
- match_allowed_bots: 校验 target_bot_id 是否在 allowed_bots 列表中，返回匹配到的权威 bot_id
"""

from secbaas.adapters.web.routers.open_api.dependencies import (
    match_allowed_bots,
    parse_bot_id,
)


class TestParseBotId:
    """测试 parse_bot_id 函数"""

    def test_with_entity_id(self):
        """带 entity_id 的情况"""
        real_bot_id, entity_id = parse_bot_id("bot123:entity456")
        assert real_bot_id == "bot123"
        assert entity_id == "entity456"

    def test_without_entity_id(self):
        """不带 entity_id 的情况"""
        real_bot_id, entity_id = parse_bot_id("bot123")
        assert real_bot_id == "bot123"
        assert entity_id == ""

    def test_default_with_entity_id(self):
        """default 模式带 entity_id"""
        real_bot_id, entity_id = parse_bot_id("default:entity456")
        assert real_bot_id == "default"
        assert entity_id == "entity456"

    def test_default_without_entity_id(self):
        """default 模式不带 entity_id"""
        real_bot_id, entity_id = parse_bot_id("default")
        assert real_bot_id == "default"
        assert entity_id == ""

    def test_empty_string(self):
        """空字符串"""
        real_bot_id, entity_id = parse_bot_id("")
        assert real_bot_id == ""
        assert entity_id == ""

    def test_multiple_colons(self):
        """多个冒号，只取第一个"""
        real_bot_id, entity_id = parse_bot_id("bot123:entity:extra")
        assert real_bot_id == "bot123"
        assert entity_id == "entity:extra"


class TestMatchAllowedBots:
    """测试 match_allowed_bots 函数"""

    def test_empty_allowed_bots(self):
        """allowed_bots 为空列表"""
        assert match_allowed_bots("bot123:entity456", []) is None

    def test_empty_target(self):
        """target_bot_id 为空"""
        assert match_allowed_bots("", ["bot123"]) is None

    def test_non_default_matching_real_bot_id(self):
        """非 default 模式：real_bot_id 匹配即可，返回 allowed 中的权威格式"""
        assert match_allowed_bots("bot123:entity456", ["bot123"]) == "bot123"

    def test_non_default_matching_full_id(self):
        """非 default 模式：完整匹配"""
        assert (
            match_allowed_bots("bot123:entity456", ["bot123:entity456"])
            == "bot123:entity456"
        )

    def test_non_default_not_matching(self):
        """非 default 模式：不匹配"""
        assert match_allowed_bots("bot123:entity456", ["bot456"]) is None

    def test_non_default_with_different_entity(self):
        """非 default 模式：real_bot_id 相同但 entity_id 不同，返回权威格式"""
        assert (
            match_allowed_bots("bot123:entity456", ["bot123:entity789"])
            == "bot123:entity789"
        )

    def test_default_matching_full_id(self):
        """default 模式：需要完整匹配"""
        assert (
            match_allowed_bots("default:entity456", ["default:entity456"])
            == "default:entity456"
        )

    def test_default_not_matching_only_real_bot_id(self):
        """default 模式：只匹配 real_bot_id 不匹配"""
        assert match_allowed_bots("default:entity456", ["default"]) is None

    def test_default_not_matching_different_entity(self):
        """default 模式：entity_id 不同不匹配"""
        assert match_allowed_bots("default:entity456", ["default:entity789"]) is None

    def test_multiple_allowed_bots(self):
        """多个 allowed 项，返回匹配到的条目"""
        allowed = ["bot123", "bot456:entity789", "default:entity001"]
        assert match_allowed_bots("bot123:entity456", allowed) == "bot123"
        assert match_allowed_bots("bot456:entity999", allowed) == "bot456:entity789"
        assert match_allowed_bots("default:entity001", allowed) == "default:entity001"
        assert match_allowed_bots("bot789:entity999", allowed) is None

    def test_mixed_default_and_non_default(self):
        """混合 default 和非 default"""
        allowed = ["bot123", "default:entity456"]
        assert match_allowed_bots("bot123:any", allowed) == "bot123"
        assert match_allowed_bots("default:entity456", allowed) == "default:entity456"
        assert match_allowed_bots("default:entity789", allowed) is None
        assert match_allowed_bots("bot999:any", allowed) is None
